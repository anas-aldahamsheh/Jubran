"""Live voice calls with GPT-Live: it listens and speaks, and every request it hands over goes through
the same chat assistant, guards and conversation memory as text chat. A call keeps going when the
phone's audio stalls or GPT-Live's session drops."""
import asyncio
import base64
import contextlib
import json

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from jubran.application.ai import live_voice
from jubran.application.ai.conversation_store import ConversationStore
from jubran.application.ai.live_voice import (
    NOT_HEARD, RESUMED, ClientFrame, GptLiveError, GptLiveProvider, LiveVoiceCall, LiveVoiceUnavailable,
)
from jubran.application.ai.provider_errors import classify_provider_failure
from jubran.application.session_service import SessionService
from jubran.infrastructure.db.models import AssistantConversationModel, OrderModel, ProductModel
from jubran.infrastructure.db.seed import seed_database
from jubran.infrastructure.db.session import Base
from helpers import issue_table_qr
from test_model_agent import model_call, model_response, script_model, summary_calls


class FakeLiveSession:
    """Plays GPT-Live events; a number in the script waits until that many results were said."""

    def __init__(self, script):
        self.script = script
        self.audio_in, self.said, self.instructions = [], [], []
        self.closed = False
        self.finished = asyncio.Event()
        self._changed = asyncio.Event()

    async def send_audio(self, pcm16):
        self.audio_in.append(pcm16)

    async def say(self, text, delegation_id):
        self.said.append((text, delegation_id))
        self._changed.set()

    async def instruct(self, text):
        self.instructions.append(text)

    async def close(self):
        self.closed = True

    async def events(self):
        for step in self.script:
            if isinstance(step, int):
                while len(self.said) < step:
                    self._changed.clear()
                    await asyncio.wait_for(self._changed.wait(), timeout=10)
                continue
            if step.get("type") == "session.closed":
                self.finished.set()   # GPT-Live ends this session here
            yield step
        self.finished.set()
        while not self.closed:  # like a real call: open until the guest hangs up
            await asyncio.sleep(0.01)


class FakeProvider:
    """GPT-Live sessions, one per connect (a dropped session is replaced by the next script)."""

    def __init__(self, script, fail=False, *more_scripts):
        self.sessions = [FakeLiveSession(script), *(FakeLiveSession(extra) for extra in more_scripts)]
        self.session = self.sessions[0]
        self.fail = fail
        self.instruction = None
        self.history = None
        self.histories = []
        self.opened = 0

    @contextlib.asynccontextmanager
    async def connect(self, instruction, history):
        if self.fail or self.opened >= len(self.sessions):
            raise ConnectionError("provider down")
        session = self.sessions[self.opened]
        self.opened += 1
        self.instruction, self.history = instruction, history
        self.histories.append([message["content"] for message in history])
        yield session


def heard(text, start_ms):
    return {"type": "session.input_transcript.delta", "delta": text, "start_ms": start_ms, "end_ms": start_ms + 300}


def spoke(text):
    return {"type": "session.output_transcript.delta", "delta": text, "start_ms": 0, "end_ms": 0}


def delegate(delegation_id, offset_ms):
    return {"type": "session.delegation.created", "offset_ms": offset_ms,
            "delegation": {"id": delegation_id, "type": "delegation", "target": "client"}}


def audio(pcm16):
    return {"type": "session.output_audio.delta", "delta": base64.b64encode(pcm16).decode("ascii")}


def closed(reason):
    return {"type": "session.closed", "reason": reason, "usage": {"seconds": 3}}


def guest_audio(session):
    """What reached GPT-Live from the guest, without the silence that bridges stalls."""
    return [chunk for chunk in session.audio_in if any(chunk)]


@pytest_asyncio.fixture
async def visit(tmp_path, monkeypatch):
    monkeypatch.setattr(live_voice, "TRANSCRIPT_GRACE_SECONDS", 0.01)
    monkeypatch.setattr(live_voice, "PROVIDER_RETRY_DELAYS", (0.01, 0.01, 0.01))
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'voice.db'}", connect_args={"timeout": 30})
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        await seed_database(db)
        _, customer, _ = await SessionService.start_or_resume_session(db, await issue_table_qr(db, "T6"))
        ids = {"customer": customer.id, "visit": customer.table_session_id}
        tea = (await db.execute(select(ProductModel).where(ProductModel.name_ar == "شاي"))).scalar_one()
        ids["tea"] = tea.id
    yield factory, ids
    await engine.dispose()


async def place_call(factory, ids, provider, guest_frames, language="ar", keep=None, resume=False):
    sent_json, sent_audio = [], []

    async def emit_json(payload):
        sent_json.append(payload)

    async def emit_audio(chunk):
        sent_audio.append(chunk)

    call = LiveVoiceCall(customer_session_id=ids["customer"], table_session_id=ids["visit"], table_number="T6",
                         language=language, provider=provider, session_factory=factory,
                         emit_json=emit_json, emit_audio=emit_audio, resume=resume)
    if keep is not None:
        keep.append(call)
    await asyncio.wait_for(call.run(guest_frames(provider.session)), timeout=20)
    return sent_json, sent_audio


def hang_up_when_done(*extra_frames):
    async def frames(session):
        yield ClientFrame(audio=b"\x01\x00" * 800)  # 50 ms of guest audio
        await asyncio.wait_for(session.finished.wait(), timeout=10)
        for frame in extra_frames:
            yield frame
        yield ClientFrame(end=True)
    return frames


@pytest.mark.asyncio
async def test_a_voice_order_goes_through_the_chat_assistant_and_its_rules(visit, monkeypatch):
    factory, ids = visit
    script_model(monkeypatch, [
        # Message 1: the guest orders tea; the chat assistant adds it and shows the summary...
        model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": ids["tea"], "quantity": 1}])),
        *summary_calls(),
        # ...and tries to send it in the same message: the server refuses.
        model_response(model_call("submit_order", confirmed_summary=True, guest_words="وخلص")),
        model_response(text="طلبك شاي واحد. بتأكد؟"),
    ], [
        # Message 2: the guest says yes; now it is sent.
        model_response(model_call("submit_order", confirmed_summary=True, guest_words="آه أكد")),
        model_response(text="تم إرسال طلبك."),
    ])
    provider = FakeProvider([
        heard("بدي شاي", 1000), heard(" وخلص", 1400), delegate("d1", 1800), 1,
        audio(b"\x02\x00" * 1200), spoke("طلبك شاي واحد، بتأكد؟"),
        heard("آه أكد", 5000), delegate("d2", 5600), 2,
    ])
    sent_json, sent_audio = await place_call(factory, ids, provider, hang_up_when_done())

    session = provider.session
    # GPT-Live says the assistant's replies, each for the request it handed over.
    assert session.said == [("طلبك شاي واحد. بتأكد؟", "d1"), ("تم إرسال طلبك.", "d2")]
    actions = [m["action"]["type"] for m in sent_json if m["type"] == "action"]
    assert actions == ["AWAITING_ORDER_CONFIRMATION", "ORDER_SUBMITTED"]
    assert sent_json[0]["type"] == "ready"
    assert guest_audio(session) == [b"\x01\x00" * 800] and sent_audio == [b"\x02\x00" * 1200]
    # The guest speaking again after the waiter starts a new exchange on the screen.
    kinds = [(m["type"], m.get("role")) for m in sent_json if m["type"] in ("transcript", "turn_complete")]
    assert kinds == [("transcript", "user"), ("transcript", "user"), ("transcript", "assistant"),
                     ("turn_complete", None), ("transcript", "user")]

    async with factory() as db:
        assert len((await db.execute(select(OrderModel))).scalars().all()) == 1
        state = await ConversationStore.load(db, ids["customer"])
        row = (await db.execute(select(AssistantConversationModel))).scalar_one()
    # The guest's words (joined as heard) and the replies are the visit's conversation, as in text chat.
    assert [m["content"] for m in state.history] == ["بدي شاي وخلص", "طلبك شاي واحد. بتأكد؟", "آه أكد", "تم إرسال طلبك."]
    assert row.busy_lease is None
    assert "Delegation policy" in provider.instruction and "Greet the guest" in session.instructions[0]


@pytest.mark.asyncio
async def test_the_confirm_button_works_during_a_call(visit, monkeypatch):
    factory, ids = visit
    script_model(monkeypatch, [
        model_response(model_call("search_knowledge", query="شاي", source_types=["product"])),
        model_response(model_call("update_draft_order", operations=[{"op": "add", "product_id": ids["tea"], "quantity": 2}])),
        *summary_calls(),
        model_response(text="ملخص طلبك: 2 شاي. بتأكد؟"),
    ], [model_response(text="تمام، طلبك وصل المطبخ.")])  # the chat model words the confirmation
    provider = FakeProvider([heard("بدي 2 شاي وخلص", 1000), delegate("d1", 1500), 1])

    async def frames(session):
        yield ClientFrame(audio=b"\x01\x00" * 800)
        await asyncio.wait_for(session.finished.wait(), timeout=10)
        yield ClientFrame(confirm_order=True)
        # The guest stays on the call to hear the answer.
        while len(session.said) < 2:
            await asyncio.sleep(0.02)
        yield ClientFrame(end=True)

    sent_json, _ = await place_call(factory, ids, provider, frames)

    submitted = [m for m in sent_json if m["type"] == "action" and m["action"]["type"] == "ORDER_SUBMITTED"]
    assert len(submitted) == 1
    # GPT-Live is given the confirmation to say out loud.
    assert provider.session.said[-1] == ("تمام، طلبك وصل المطبخ.", None)
    async with factory() as db:
        assert len((await db.execute(select(OrderModel))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_nothing_heard_asks_again_and_nothing_runs(visit, monkeypatch):
    factory, ids = visit
    chats = script_model(monkeypatch, [model_response(text="unused")])
    provider = FakeProvider([delegate("d1", 900), 1])
    await place_call(factory, ids, provider, hang_up_when_done())
    assert provider.session.said == [(NOT_HEARD["ar"], "d1")]
    assert chats[0].messages == []


@pytest.mark.asyncio
async def test_words_are_spaced_and_silence_is_not_streamed(visit, monkeypatch):
    factory, ids = visit
    chats = script_model(monkeypatch, [model_response(text="We close at 1 AM.")])
    silence, speech = b"\x00" * 4800, b"\x05\x00" * 2400  # 0.1 s each at 24 kHz
    provider = FakeProvider([
        audio(silence),                          # before the waiter says anything: not sent
        audio(speech), *[audio(silence)] * 6,    # after speech: a short natural pause only (0.4 s)
        heard("Hello", 3000), heard("What time", 4600), heard(" do you close?", 5200), delegate("d1", 6000), 1,
    ])
    sent_json, sent_audio = await place_call(factory, ids, provider, hang_up_when_done(), language="en")

    assert sent_audio == [speech] + [silence] * 4
    # Pieces after a real pause start a new word; pieces that carry their own space are kept as they are.
    assert [m["text"] for m in sent_json if m["type"] == "transcript"] == ["Hello", " What time", " do you close?"]
    assert chats[0].messages[0] == "Hello What time do you close?"
    assert provider.session.said == [("We close at 1 AM.", "d1")]


@pytest.mark.asyncio
async def test_a_word_cut_across_pieces_stays_one_word(visit, monkeypatch):
    factory, ids = visit
    chats = script_model(monkeypatch, [model_response(text="عنا حمص.")])
    # GPT-Live writes each 200 ms of speech as a piece: "بدي" came as "ب" + "دي", with a short pause inside.
    provider = FakeProvider([
        {"type": "session.input_transcript.delta", "delta": "ب", "start_ms": 1000, "end_ms": 1200},
        {"type": "session.input_transcript.delta", "delta": "دي", "start_ms": 1400, "end_ms": 1600},
        {"type": "session.input_transcript.delta", "delta": " حم", "start_ms": 1600, "end_ms": 1800},
        {"type": "session.input_transcript.delta", "delta": "ص؟", "start_ms": 2000, "end_ms": 2200},
        delegate("d1", 2400), 1,
    ])
    await place_call(factory, ids, provider, hang_up_when_done())
    assert chats[0].messages[0] == "بدي حمص؟"


@pytest.mark.asyncio
async def test_a_stalled_phone_is_bridged_with_silence_so_gpt_live_keeps_answering(visit, monkeypatch):
    factory, ids = visit
    monkeypatch.setattr(live_voice, "STALL_FILL_SECONDS", 0.05)
    provider = FakeProvider([])

    async def frames(session):
        yield ClientFrame(audio=b"\x01\x00" * 800)   # 50 ms, then the phone's audio stops coming
        await asyncio.sleep(0.6)
        yield ClientFrame(audio=b"\x02\x00" * 800)
        yield ClientFrame(end=True, reason="hang_up")

    calls = []
    await place_call(factory, ids, provider, frames, keep=calls)
    session = provider.session
    assert guest_audio(session) == [b"\x01\x00" * 800, b"\x02\x00" * 800]
    bridged = sum(len(chunk) for chunk in session.audio_in if not any(chunk))
    assert bridged >= 32000 * 3 // 10   # at least ~0.3 s of silence kept GPT-Live's clock going
    assert calls[0].guest_reason == "hang_up"


@pytest.mark.asyncio
async def test_a_dropped_gpt_live_session_is_replaced_and_the_call_goes_on(visit, monkeypatch):
    factory, ids = visit
    script_model(monkeypatch, [model_response(text="عنا حمص وفول.")])
    provider = FakeProvider(
        [heard("شو عندكم؟", 1000), delegate("d1", 1500), 1, closed("connection_lost")],
        False,
        [heard("تمام", 800)],   # the new session, with the conversation so far
    )
    sent_json, _ = await place_call(factory, ids, provider, hang_up_when_done_last(provider))

    first, second = provider.sessions
    assert first.said == [("عنا حمص وفول.", "d1")]
    assert [m["type"] for m in sent_json if m["type"] in ("ready", "reconnecting", "resumed")] == ["ready", "reconnecting", "resumed"]
    # The new session starts with the conversation so far and says it is back (no new greeting).
    assert provider.histories[1][-2:] == ["شو عندكم؟", "عنا حمص وفول."]
    assert second.instructions == [RESUMED.format(language="Arabic (Jordanian dialect)")]
    assert not any(m["type"] == "ending" for m in sent_json)


@pytest.mark.asyncio
async def test_a_reply_cut_off_by_a_drop_is_said_by_the_next_session(visit, monkeypatch):
    factory, ids = visit
    script_model(monkeypatch, [model_response(text="ضفتلك شاي.")])
    provider = FakeProvider(
        [heard("ضيف شاي", 1000), delegate("d1", 1400), closed("remote_hangup")],   # drops before the answer
        False,
        [],
    )
    await place_call(factory, ids, provider, hang_up_when_done_last(provider))
    second = provider.sessions[1]
    # Said in the new session for the whole session (the old delegation id belongs to the old one).
    assert ("ضفتلك شاي.", None) in second.said


@pytest.mark.asyncio
async def test_a_safety_stop_ends_the_call_with_a_reason(visit):
    factory, ids = visit
    provider = FakeProvider([closed("content")])
    sent_json, _ = await place_call(factory, ids, provider, hang_up_when_done_last(provider))
    assert sent_json[-1] == {"type": "ending", "reason": "PROVIDER_CONTENT"}
    assert provider.opened == 1


@pytest.mark.asyncio
async def test_gpt_live_that_keeps_dropping_ends_the_call_clearly(visit):
    factory, ids = visit
    provider = FakeProvider([closed("connection_lost")], False, [closed("connection_lost")])
    sent_json, _ = await place_call(factory, ids, provider, hang_up_when_done_last(provider))
    assert sent_json[-1] == {"type": "ending", "reason": "PROVIDER_UNAVAILABLE"}


@pytest.mark.asyncio
async def test_a_phone_that_reconnects_is_not_greeted_again(visit):
    factory, ids = visit
    provider = FakeProvider([])
    await place_call(factory, ids, provider, hang_up_when_done(), resume=True)
    assert provider.session.instructions == [RESUMED.format(language="Arabic (Jordanian dialect)")]


@pytest.mark.asyncio
async def test_the_phone_can_check_the_line(visit):
    factory, ids = visit
    provider = FakeProvider([])
    sent_json, _ = await place_call(factory, ids, provider, hang_up_when_done(ClientFrame(ping=True)))
    assert {"type": "pong"} in sent_json


@pytest.mark.asyncio
async def test_audio_pieces_are_kept_whole_samples(visit):
    factory, ids = visit
    provider = FakeProvider([audio(b"\x05\x00\x06"), audio(b"\x00\x07\x00")])
    _, sent_audio = await place_call(factory, ids, provider, hang_up_when_done())
    assert sent_audio == [b"\x05\x00", b"\x06\x00\x07\x00"]


@pytest.mark.asyncio
async def test_provider_failure_is_reported_and_leaves_the_conversation_free(visit):
    factory, ids = visit
    with pytest.raises(LiveVoiceUnavailable):
        await place_call(factory, ids, FakeProvider([], fail=True), hang_up_when_done())
    async with factory() as db:
        # Free: text chat or turn-by-turn voice can take over at once.
        assert await ConversationStore.acquire(db, ids["customer"], wait_seconds=0)


@pytest.mark.asyncio
async def test_earlier_chat_messages_carry_into_the_call(visit):
    factory, ids = visit
    async with factory() as db:
        state = await ConversationStore.load(db, ids["customer"])
        state.append("user", "شو عندكم فطور؟")
        state.append("assistant", "عنا حمص وفول وفتة.")
        await ConversationStore.save(db, state)
    provider = FakeProvider([])
    await place_call(factory, ids, provider, hang_up_when_done(), language="en")
    assert [m["content"] for m in provider.history] == ["شو عندكم فطور؟", "عنا حمص وفول وفتة."]
    assert "Until the guest speaks, use English" in provider.instruction
    assert "continuing a conversation" in provider.session.instructions[0]


def hang_up_when_done_last(provider):
    """The guest stays on until the last GPT-Live session the test prepared has played."""
    async def frames(_session):
        yield ClientFrame(audio=b"\x01\x00" * 800)
        for session in provider.sessions:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(session.finished.wait(), timeout=5)
        await asyncio.sleep(0.2)
        yield ClientFrame(end=True)
    return frames


class FakeSocket:
    """A GPT-Live WebSocket: records what we send and answers with the given events."""

    def __init__(self, incoming):
        self.incoming = list(incoming)
        self.sent = []

    async def send(self, data):
        self.sent.append(json.loads(data))

    async def recv(self):
        if not self.incoming:
            await asyncio.sleep(3600)
        return json.dumps(self.incoming.pop(0))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return json.dumps(self.incoming.pop(0))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def fake_connect(monkeypatch, incoming):
    seen = {}
    socket = FakeSocket(incoming)

    def connect(url, additional_headers=None, **kwargs):
        seen.update(url=url, headers=additional_headers)
        return socket

    monkeypatch.setattr("websockets.asyncio.client.connect", connect)
    return socket, seen


@pytest.mark.asyncio
async def test_the_gpt_live_session_is_started_and_fed_as_documented(monkeypatch):
    socket, seen = fake_connect(monkeypatch, [{"type": "session.started", "event_id": "e1", "session": {}}])
    history = [{"role": "user", "content": "مرحبا"}, {"role": "assistant", "content": "أهلاً!"}, {"role": "user", "content": ""}]
    async with GptLiveProvider("key-1", "gpt-live-1").connect("be brief", history) as live:
        await live.send_audio(b"\x10\x00" * 1600)  # 100 ms at 16 kHz
        await live.say("تم", "d1")

    assert seen == {"url": "wss://api.openai.com/v1/live/sessions", "headers": {"Authorization": "Bearer key-1"}}
    start = socket.sent[0]
    assert start["type"] == "session.start"
    session = start["session"]
    assert (session["model"], session["instructions"], session["store"]) == ("gpt-live-1", "be brief", False)
    assert session["delegation"] == {"type": "client"}
    assert session["audio"] == {"format": {"type": "audio/pcm", "rate": 24000}, "output": {"voice": "marin"}}
    assert session["input"] == [
        {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "مرحبا"}]},
        {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "أهلاً!"}]},
    ]
    appended = socket.sent[1]
    assert appended["type"] == "session.input_audio.append"
    assert abs(len(base64.b64decode(appended["audio"])) - 4800) <= 4  # the same 100 ms at 24 kHz
    assert socket.sent[2] == {"type": "session.commentary.append", "event_id": "result_1", "delegation_id": "d1",
                              "content": "تم"}
    assert socket.sent[-1]["type"] == "session.close"


@pytest.mark.asyncio
async def test_a_refused_session_explains_why(monkeypatch):
    fake_connect(monkeypatch, [{"type": "error", "event_id": "e1", "error": {
        "type": "invalid_request_error", "code": "model_not_found", "message": "The model does not exist."}}])
    with pytest.raises(GptLiveError) as failure:
        async with GptLiveProvider("key-1", "gpt-live-x").connect("be brief", []):
            pass
    assert failure.value.code == "model_not_found"
    assert classify_provider_failure(failure.value, "chat") == "AI_MODEL_NOT_FOUND"
