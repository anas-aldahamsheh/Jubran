"""Streaming sample-rate conversion for 16-bit little-endian mono PCM (no native dependencies).

The browser sends 16 kHz audio; some voice providers only take 24 kHz. Frames arrive one by
one, so the converter keeps its position and the last sample between frames: the output is
continuous, with no click at frame edges.
"""
from array import array
import sys


class PcmResampler:
    """Linear-interpolation resampler, ``source_rate`` to ``target_rate`` (e.g. 16000 to 24000)."""

    def __init__(self, source_rate: int, target_rate: int):
        if source_rate <= 0 or target_rate <= 0:
            raise ValueError("Sample rates must be positive")
        self._step = source_rate / target_rate  # input samples per output sample
        self._position = 0.0                    # next output position, in input samples from `_previous`
        self._previous: int | None = None       # the last sample of the previous frame

    def convert(self, pcm16: bytes) -> bytes:
        samples = array("h")
        samples.frombytes(pcm16[: len(pcm16) - len(pcm16) % 2])
        if sys.byteorder != "little":
            samples.byteswap()
        if not samples:
            return b""
        # After the first frame, index 0 is the previous frame's last sample.
        if self._previous is None:
            source = samples
        else:
            source = array("h", [self._previous])
            source.extend(samples)
        out = array("h")
        position, step, last = self._position, self._step, len(source) - 1
        while position < last:
            index = int(position)
            fraction = position - index
            a, b = source[index], source[index + 1]
            out.append(int(round(a + (b - a) * fraction)))
            position += step
        self._position = position - last
        self._previous = source[last]
        if sys.byteorder != "little":
            out.byteswap()
        return out.tobytes()
