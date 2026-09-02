"""Marking a traced letter.

Four questions are asked of every stroke, and they are deliberately different
questions:

* **Coverage** -- how much of the guide the finger actually went over. This is
  the one that catches a letter half written.
* **Neatness** -- how much of what was drawn was on the guide. This is the one
  that catches ink that wandered off.
* **Flow** -- whether the finger moved *along* the stroke rather than back and
  forth over it. This is the one that catches a scribble, and it is the reason
  the other two are not enough: a child who scrubs their whole hand over an `o`
  covers every point of it and never leaves it, so coverage and neatness both
  say the letter was written perfectly. Flow says it was not written at all.
* **Direction** -- whether the stroke ran the way it is taught. An `a` traced
  anticlockwise is the right shape and the wrong movement, and the movement is
  what a first-grader is practising.

There is one more, and it is a multiplier rather than a question: **economy**,
the length of ink against the length of the guide. Retracing a letter to be sure
is free; drawing five times the letter is not writing it.

Stroke *order* is scored too, and softly: writing the crossbar of a `t` before
the stem costs a little and can never fail the letter. That is a judgement about
six-year-olds rather than about handwriting -- a tool that rejects the shape
because the order was wrong teaches a child that they cannot write.

Nothing here is an assessment. It is the same claim the rest of Pensum makes:
practice, with a number attached so the practice has somewhere to go.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from pensum.writing.paths import (
    Point,
    distance_to,
    length,
    nearest_index,
    resample,
    sample,
)
from pensum.writing.schema import Alphabet, Glyph, WritingPrompt

# How far from the guide a fingertip may be and still count as on it, in grid
# units -- a little over a tenth of a letter's width. A fingertip on a phone
# covers roughly this much of a letter that fills the screen, so a tighter
# tolerance would mark down the finger rather than the writing.
TOLERANCE = 12.0

# Ink is resampled before scoring: a slow finger emits far more points than a
# fast one over the same path, and unsampled points would let dawdling inflate
# every measure below.
INK_SPACING = 3.0

# Coverage carries more than neatness. Both matter, but a letter fully traced a
# bit wobbily is closer to right than a tidy quarter of one -- and neatness is
# the measure a child gets for free by drawing very little.
COVERAGE_WEIGHT = 0.7
NEATNESS_WEIGHT = 0.3

# What a stroke keeps when it ran backwards. Not zero: the shape was still
# drawn, and telling a child who drew a recognisable `o` that they drew nothing
# is false.
BACKWARDS_KEEPS = 0.75

# Where flow stops counting against a stroke. Flow is the share of movement that
# went forwards along the guide, so 1.0 is a clean trace, 0.0 a clean reverse,
# and 0.5 is ink that went nowhere. Measured against synthetic tracings, a
# deliberately wobbly finger holds 0.75 or better and a scribble sits near 0.5,
# so full marks stop at a quarter either side of undecided.
COHERENCE_FULL = 0.25

# How much more ink than guide a stroke may use before it stops being tracing.
# Generous on purpose: going round an `o` twice to be sure is what a careful
# six-year-old does, and it must cost nothing.
INK_SLACK = 2.5

# What writing the strokes in the wrong order costs, once, however many are out
# of place. Deliberately small -- see the module docstring.
ORDER_COST = 0.10

# Below this a pairing is not a worse attempt at that stroke, it is ink that
# belongs to a different stroke entirely.
PAIR_FLOOR = 0.15

# The most resampled ink points one character's marking will look at, across all
# of its strokes. Marking costs guide points times ink points, so this is what
# keeps a posted body from turning into minutes of arithmetic here. A real
# tracing of one letter is a hundred or so points; four hundred is a letter
# traced four times over, and anything that reaches the ceiling has already
# scored nothing on `economy`.
#
# It bounds the cost rather than making it small: the nastiest body the schema
# accepts still takes a couple of seconds of CPU, which is why the route runs
# this off the event loop.
MAX_INK_POINTS = 400


@dataclass(frozen=True)
class StrokeMark:
    """One guide stroke, and what was drawn over it."""

    coverage: float
    neatness: float
    # The share of the movement that ran forwards along the guide. 1.0 traced
    # it, 0.0 traced it backwards, 0.5 went nowhere.
    flow: float
    # Ink length against guide length, already inverted and capped, so 1.0 is
    # "no more ink than the letter needs".
    economy: float
    # Which of the pupil's strokes was matched to this one, or None when nothing
    # was. Kept so the result page can say which stroke was missed rather than
    # only showing a lower number.
    ink_index: int | None

    @property
    def attempted(self) -> bool:
        return self.ink_index is not None

    @property
    def forward(self) -> bool:
        return self.flow >= 0.5

    @property
    def coherence(self) -> float:
        """How decided the movement was, whichever way it went.

        Full marks for a stroke that mostly ran one way; nothing for ink that
        went back and forth in equal measure, because that is a scrub rather
        than a stroke.
        """
        return min(1.0, abs(self.flow - 0.5) / COHERENCE_FULL)

    @property
    def score(self) -> float:
        if not self.attempted:
            return 0.0
        raw = COVERAGE_WEIGHT * self.coverage + NEATNESS_WEIGHT * self.neatness
        raw *= self.coherence * self.economy
        return raw if self.forward else raw * BACKWARDS_KEEPS


@dataclass(frozen=True)
class GlyphMark:
    """One character of the prompt, marked."""

    char: str
    strokes: tuple[StrokeMark, ...]
    in_order: bool

    @property
    def attempted(self) -> bool:
        return any(stroke.attempted for stroke in self.strokes)

    @property
    def missed(self) -> int:
        return sum(1 for stroke in self.strokes if not stroke.attempted)

    @property
    def backwards(self) -> int:
        return sum(1 for stroke in self.strokes if stroke.attempted and not stroke.forward)

    @property
    def score(self) -> float:
        if not self.strokes:
            return 0.0
        raw = sum(stroke.score for stroke in self.strokes) / len(self.strokes)
        if not self.in_order:
            raw -= ORDER_COST
        return max(0.0, min(1.0, raw))


@dataclass(frozen=True)
class Mark:
    """The whole prompt, marked."""

    glyphs: tuple[GlyphMark, ...]
    seconds: float

    @property
    def attempted(self) -> int:
        return sum(1 for glyph in self.glyphs if glyph.attempted)

    @property
    def finished(self) -> bool:
        """Every character was written. Not "written well" -- see `score`."""
        return bool(self.glyphs) and self.attempted == len(self.glyphs)

    @property
    def score(self) -> float:
        """The mean over every character the prompt asked for.

        Over every character, not over the ones attempted: skipping the hard
        letter must not raise the score.
        """
        if not self.glyphs:
            return 0.0
        return sum(glyph.score for glyph in self.glyphs) / len(self.glyphs)


@lru_cache(maxsize=512)
def guide(stroke: str) -> tuple[Point, ...]:
    """An authored stroke as points. Cached: the same handful of letterforms are
    scored over and over, and the sampling is pure."""
    return sample(stroke)


def _coverage(guide_points: tuple[Point, ...], ink: tuple[Point, ...]) -> float:
    if not guide_points:
        return 0.0
    hit = sum(1 for point in guide_points if distance_to(point, ink) <= TOLERANCE)
    return hit / len(guide_points)


def _neatness(guide_points: tuple[Point, ...], ink: tuple[Point, ...]) -> float:
    if not ink:
        return 0.0
    hit = sum(1 for point in ink if distance_to(point, guide_points) <= TOLERANCE)
    return hit / len(ink)


def _flow(guide_points: tuple[Point, ...], ink: tuple[Point, ...]) -> float:
    """The share of the movement that ran along the guide the way it runs.

    Asks where each bit of ink sits *along* the guide rather than comparing the
    two endpoints. A circle starts and ends in the same place, so endpoints
    alone cannot tell an `o` from an `o` drawn backwards -- and that is the
    letter where the difference matters most.

    Only ink that is on the guide votes. A stroke that wanders off and comes
    back should lose neatness for the wandering, not have the wandering counted
    as evidence about its direction.
    """
    positions = [
        nearest_index(point, guide_points)
        for point in ink
        if distance_to(point, guide_points) <= TOLERANCE
    ]
    ahead = sum(1 for a, b in zip(positions, positions[1:], strict=False) if b > a)
    behind = sum(1 for a, b in zip(positions, positions[1:], strict=False) if b < a)
    if ahead + behind == 0:
        # Ink that never moved along the guide at all: a dot, or a finger held
        # still. Undecided, which `coherence` scores as nothing.
        return 0.5
    return ahead / (ahead + behind)


def _economy(guide_points: tuple[Point, ...], drawn: float) -> float:
    """`drawn` is the length of the ink as posted, before any trimming."""
    if drawn <= 0:
        return 0.0
    return min(1.0, length(guide_points) * INK_SLACK / drawn)


def _pair(
    guide_points: tuple[Point, ...], ink: tuple[Point, ...], drawn: float
) -> tuple[float, float, float, float]:
    return (
        _coverage(guide_points, ink),
        _neatness(guide_points, ink),
        _flow(guide_points, ink),
        _economy(guide_points, drawn),
    )


def prepare_ink(strokes: list[tuple[Point, ...]]) -> list[tuple[tuple[Point, ...], float]]:
    """Resample each stroke, and say how long it really was.

    Two jobs, and the second is why they are done together. Resampling produces
    points in proportion to *length*, so ink that zig-zags across the page
    expands into far more points than were posted -- which is a cost the marking
    below pays quadratically. So the number of points is capped here, and the
    true length is measured before the cap and carried alongside.

    Nothing legitimate is affected: a letter is a few hundred units of ink, and
    a stroke long enough to be trimmed has already lost everything on `economy`.
    """
    prepared: list[tuple[tuple[Point, ...], float]] = []
    budget = MAX_INK_POINTS
    for points in strokes:
        # Measured before the walk, and on the points as posted: the length is
        # what `economy` judges, and it must not shrink because the walk was cut
        # short.
        drawn = length(points)
        sampled = resample(list(points), INK_SPACING, limit=max(2, budget))
        budget = max(0, budget - len(sampled))
        prepared.append((sampled, drawn))
    return prepared


def mark_glyph(glyph: Glyph, ink_strokes: list[tuple[Point, ...]]) -> GlyphMark:
    """Match what was drawn to what was asked for, then mark each pairing.

    The matching is greedy over the best-scoring pairs rather than positional:
    a pupil who draws the stem of a `b` second has drawn a `b`, and pairing by
    position would mark both strokes as wrong instead of the order as wrong.
    """
    guides = [guide(stroke) for stroke in glyph.strokes]
    inks = prepare_ink(ink_strokes)

    Measured = tuple[float, float, float, float]
    scored: list[tuple[float, int, int, Measured]] = []
    for g_index, guide_points in enumerate(guides):
        for i_index, (ink, drawn) in enumerate(inks):
            measured = _pair(guide_points, ink, drawn)
            fit = COVERAGE_WEIGHT * measured[0] + NEATNESS_WEIGHT * measured[1]
            if fit >= PAIR_FLOOR:
                scored.append((fit, g_index, i_index, measured))

    scored.sort(key=lambda row: row[0], reverse=True)
    taken_guides: dict[int, tuple[int, Measured]] = {}
    taken_inks: set[int] = set()
    for _, g_index, i_index, measured in scored:
        if g_index in taken_guides or i_index in taken_inks:
            continue
        taken_guides[g_index] = (i_index, measured)
        taken_inks.add(i_index)

    marks = []
    for g_index in range(len(guides)):
        match = taken_guides.get(g_index)
        if match is None:
            marks.append(
                StrokeMark(coverage=0.0, neatness=0.0, flow=0.5, economy=0.0, ink_index=None)
            )
            continue
        i_index, (coverage, neatness, flow, economy) = match
        marks.append(
            StrokeMark(
                coverage=coverage,
                neatness=neatness,
                flow=flow,
                economy=economy,
                ink_index=i_index,
            )
        )

    # In order when the strokes that were attempted were drawn in the sequence
    # the letterform names. Strokes that were missed are simply not part of the
    # question.
    drawn = [mark.ink_index for mark in marks if mark.ink_index is not None]
    return GlyphMark(char=glyph.char, strokes=tuple(marks), in_order=drawn == sorted(drawn))


def mark(prompt: WritingPrompt, alphabet: Alphabet, attempt) -> Mark:
    """Mark a whole prompt.

    A character with no glyph is impossible here -- the library refuses to serve
    a prompt the alphabet cannot draw -- so this may assume one exists.
    """
    marks = []
    for index, char in enumerate(prompt.characters):
        glyph = alphabet.glyph(char)
        if glyph is None:  # pragma: no cover - the library filters these out
            continue
        traced = attempt.for_index(index)
        ink = [stroke.clamped() for stroke in traced.strokes] if traced else []
        marks.append(mark_glyph(glyph, ink))
    return Mark(glyphs=tuple(marks), seconds=attempt.seconds)
