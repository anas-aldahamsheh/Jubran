"""16 kHz browser audio becomes 24 kHz for providers that only take 24 kHz: the right length, the
same sound, and no break between frames."""
import math
from array import array

from jubran.application.ai.pcm_resampler import PcmResampler


def tone(rate: int, seconds: float, hz: float = 440.0) -> array:
    return array("h", (int(12000 * math.sin(2 * math.pi * hz * n / rate)) for n in range(int(rate * seconds))))


def test_one_second_of_16k_becomes_one_second_of_24k():
    out = PcmResampler(16000, 24000).convert(tone(16000, 1.0).tobytes())
    assert abs(len(out) // 2 - 24000) <= 2


def test_frames_join_without_a_break():
    source = tone(16000, 0.5)
    whole = array("h")
    whole.frombytes(PcmResampler(16000, 24000).convert(source.tobytes()))
    pieces, resampler = array("h"), PcmResampler(16000, 24000)
    for start in range(0, len(source), 320):  # 20 ms frames, as the browser sends them
        pieces.frombytes(resampler.convert(source[start:start + 320].tobytes()))
    assert abs(len(pieces) - len(whole)) <= 2
    assert max(abs(a - b) for a, b in zip(pieces, whole)) <= 1


def test_the_sound_is_kept():
    out = array("h")
    out.frombytes(PcmResampler(16000, 24000).convert(tone(16000, 0.25).tobytes()))
    expected = tone(24000, 0.25)
    # Same 440 Hz wave at the new rate (linear interpolation: within a few percent of full scale).
    assert max(abs(a - b) for a, b in zip(out[10:5000], expected[10:5000])) < 600


def test_odd_or_empty_input_is_safe():
    resampler = PcmResampler(16000, 24000)
    assert resampler.convert(b"") == b""
    assert len(resampler.convert(b"\x01")) == 0
