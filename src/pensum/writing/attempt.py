"""What the page posts when a pupil lifts their finger for the last time.

Ink, not pictures. The page samples the pointer as it moves and sends the points
in the glyph's own coordinates -- the same 100 by 140 box the letterforms are
authored in -- so nothing here depends on the size of the screen it was drawn
on.

Every number in here is the page's word, exactly as a device reading's is. A
pupil with the developer tools open can post a perfect `a` they never drew, and
nothing on this side can tell. That is accepted rather than defended against:
Pensum stores no score for this exercise at all, the result is shown to the
child who produced it, and a practice tool that treats its user as an adversary
is a worse practice tool.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pensum.writing.schema import MAX_CHARACTERS

# A child overshoots, and an overshoot is information -- it is what "outside the
# lines" means. Points are kept well past the box and rejected only where they
# stop being a fingertip and start being a fuzzer.
MIN_COORDINATE = -200.0
MAX_COORDINATE = 400.0

# More strokes than any letterform needs, because a pupil may lift and restart.
# Past this it is scribbling, and scribbling is scored as what it is rather than
# refused.
MAX_STROKES = 8
# One stroke of a letter at the page's sampling rate is a couple of hundred
# points. Well past that is a stuck event loop or someone poking the endpoint,
# and marking costs guide points times ink points -- so the ceiling here is a
# cost ceiling as much as a sanity one.
MAX_POINTS = 500
MIN_POINTS = 2

# A prompt left open over lunch is not a ten-minute letter.
MAX_SECONDS = 900.0

Pair = tuple[float, float]


class InkStroke(BaseModel):
    """One press, drag and release, as points in glyph coordinates.

    Pairs rather than objects: a long word is a few thousand numbers, and
    `{"x": ..., "y": ...}` would triple the request for no added meaning.
    """

    model_config = ConfigDict(frozen=True)

    points: tuple[Pair, ...] = Field(min_length=MIN_POINTS, max_length=MAX_POINTS)

    def clamped(self) -> tuple[Pair, ...]:
        return tuple(
            (
                min(max(x, MIN_COORDINATE), MAX_COORDINATE),
                min(max(y, MIN_COORDINATE), MAX_COORDINATE),
            )
            for x, y in self.points
        )


class TracedGlyph(BaseModel):
    """Everything drawn on top of one character of the prompt."""

    model_config = ConfigDict(frozen=True)

    # Which character of the prompt this is. Sent rather than inferred from the
    # order, so a page that lets a pupil go back and redo the second letter does
    # not have to lie about the first.
    index: int = Field(ge=0, lt=MAX_CHARACTERS)
    strokes: tuple[InkStroke, ...] = Field(default=(), max_length=MAX_STROKES)


class Attempt(BaseModel):
    """One prompt, written."""

    model_config = ConfigDict(frozen=True)

    seconds: float = Field(gt=0, le=MAX_SECONDS)
    glyphs: tuple[TracedGlyph, ...] = Field(default=(), max_length=MAX_CHARACTERS)

    def for_index(self, index: int) -> TracedGlyph | None:
        return next((glyph for glyph in self.glyphs if glyph.index == index), None)
