"""In-flight readings, held only while the pupil is still reading.

Lighting words up as they are spoken needs the audio to arrive in pieces rather
than all at once, and something has to hold the pieces between requests. That is
all this is: a dictionary of buffers with a lifetime measured in minutes.

Nothing here is written to disk and nothing carries an identity. A stream knows
which passage it belongs to and nothing about who is reading it -- signed in or
not -- and the buffer is dropped the moment the reading finishes, expires, or
the process restarts.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pensum.reading.audio import MAX_BYTES, SAMPLE_RATE, SAMPLE_WIDTH

# Long enough for a slow reader on a long passage, short enough that an
# abandoned tab does not hold megabytes of audio all afternoon.
STREAM_TTL = timedelta(minutes=15)

# Audio is held in memory for the length of a reading, so the ceiling on
# concurrent readings is a ceiling on memory: this many times MAX_BYTES. Small
# on purpose -- Pensum is a study aid for one household or one classroom, and a
# deployment that needs more should say so deliberately.
MAX_STREAMS = 32


class StreamLimit(RuntimeError):
    """Too many readings in flight, or this one has gone on too long."""


@dataclass
class ReadingStream:
    """One reading, still in progress."""

    id: str
    goal_set: str
    text_id: str
    language: str
    created_at: datetime
    audio: bytearray = field(default_factory=bytearray)
    # How far through the passage the live highlight has got. Advanced from a
    # window of recent audio, never moved backwards, and never used for the
    # score -- that comes from one pass over the whole recording at the end.
    cursor: int = 0

    def append(self, chunk: bytes) -> None:
        if len(self.audio) + len(chunk) > MAX_BYTES:
            raise StreamLimit("reading too long")
        self.audio.extend(chunk)

    def tail(self, seconds: float) -> bytes:
        """The last `seconds` of audio, for the live pass.

        A window rather than the whole buffer because the live pass runs every
        couple of seconds: transcribing everything each time would cost more the
        longer a child read, which is precisely backwards.
        """
        frames = int(seconds * SAMPLE_RATE) * SAMPLE_WIDTH
        return bytes(self.audio[-frames:])

    @property
    def seconds(self) -> float:
        return len(self.audio) / (SAMPLE_RATE * SAMPLE_WIDTH)

    def expired(self, now: datetime) -> bool:
        return now - self.created_at > STREAM_TTL


class StreamStore:
    """Every reading currently in flight. In memory, and deliberately so."""

    def __init__(self) -> None:
        self._streams: dict[str, ReadingStream] = {}

    def create(self, *, goal_set: str, text_id: str, language: str, now: datetime) -> ReadingStream:
        self._prune(now)
        if len(self._streams) >= MAX_STREAMS:
            raise StreamLimit("too many readings in flight")

        stream = ReadingStream(
            # Unguessable, because it is the only thing standing between one
            # reading's audio and another request.
            id=secrets.token_urlsafe(16),
            goal_set=goal_set,
            text_id=text_id,
            language=language,
            created_at=now,
        )
        self._streams[stream.id] = stream
        return stream

    def get(self, stream_id: str, now: datetime) -> ReadingStream | None:
        stream = self._streams.get(stream_id)
        if stream is None:
            return None
        if stream.expired(now):
            self.drop(stream_id)
            return None
        return stream

    def drop(self, stream_id: str) -> None:
        """Forget a reading. Called the moment it finishes, not on a timer."""
        self._streams.pop(stream_id, None)

    def _prune(self, now: datetime) -> None:
        for stream_id in [i for i, s in self._streams.items() if s.expired(now)]:
            self.drop(stream_id)

    def __len__(self) -> int:
        return len(self._streams)
