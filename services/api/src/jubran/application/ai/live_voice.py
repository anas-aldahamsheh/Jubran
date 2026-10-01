"""Live (streaming) voice calls with OpenAI GPT-Live (``gpt-live-1``).

GPT-Live listens and speaks with the guest. Whenever the guest asks for something it hands
the request to this application ("client delegation"), and the request goes through exactly
the same assistant as text chat (``AssistantService.chat``: the chat model, the tools, the
server's guards and the visit's conversation). The reply goes back for GPT-Live to say, and
summaries and sent orders reach the screen as in the text chat. The confirm button on a
summary is the text chat's button too. Provider keys stay on the server: the browser only
talks to our own WebSocket.

A call keeps going through rough moments: when the phone's audio stalls GPT-Live gets
silence meanwhile (its clock follows the audio it hears, so it would otherwise stop
answering), and when GPT-Live's session drops a new one starts with the conversation so far.

Audio on the wire: 16-bit little-endian mono PCM, 16 kHz from the browser (converted to
24 kHz for GPT-Live) and 24 kHz back to it. Audio is never stored or logged.
"""
import asyncio
import base64
import collections
import contextlib
import json
import logging
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, List, Optional, Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from jubran.application.ai.agent_service import AssistantService, interface_language
from jubran.application.ai.conversation_store import ConversationStore
from jubran.application.ai.model_config_service import ModelConfigService
from jubran.application.ai.pcm_resampler import PcmResampler
from jubran.application.ai.tools import AssistantToolExecutor
from jubran.domain.exceptions import DomainException

logger = logging.getLogger(__name__)

INPUT_SAMPLE_RATE = 16000              # what the browser sends
INPUT_BYTES_PER_SECOND = INPUT_SAMPLE_RATE * 2
LIVE_SAMPLE_RATE = 24000               # GPT-Live's audio both ways, and what the browser plays
MAX_AUDIO_FRAME_BYTES = 64 * 1024      # ~2 s of 16 kHz audio per frame is plenty
MAX_CALL_SECONDS = 15 * 60
LIVE_URL = "wss://api.openai.com/v1/live/sessions"
VOICE = "marin"
START_TIMEOUT_SECONDS = 15
HISTORY_MESSAGES = 30                  # earlier messages given at the start (GPT-Live takes 128 and 8,192 tokens)
HISTORY_CHARACTERS = 9000
REPLY_CHARACTERS = 1500                # a spoken result stays well under GPT-Live's 500 tokens per update
TRANSCRIPT_GRACE_SECONDS = 0.4         # the guest's last words can arrive just after GPT-Live asks for help
TRANSCRIPT_SLACK_MS = 250
WORD_GAP_MS = 800                      # a pause this long starts a new utterance (a space before it)
QUIET_TAIL_BYTES = LIVE_SAMPLE_RATE * 2 * 4 // 10   # silence kept after speech (0.4 s): natural pauses stay
SENTENCE_ENDS = (".", "?", "!", "؟", "،", ",", "…")
FINISH_REQUEST_SECONDS = 60            # a request already running when the guest hangs up still completes
STALL_FILL_SECONDS = 0.25              # the phone's audio this late: GPT-Live hears silence meanwhile
MIN_FILL_BYTES = INPUT_BYTES_PER_SECOND // 50            # 20 ms
MAX_FILL_BYTES = INPUT_BYTES_PER_SECOND * 5              # at most 5 s of silence at once
BACKLOG_SECONDS = 4                    # the guest's latest audio kept while GPT-Live reconnects
PROVIDER_RETRY_DELAYS = (0.5, 1.5, 3.0)   # a dropped GPT-Live session is opened again after these waits

LANGUAGE_NAMES = {"ar": "Arabic (Jordanian dialect)", "en": "English"}

LIVE_INSTRUCTIONS = """You are the voice of the waiter at Jubran, a traditional Jordanian restaurant (hummus, falafel, foul and more). You are talking with a guest at their table through their phone.
Speak warmly and naturally, and briefly: one or two short sentences, no lists, and never read symbols or markup aloud. Do not assume the guest's gender.
Language: answer in the language the guest speaks; with Arabic speakers use natural Jordanian Arabic. Until the guest speaks, use {language}.

Backchannel policy: Do not use backchannels.

Interruption policy: Stop speaking when the guest interrupts. Listen to what they say.

Delegation policy:
Backend tools:
- The restaurant's assistant: the menu, dishes, prices and availability; the guest's basket (add, change, remove); order summaries and sending orders; changes to orders already sent; order status; calling staff, napkins, table cleaning or the bill; complaints and ratings; opening hours and branches.
Delegate to the backend when:
- The guest asks about the menu, a dish, prices, the restaurant, their basket or their orders, or asks for a service or for anything else at their table (salt, a spoon, the air conditioning...).
- The guest wants to add, change, remove, confirm or send something, or answers a question the backend asked (yes or no, a quantity, a choice).
- A correction changes something already requested.
Do not delegate to the backend when:
- The guest only greets you or thanks you: answer briefly yourself.
- You need a very short clarification before passing the request on.
Delegate before giving an answer that depends on the backend. Do not guess the result while waiting.

Never invent dishes, prices, availability, order numbers or results, and never say that something was added, sent or requested unless the backend's result says so. Say the backend's results naturally and briefly, keeping every price, quantity and order number exactly as given. When the backend asks the guest something, ask it and wait for the answer. The guest can also confirm an order with the button on the screen."""

GREETING = {
    False: "Greet the guest now, in {language}, in one short sentence as the Jubran waiter, and ask what they would like. Then pause and listen.",
    True: "The guest is continuing a conversation from the chat. Say now, in {language}, in a few words that you are listening. Then pause and listen.",
}
# After GPT-Live's session dropped and a new one took over (the conversation so far is in its input).
RESUMED = "The line dropped for a moment and is back now. Do not greet again. Say now, in {language}, in two or three words, that you are back and listening. Then pause and listen."

# Content for GPT-Live to say (it words it in its own way).
NOT_HEARD = {"ar": "ما سمعت منيح، ممكن تعيد؟", "en": "Sorry, I didn't catch that. Could you say it again?"}
TROUBLE = {"ar": "صار عندي عطل صغير، ممكن تعيد طلبك؟", "en": "Something went wrong on my side. Could you say that again?"}
NOT_SENT = {"ar": "ما انبعت الطلب. شوف الملخص على الشاشة وأكّد من جديد.",
            "en": "The order wasn't sent. Please check the summary on the screen and confirm again."}


class LiveVoiceUnavailable(Exception):
    """The live voice provider cannot be used at the start of a call; the web app goes on turn by turn."""


class GptLiveError(Exception):
    """GPT-Live refused to start the session (a wrong key or model, no access, a limit)."""

    def __init__(self, code: Optional[str], message: Optional[str]):
        self.code = code or "live_error"
        super().__init__(f"{self.code.replace('_', ' ')}: {message or ''}")


class LiveVoiceSession(Protocol):
    async def send_audio(self, pcm16: bytes) -> None: ...
    async def say(self, text: str, delegation_id: Optional[str]) -> None: ...
    async def instruct(self, text: str) -> None: ...
    async def close(self) -> None: ...
    def events(self) -> AsyncIterator[Dict[str, Any]]: ...


class LiveVoiceProvider(Protocol):
    def connect(self, instruction: str, history: List[Dict[str, str]]) -> "contextlib.AbstractAsyncContextManager[LiveVoiceSession]": ...


def history_items(history: List[Dict[str, str]]) -> List[Dict[str, Any]]:
    """The visit's latest messages as GPT-Live startup input (text only, within its limits)."""
    kept = [message for message in history if message.get("content")][-HISTORY_MESSAGES:]
    while kept and sum(len(message["content"]) for message in kept) > HISTORY_CHARACTERS:
        kept.pop(0)
    return [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": message["content"]}]}
            if message.get("role") == "assistant" else
            {"type": "message", "role": "user", "content": [{"type": "input_text", "text": message["content"]}]}
            for message in kept]


# ---------------------------------------------------------------------------
# GPT-Live adapter (OpenAI Live API over a server-side WebSocket)
# ---------------------------------------------------------------------------

class _GptLiveSession:
    def __init__(self, socket):
        self._socket = socket
        self._send_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()   # converting and sending a piece stay together, in order
        self._resampler = PcmResampler(INPUT_SAMPLE_RATE, LIVE_SAMPLE_RATE)
        self._sent = 0
        self._closed = False

    async def _send(self, event: Dict[str, Any]) -> None:
        async with self._send_lock:
            await self._socket.send(json.dumps(event, ensure_ascii=False))

    def _event_id(self, prefix: str) -> str:
        self._sent += 1
        return f"{prefix}_{self._sent}"

    async def send_audio(self, pcm16: bytes) -> None:
        async with self._audio_lock:
            audio = self._resampler.convert(pcm16)
            if audio:
                await self._send({"type": "session.input_audio.append", "audio": base64.b64encode(audio).decode("ascii")})

    async def say(self, text: str, delegation_id: Optional[str]) -> None:
        await self._send({"type": "session.commentary.append", "event_id": self._event_id("result"),
                          "delegation_id": delegation_id, "content": text})

    async def instruct(self, text: str) -> None:
        await self._send({"type": "session.instructions.append", "event_id": self._event_id("instruction"),
                          "delegation_id": None, "content": text})

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        with contextlib.suppress(Exception):
            await self._send({"type": "session.close", "event_id": "close"})

    async def events(self) -> AsyncIterator[Dict[str, Any]]:
        async for raw in self._socket:
            if isinstance(raw, bytes):
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if isinstance(event, dict):
                yield event


async def _wait_until_started(socket) -> None:
    """Audio may only be sent once GPT-Live confirms the session; a refusal says why."""
    async def started() -> None:
        while True:
            raw = await socket.recv()
            event = json.loads(raw) if isinstance(raw, str) else {}
            if event.get("type") == "session.started":
                return
            if event.get("type") == "error":
                error = event.get("error") or {}
                raise GptLiveError(error.get("code"), error.get("message"))
    await asyncio.wait_for(started(), timeout=START_TIMEOUT_SECONDS)


class GptLiveProvider:
    """``LiveVoiceProvider`` backed by OpenAI's Live API (GPT-Live), with client delegation."""

    def __init__(self, api_key: str, model: str, url: str = LIVE_URL):
        self._api_key = api_key
        self._model = model
        self._url = url

    @contextlib.asynccontextmanager
    async def connect(self, instruction: str, history: List[Dict[str, str]]):
        from websockets.asyncio.client import connect

        async with connect(self._url, additional_headers={"Authorization": f"Bearer {self._api_key}"},
                           open_timeout=START_TIMEOUT_SECONDS, max_size=16 * 1024 * 1024) as socket:
            await socket.send(json.dumps({"type": "session.start", "event_id": "start", "session": {
                "model": self._model,
                "instructions": instruction,
                "input": history_items(history),
                "audio": {"format": {"type": "audio/pcm", "rate": LIVE_SAMPLE_RATE}, "output": {"voice": VOICE}},
                "delegation": {"type": "client"},
                "store": False,
            }}, ensure_ascii=False))
            await _wait_until_started(socket)
            session = _GptLiveSession(socket)
            try:
                yield session
            finally:
                await session.close()


async def live_voice_provider(db: AsyncSession) -> LiveVoiceProvider:
    """Live voice when the administrator switched it on in its settings section; otherwise
    LiveVoiceUnavailable, and voice works turn by turn (speech to text, then the chat model)."""
    try:
        config = await ModelConfigService.runtime(db, "voice")
    except ValueError as exc:
        raise LiveVoiceUnavailable(str(exc)) from exc
    return GptLiveProvider(config.api_key, config.model_id)


# ---------------------------------------------------------------------------
# One call: browser <-> our server <-> GPT-Live
# ---------------------------------------------------------------------------

@dataclass
class ClientFrame:
    """What the browser sent: raw audio, typed text, a confirm-button press, a keep-alive ping,
    or hang-up (with why the call ended on the phone: the guest, quiet, left the page...)."""
    audio: bytes = b""
    text: str = ""
    confirm_order: bool = False
    ping: bool = False
    end: bool = False
    reason: str = ""


@dataclass
class _Request:
    """Work for the assistant: what GPT-Live asked for help with, typed text, or the confirm button."""
    kind: str                          # "voice", "text" or "confirm"
    delegation_id: Optional[str] = None
    offset_ms: int = 0
    text: str = ""
    session: int = 0                   # the GPT-Live session that asked (its delegation ids are its own)


EmitJson = Callable[[Dict[str, Any]], Awaitable[None]]
EmitAudio = Callable[[bytes], Awaitable[None]]


class _AudioBacklog:
    """The guest's audio on its way to GPT-Live: it arrives in bursts and leaves in order.
    While GPT-Live reconnects only the latest few seconds are kept (what the guest just said)."""

    def __init__(self, limit_bytes: int):
        self._chunks: "collections.deque[bytes]" = collections.deque()
        self._size = 0
        self._limit = limit_bytes
        self._ready = asyncio.Event()

    def put(self, chunk: bytes) -> None:
        self._chunks.append(chunk)
        self._size += len(chunk)
        while self._size > self._limit and len(self._chunks) > 1:
            self._size -= len(self._chunks.popleft())
        self._ready.set()

    def take_all(self) -> bytes:
        data = b"".join(self._chunks)
        self._chunks.clear()
        self._size = 0
        self._ready.clear()
        return data

    async def wait(self, timeout: float) -> None:
        """Until audio is waiting or `timeout` passed. (Not asyncio.wait_for: on Python 3.10 it can
        swallow a cancellation that arrives just as the audio does, and the call would never stop.)"""
        if self._ready.is_set():
            return
        waiter = asyncio.ensure_future(self._ready.wait())
        try:
            await asyncio.wait({waiter}, timeout=timeout)
        finally:
            waiter.cancel()


class LiveVoiceCall:
    """One live call. The guest's side (the browser socket) lasts the whole call; GPT-Live's side
    is a session that is opened again by itself when it drops, with the conversation so far."""

    def __init__(self, *, customer_session_id: str, table_session_id: str, table_number: str,
                 language: Optional[str], provider: LiveVoiceProvider,
                 session_factory: Callable[[], AsyncSession], emit_json: EmitJson, emit_audio: EmitAudio,
                 resume: bool = False):
        self.customer_session_id = customer_session_id
        self.resume = resume                   # the phone reconnects to a call whose line dropped
        self.table_session_id = table_session_id
        self.table_number = table_number
        self.language = interface_language(language)
        self.provider = provider
        self.session_factory = session_factory
        self.emit_json = emit_json
        self.emit_audio = emit_audio
        self._tag = customer_session_id[:8]
        self._requests: "asyncio.Queue[Optional[_Request]]" = asyncio.Queue()
        self._audio = _AudioBacklog(int(BACKLOG_SECONDS * INPUT_BYTES_PER_SECOND))
        self._guest_done = asyncio.Event()
        self.guest_reason = ""                 # why the phone ended the call ("" = still on)
        self._live: Optional[LiveVoiceSession] = None
        self._session = 0                      # the current GPT-Live session's number (0: none open)
        self._sessions_opened = 0
        self._undelivered: Optional[str] = None   # a reply GPT-Live could not say (its session dropped)
        self._stats = {"sessions": 0, "requests": 0, "voice_seconds": 0.0, "session_seconds": 0.0, "filled_ms": 0}
        # Per GPT-Live session (each has its own timeline): (start_ms, text) of the guest's words not
        # yet passed on. A dropped session's words stay for its requests still waiting.
        self._heard: Dict[int, List[tuple]] = {}
        self._begin_session(None)

    def _begin_session(self, live: Optional[LiveVoiceSession]) -> None:
        """What belongs to one GPT-Live session starts fresh (its timeline starts at zero)."""
        self._live = live
        if live is not None:
            self._sessions_opened += 1
        self._session = self._sessions_opened if live is not None else 0
        self._waiter_spoke = False             # the waiter spoke since the guest's last words
        self._last_piece: Dict[str, tuple] = {}   # per speaker: (end_ms, text) of the latest piece of speech
        self._quiet_bytes: Optional[int] = None   # silence sent since the waiter's last sound (None: none yet)
        self._odd_byte = b""                   # half a sample left over from the last audio piece

    async def _history(self) -> List[Dict[str, str]]:
        async with self.session_factory() as db:
            state = await ConversationStore.load(db, self.customer_session_id)
        return [dict(message) for message in state.history]

    async def run(self, frames: AsyncIterator[ClientFrame]) -> None:
        loop = asyncio.get_running_loop()
        began = loop.time()
        history = await self._history()
        async with self.session_factory() as db:
            draft = await AssistantToolExecutor(db, self.customer_session_id, self.table_session_id,
                                                self.table_number).execute("get_current_draft", {})
        logger.info("live call %s: started (%s)", self._tag, self.language)
        reader = asyncio.create_task(self._read_guest(frames))
        worker = asyncio.create_task(self._serve())
        outcome = "GUEST_LEFT"
        try:
            outcome = await asyncio.wait_for(self._sessions(history, draft), timeout=MAX_CALL_SECONDS)
        except asyncio.TimeoutError:
            outcome = "CALL_TIME_LIMIT"
        finally:
            reader.cancel()
            # A request already running (e.g. adding to the basket) completes, so the conversation
            # and the basket always agree; nothing new starts.
            self._requests.put_nowait(None)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(asyncio.shield(worker), timeout=FINISH_REQUEST_SECONDS)
            worker.cancel()
            await asyncio.gather(reader, worker, return_exceptions=True)
            logger.info("live call %s: ended (%s%s) after %.0fs; %d GPT-Live session(s), %d request(s), "
                        "%.0fs of voice, %d ms of stalled phone audio bridged",
                        self._tag, outcome, f": {self.guest_reason}" if self.guest_reason else "",
                        loop.time() - began, self._stats["sessions"], self._stats["requests"],
                        self._stats["voice_seconds"], self._stats["filled_ms"])
        if outcome != "GUEST_LEFT":
            # Why the call ended, for the screen (a time limit, or GPT-Live could not go on).
            with contextlib.suppress(Exception):
                await self.emit_json({"type": "ending", "reason": outcome})

    async def _sessions(self, history: List[Dict[str, str]], draft: Dict[str, Any]) -> str:
        """GPT-Live sessions, one after another, until the call ends. A session that drops is
        replaced (the guest hears a short "I'm back"); only a first one that cannot start at all
        turns the call down (LiveVoiceUnavailable: the phone goes on step by step instead)."""
        loop = asyncio.get_running_loop()
        language = LANGUAGE_NAMES[self.language]
        instructions = LIVE_INSTRUCTIONS.format(language=language)
        retries = 0
        started_once = False
        while not self._guest_done.is_set():
            opened = loop.time()
            outcome = "PROVIDER_CONNECT_FAILED"
            try:
                async with self.provider.connect(instructions, history) as live:
                    self._begin_session(live)
                    self._stats["sessions"] += 1
                    logger.info("live call %s: GPT-Live session %d open in %.1fs", self._tag,
                                self._stats["sessions"], loop.time() - opened)
                    if not started_once:
                        started_once = True
                        await self.emit_json({"type": "ready", "draft": draft if draft.get("items") else None})
                        opening = RESUMED if self.resume else GREETING[bool(history)]
                        with contextlib.suppress(Exception):
                            await live.instruct(opening.format(language=language))
                    else:
                        await self.emit_json({"type": "resumed"})
                        reply, self._undelivered = self._undelivered, None
                        with contextlib.suppress(Exception):
                            if reply:
                                await live.say(reply, None)   # what it was about to say when it dropped
                            else:
                                await live.instruct(RESUMED.format(language=language))
                    retries = 0
                    outcome = await self._pump(live, self._session)
            except Exception as exc:
                if not started_once:
                    logger.warning("Live voice provider unavailable: %s %s", type(exc).__name__,
                                   getattr(exc, "code", "") if isinstance(exc, GptLiveError) else "")
                    raise LiveVoiceUnavailable() from exc
                logger.warning("live call %s: GPT-Live session problem: %s", self._tag, type(exc).__name__)
            finally:
                self._stats["session_seconds"] += loop.time() - opened
                self._begin_session(None)
            if outcome == "GUEST_LEFT" or self._guest_done.is_set():
                return "GUEST_LEFT"
            logger.info("live call %s: GPT-Live session ended (%s) after %.0fs", self._tag, outcome,
                        loop.time() - opened)
            if outcome == "content":
                return "PROVIDER_CONTENT"   # a safety stop: not started again
            if retries >= len(PROVIDER_RETRY_DELAYS):
                return "PROVIDER_UNAVAILABLE"
            await self.emit_json({"type": "reconnecting"})
            await asyncio.sleep(PROVIDER_RETRY_DELAYS[retries])
            retries += 1
            logger.info("live call %s: opening GPT-Live again (attempt %d)", self._tag, retries)
            with contextlib.suppress(Exception):
                history = await self._history()   # the conversation so far, for the new session
        return "GUEST_LEFT"

    async def _pump(self, live: LiveVoiceSession, number: int) -> str:
        """Guest -> GPT-Live and GPT-Live -> guest for one session. Returns why it stopped:
        GUEST_LEFT, or what ended GPT-Live's side (its close reason, or a lost connection)."""
        sender = asyncio.create_task(self._send_audio(live))
        listener = asyncio.create_task(self._from_live(live, number))
        guest = asyncio.create_task(self._guest_done.wait())
        try:
            done, _ = await asyncio.wait({sender, listener, guest}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (sender, listener, guest):
                task.cancel()
            await asyncio.gather(sender, listener, guest, return_exceptions=True)
        if guest in done or self._guest_done.is_set():
            with contextlib.suppress(Exception):
                await live.close()
            return "GUEST_LEFT"
        if listener in done and not listener.cancelled() and listener.exception() is None:
            return listener.result()
        return "connection_lost"

    async def _read_guest(self, frames: AsyncIterator[ClientFrame]) -> None:
        """The browser's side, for the whole call (it outlives any one GPT-Live session)."""
        try:
            async for frame in frames:
                if frame.end:
                    self.guest_reason = frame.reason or "hang_up"
                    return
                if frame.audio:
                    self._audio.put(frame.audio)
                elif frame.ping:
                    await self.emit_json({"type": "pong"})
                elif frame.text:
                    await self._requests.put(_Request("text", text=frame.text))
                elif frame.confirm_order:
                    await self._requests.put(_Request("confirm"))
            self.guest_reason = self.guest_reason or "disconnected"
        except asyncio.CancelledError:
            raise
        except Exception:
            self.guest_reason = self.guest_reason or "disconnected"
        finally:
            self._guest_done.set()

    async def _send_audio(self, live: LiveVoiceSession) -> None:
        """The guest's audio to GPT-Live as it arrives. GPT-Live's clock follows the audio it hears,
        so when the phone's audio stops coming (a bad moment on the network, a locked screen) it
        gets silence meanwhile and keeps listening and answering instead of waiting."""
        loop = asyncio.get_running_loop()
        start = loop.time()
        sent = 0
        while True:
            await self._audio.wait(0.1)
            data = self._audio.take_all()
            if data:
                await live.send_audio(data)
                sent += len(data)
            behind = int((loop.time() - start - STALL_FILL_SECONDS) * INPUT_BYTES_PER_SECOND) - sent
            if behind >= MIN_FILL_BYTES:
                behind = min(behind - behind % 2, MAX_FILL_BYTES)
                await live.send_audio(bytes(behind))
                sent += behind
                self._stats["filled_ms"] += behind * 1000 // INPUT_BYTES_PER_SECOND

    def _spaced(self, speaker: str, event: Dict[str, Any]) -> str:
        """A piece of speech, with a space before it where GPT-Live left none between two words.
        Its pieces carry their own spaces between words, and a word can be cut across two pieces
        (even with a short pause inside it), so a space is only added after a sentence ends or
        after a real pause (a new utterance)."""
        text, start = event["delta"], int(event.get("start_ms") or 0)
        previous = self._last_piece.get(speaker)
        self._last_piece[speaker] = (int(event.get("end_ms") or start), text)
        if previous and previous[1] and not previous[1][-1].isspace() and not text[0].isspace() \
                and text[0] not in SENTENCE_ENDS \
                and (start - previous[0] >= WORD_GAP_MS or previous[1].endswith(SENTENCE_ENDS)):
            return " " + text
        return text

    async def _play(self, chunk: bytes) -> None:
        """The waiter's audio. GPT-Live streams silence too; beyond a short pause after speech it
        is dropped, so the screen shows who is talking and the phone plays nothing needlessly.
        Pieces are kept whole samples (a stray half sample would turn the rest into noise)."""
        if self._odd_byte:
            chunk, self._odd_byte = self._odd_byte + chunk, b""
        if len(chunk) % 2:
            chunk, self._odd_byte = chunk[:-1], chunk[-1:]
        if not chunk:
            return
        if chunk.count(0) == len(chunk):
            if self._quiet_bytes is None or self._quiet_bytes >= QUIET_TAIL_BYTES:
                return
            self._quiet_bytes += len(chunk)
        else:
            self._quiet_bytes = 0
        await self.emit_audio(chunk)

    async def _from_live(self, live: LiveVoiceSession, number: int) -> str:
        """GPT-Live's events for one session; returns its close reason when it ends."""
        voice_seconds = 0.0
        try:
            async for event in live.events():
                kind = event.get("type")
                if kind == "session.output_audio.delta":
                    with contextlib.suppress(ValueError, TypeError):
                        await self._play(base64.b64decode(event.get("delta") or ""))
                elif kind == "session.output_transcript.delta" and event.get("delta"):
                    self._waiter_spoke = True
                    await self.emit_json({"type": "transcript", "role": "assistant", "text": self._spaced("waiter", event)})
                elif kind == "session.input_transcript.delta" and event.get("delta"):
                    if self._waiter_spoke:
                        # The guest speaks again after the waiter: a new exchange on the screen.
                        self._waiter_spoke = False
                        await self.emit_json({"type": "turn_complete"})
                    text = self._spaced("guest", event)
                    self._heard.setdefault(number, []).append((int(event.get("start_ms") or 0), text))
                    await self.emit_json({"type": "transcript", "role": "user", "text": text})
                elif kind == "session.delegation.created":
                    delegation = event.get("delegation") or {}
                    if delegation.get("target", "client") == "client":
                        await self._requests.put(_Request("voice", delegation_id=delegation.get("id"),
                                                          offset_ms=int(event.get("offset_ms") or 0),
                                                          session=number))
                elif kind == "session.usage.updated":
                    with contextlib.suppress(TypeError, ValueError):
                        voice_seconds = float((event.get("usage") or {}).get("seconds") or voice_seconds)
                elif kind == "session.closed":
                    with contextlib.suppress(TypeError, ValueError):
                        voice_seconds = float((event.get("usage") or {}).get("seconds") or voice_seconds)
                    return str(event.get("reason") or "closed")
                elif kind == "error":
                    error = event.get("error") or {}
                    logger.warning("live call %s: GPT-Live error %s: %s", self._tag, error.get("code"),
                                   str(error.get("message") or "")[:200])
            return "connection_lost"
        finally:
            self._stats["voice_seconds"] += voice_seconds

    async def _serve(self) -> None:
        """One request at a time, in order, like messages in the text chat."""
        while (request := await self._requests.get()) is not None:
            self._stats["requests"] += 1
            try:
                if request.kind == "confirm":
                    await self._confirm_with_button()
                else:
                    await self._answer(request)
            except Exception:
                logger.exception("A live voice request failed")
                await self._say(TROUBLE[self.language], request)

    async def _say(self, text: str, request: Optional[_Request]) -> None:
        """Content for GPT-Live to say now. A delegation id only belongs to the session that
        made it; when there is no session (it dropped), the next session says it instead."""
        live = self._live
        if live is None:
            self._undelivered = text
            return
        delegation = request.delegation_id if request is not None and request.session == self._session else None
        try:
            await live.say(text, delegation)
        except Exception:
            self._undelivered = text

    def _take_words(self, session: int, offset_ms: int) -> str:
        """The guest's words up to the moment GPT-Live asked for help (else any still unanswered)."""
        heard = self._heard.get(session, [])
        cutoff = offset_ms + TRANSCRIPT_SLACK_MS
        now = [fragment for fragment in heard if fragment[0] <= cutoff]
        later = [fragment for fragment in heard if fragment[0] > cutoff]
        if not now:
            now, later = later, []
        self._heard[session] = later
        return "".join(text for _, text in now).strip()

    async def _answer(self, request: _Request) -> None:
        if request.kind == "voice":
            await asyncio.sleep(TRANSCRIPT_GRACE_SECONDS)
            words = self._take_words(request.session, request.offset_ms)
        else:
            words = request.text
        if not words:
            await self._say(NOT_HEARD[self.language], request)
            return
        try:
            async with self.session_factory() as db:
                result = await AssistantService.chat(db, self.customer_session_id, self.table_session_id,
                                                     self.table_number, words, self.language)
        except DomainException:  # e.g. ASSISTANT_BUSY: a text reply is still being written
            await self._say(TROUBLE[self.language], request)
            return
        if result.get("action"):
            await self.emit_json({"type": "action", "action": result["action"]})
        await self._send_draft(result.get("draft"))
        reply = (result.get("response") or "").strip()
        if reply:
            await self._say(reply[:REPLY_CHARACTERS], request)

    async def _confirm_with_button(self) -> None:
        """The summary's confirm button during the call: the text chat's confirmation, then GPT-Live says it."""
        async with self.session_factory() as db:
            result = await AssistantService.confirm_pending_order(
                db, self.customer_session_id, self.table_session_id, self.table_number, self.language)
        if result.get("action"):
            await self.emit_json({"type": "action", "action": result["action"]})
        if not result.get("success"):
            await self.emit_json({"type": "confirm_failed", "error_code": result.get("error_code")})
        await self._send_draft(result.get("draft"))
        await self._say((result.get("response") or "").strip() or NOT_SENT[self.language], None)

    async def _send_draft(self, draft: Optional[Dict[str, Any]]) -> None:
        await self.emit_json({"type": "draft", "draft": draft if draft and draft.get("items") else None})
