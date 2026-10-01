"""Integration tests for Dictation Speech-to-Text (REQ-017)."""
import io
import pytest
from jubran.infrastructure.db.seed import seed_database
from helpers import start_visit


@pytest.mark.asyncio
async def test_dictation_flow_and_guards(client, db_session):
    await seed_database(db_session)

    # 1. Start QR Table Session (the visit lives in this browser's cookie)
    await start_visit(client, db_session, "T6")

    # The old byte-pattern mock is gone; unavailable model cannot invent speech.
    audio_file = io.BytesIO(b"\x1a\x45\xdf\xa3" + b"webm audio sample with hummus request")
    files = {"audio": ("sample.webm", audio_file, "audio/webm")}
    res = await client.post("/api/v1/assistant/dictation", files=files)
    assert res.status_code == 503
    assert res.json()["detail"]["error"]["code"] == "AI_PROVIDER_NOT_CONFIGURED"

    # 4. Guard: Empty audio rejected (400)
    empty_file = io.BytesIO(b"")
    files_empty = {"audio": ("empty.webm", empty_file, "audio/webm")}
    empty_res = await client.post("/api/v1/assistant/dictation", files=files_empty)
    assert empty_res.status_code == 400

    # 5. Guard: Too large audio (> 5MB) rejected with 413 before it is read
    huge_bytes = b"x" * (5 * 1024 * 1024 + 100)
    huge_file = io.BytesIO(huge_bytes)
    files_huge = {"audio": ("huge.webm", huge_file, "audio/webm")}
    huge_res = await client.post("/api/v1/assistant/dictation", files=files_huge)
    assert huge_res.status_code == 413

    # 6. Guard: Missing customer session rejected (401)
    client.cookies.clear()
    unauth_res = await client.post("/api/v1/assistant/dictation", files=files)
    assert unauth_res.status_code == 401
