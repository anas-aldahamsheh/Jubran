"""Optional live provider check; run explicitly with AI_LIVE_TEST=1 (or GEMINI_LIVE_TEST=1)."""
import os

import pytest

from jubran.settings import settings


@pytest.mark.skipif("1" not in (os.getenv("AI_LIVE_TEST"), os.getenv("GEMINI_LIVE_TEST")), reason="External model check is opt-in")
def test_gemini_api_key_connectivity():
    genai = pytest.importorskip("google.genai")  # the file loads even where the SDK is not installed
    assert settings.GEMINI_API_KEY, "Set GEMINI_API_KEY for the live test"
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=settings.GEMINI_TEXT_MODEL, contents="Say hello in one sentence."
    )
    assert response.text
