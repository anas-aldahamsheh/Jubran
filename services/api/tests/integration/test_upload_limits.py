"""Uploads are size-capped before they are read, and audio must really be audio."""
import io

import pytest

from jubran.infrastructure.db.seed import seed_database
from jubran.interfaces.http.body_limits import DEFAULT_MAX_BODY_BYTES, MAX_AUDIO_UPLOAD_BYTES
from helpers import start_visit

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
MP4 = b"\x00\x00\x00\x20ftypM4A " + b"\x00" * 200
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 200


def audio(data: bytes, name: str = "clip.webm", declared: str = "audio/webm"):
    return {"audio": (name, io.BytesIO(data), declared)}


def error_code(response):
    return response.json()["detail"]["error"]["code"]


@pytest.mark.asyncio
@pytest.mark.parametrize("recording", [WEBM, MP4, WAV, b"OggS" + b"\x00" * 100])
async def test_real_recordings_are_accepted(client, db_session, recording):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    # Accepted as audio; it then stops only because no AI provider is set up in tests.
    response = await client.post("/api/v1/assistant/dictation", files=audio(recording, declared=""))
    assert response.status_code == 503 and error_code(response) == "AI_PROVIDER_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_a_file_that_only_claims_to_be_audio_is_refused(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    page = b"<html><script>alert(1)</script></html>"
    response = await client.post("/api/v1/assistant/dictation", files=audio(page, "voice.webm", "audio/webm"))
    assert response.status_code == 415 and error_code(response) == "UNSUPPORTED_AUDIO"


@pytest.mark.asyncio
async def test_oversized_audio_is_refused_before_reading(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    huge = WEBM + b"\x00" * (MAX_AUDIO_UPLOAD_BYTES + 100 * 1024)
    for path in ("/api/v1/assistant/dictation", "/api/v1/assistant/voice/audio-turn"):
        response = await client.post(path, files=audio(huge))
        assert response.status_code == 413 and error_code(response) == "PAYLOAD_TOO_LARGE", path


@pytest.mark.asyncio
async def test_a_body_streamed_without_its_size_is_cut_off_at_the_limit(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    sent = []

    async def endless_upload():
        yield (b'--x\r\nContent-Disposition: form-data; name="audio"; filename="a.webm"\r\n'
               b"Content-Type: audio/webm\r\n\r\n" + WEBM)
        chunk = b"\x00" * (256 * 1024)
        for _ in range(64):  # 16 MB offered, far over the limit
            sent.append(len(chunk))
            yield chunk

    response = await client.post("/api/v1/assistant/dictation", content=endless_upload(),
                                 headers={"content-type": "multipart/form-data; boundary=x"})
    assert response.status_code == 413 and error_code(response) == "PAYLOAD_TOO_LARGE"
    assert sum(sent) < 64 * 256 * 1024  # reading stopped early


@pytest.mark.asyncio
async def test_ordinary_requests_have_a_small_cap(client, db_session):
    await seed_database(db_session)
    await start_visit(client, db_session, "T3")
    response = await client.post("/api/v1/assistant/chat",
                                 json={"message": "x" * (DEFAULT_MAX_BODY_BYTES + 10)})
    assert response.status_code == 413 and error_code(response) == "PAYLOAD_TOO_LARGE"
