"""Reading the one audio format Pensum accepts.

The browser sends 16 kHz mono 16-bit PCM in a WAV container, because that is
what the recogniser wants and converting it in the page means the server needs
no ffmpeg, no codec and no temporary file. Anything else is refused rather than
converted: a decoder that accepts arbitrary container formats is a much larger
attack surface than a reading exercise justifies.

Nothing is written to disk. The bytes arrive in the request body, are parsed
here into memory, and are dropped when the response is sent.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass

SAMPLE_RATE = 16_000
CHANNELS = 1
SAMPLE_WIDTH = 2  # bytes, i.e. 16-bit signed PCM

# 16 kHz * 2 bytes * 300 s, plus a header's worth of slack. Five minutes is far
# longer than any passage here, and the cap is what stops a request body from
# being an unbounded allocation.
MAX_BYTES = SAMPLE_RATE * SAMPLE_WIDTH * 300 + 1024


class AudioError(ValueError):
    """The upload was not a recording we can read."""


@dataclass(frozen=True)
class Recording:
    """Decoded PCM and how long it runs."""

    pcm: bytes
    seconds: float


def decode(data: bytes) -> Recording:
    """Parse a WAV upload, or raise `AudioError` saying why not."""
    if not data:
        raise AudioError("empty recording")
    if len(data) > MAX_BYTES:
        raise AudioError("recording too long")

    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            if handle.getnchannels() != CHANNELS:
                raise AudioError("recording must be mono")
            if handle.getsampwidth() != SAMPLE_WIDTH:
                raise AudioError("recording must be 16-bit")
            if handle.getframerate() != SAMPLE_RATE:
                raise AudioError(f"recording must be {SAMPLE_RATE} Hz")
            frames = handle.getnframes()
            pcm = handle.readframes(frames)
    except AudioError:
        raise
    except (wave.Error, EOFError) as exc:
        raise AudioError("not a WAV recording") from exc

    return Recording(pcm=pcm, seconds=len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH))
