"""The geometry a letter is made of.

A glyph here is a list of *strokes*, and a stroke is an SVG path string. That
choice is the whole reason this module is small: a browser draws and measures a
path natively, so the page and the scorer work from the same authored string
rather than from two hand-kept copies of the same curve.

Only `M`, `L`, `C` and `Q` are understood, in absolute coordinates. Arcs are
deliberately absent -- `A` is the one SVG command whose parameterisation is
awkward to get right twice, and every letterform here is expressible as cubics.
Relative commands are refused rather than supported: a letter that reads
differently depending on where the previous stroke ended is a letter nobody can
review by looking at it.
"""

from __future__ import annotations

import math
import re

# The em box every glyph is authored in. Not a pixel size -- the page scales it
# to whatever the screen allows -- but the units every tolerance below is in.
VIEW_WIDTH = 100.0
VIEW_HEIGHT = 140.0

# The writing lines, in those units. Named here rather than left implicit in the
# data, so the page can rule the paper and the alphabet can be checked against
# the same four numbers a teacher would.
ASCENDER = 10.0
X_HEIGHT = 60.0
BASELINE = 110.0
DESCENDER = 135.0

# How far apart the sampled points along a guide stroke are. Fine enough that a
# fingertip's tolerance covers several of them, coarse enough that scoring a
# whole word stays a few thousand distance calculations.
SPACING = 2.0

# Steps per curve segment before resampling. No curve here is longer than half a
# letter, which is well inside the range where more steps stop moving a point.
FLATTEN_STEPS = 24

_TOKEN = re.compile(r"[MLCQ]|-?\d+(?:\.\d+)?")
_ANY_TOKEN = re.compile(r"[A-Za-z]|-?\d+(?:\.\d+)?")
_ARITY = {"M": 2, "L": 2, "Q": 4, "C": 6}

Point = tuple[float, float]


class PathError(ValueError):
    """An authored stroke that is not a stroke."""


def _cubic(p0: Point, p1: Point, p2: Point, p3: Point) -> list[Point]:
    out = []
    for step in range(1, FLATTEN_STEPS + 1):
        t = step / FLATTEN_STEPS
        u = 1.0 - t
        a, b, c, d = u * u * u, 3 * u * u * t, 3 * u * t * t, t * t * t
        out.append(
            (
                a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
                a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
            )
        )
    return out


def _quadratic(p0: Point, p1: Point, p2: Point) -> list[Point]:
    out = []
    for step in range(1, FLATTEN_STEPS + 1):
        t = step / FLATTEN_STEPS
        u = 1.0 - t
        a, b, c = u * u, 2 * u * t, t * t
        out.append((a * p0[0] + b * p1[0] + c * p2[0], a * p0[1] + b * p1[1] + c * p2[1]))
    return out


def flatten(d: str) -> list[Point]:
    """The stroke as a polyline, in the order it is written.

    The order is the point. A stroke is a movement, not a shape: reversing the
    list would describe the same ink and the wrong letter.
    """
    if len(_TOKEN.findall(d)) != len(_ANY_TOKEN.findall(d)):
        raise PathError(f"unsupported command in path: {d!r}")
    tokens = _TOKEN.findall(d)
    if not tokens or tokens[0] != "M":
        raise PathError(f"a stroke must start with M: {d!r}")

    points: list[Point] = []
    cursor: Point = (0.0, 0.0)
    index = 0
    while index < len(tokens):
        command = tokens[index]
        if command not in _ARITY:
            raise PathError(f"expected a command, found {command!r} in {d!r}")
        arity = _ARITY[command]
        raw = tokens[index + 1 : index + 1 + arity]
        if len(raw) != arity or any(value in _ARITY for value in raw):
            raise PathError(f"{command} wants {arity} numbers in {d!r}")
        numbers = [float(value) for value in raw]
        index += 1 + arity

        if command == "M":
            if points:
                raise PathError(f"a stroke is one unbroken movement; second M in {d!r}")
            cursor = (numbers[0], numbers[1])
            points.append(cursor)
        elif command == "L":
            cursor = (numbers[0], numbers[1])
            points.append(cursor)
        elif command == "Q":
            control = (numbers[0], numbers[1])
            end = (numbers[2], numbers[3])
            points.extend(_quadratic(cursor, control, end))
            cursor = end
        else:
            first = (numbers[0], numbers[1])
            second = (numbers[2], numbers[3])
            end = (numbers[4], numbers[5])
            points.extend(_cubic(cursor, first, second, end))
            cursor = end

    if len(points) < 2:
        raise PathError(f"a stroke needs somewhere to go: {d!r}")
    return points


def resample(
    points: list[Point], spacing: float = SPACING, limit: int | None = None
) -> tuple[Point, ...]:
    """Evenly spaced points along a polyline.

    Coverage is counted per point, so uneven spacing would weight a tight curve
    more heavily than a long straight: a child who traced the whole of an `l`
    would score below one who traced the curl of an `e` and stopped.

    `limit` stops the walk once that many points exist. The output grows with
    the polyline's *length*, not with how many points went in, so a short list
    of far-apart points can expand into an enormous one -- which is fine for an
    authored letterform and is a cost the caller must be able to bound for ink
    that arrived over the wire.
    """
    if spacing <= 0:
        raise ValueError("spacing must be positive")
    out: list[Point] = [points[0]]
    carry = 0.0
    for start, end in zip(points, points[1:], strict=False):
        span = math.dist(start, end)
        if span == 0:
            continue
        walked = spacing - carry
        while walked <= span:
            if limit is not None and len(out) >= limit:
                return tuple(out)
            ratio = walked / span
            out.append(
                (start[0] + (end[0] - start[0]) * ratio, start[1] + (end[1] - start[1]) * ratio)
            )
            walked += spacing
        carry = (carry + span) % spacing
    if math.dist(out[-1], points[-1]) > spacing / 2:
        out.append(points[-1])
    return tuple(out)


def sample(d: str, spacing: float = SPACING) -> tuple[Point, ...]:
    """An authored stroke as evenly spaced points, in writing order."""
    return resample(flatten(d), spacing)


def length(points: tuple[Point, ...] | list[Point]) -> float:
    return sum(math.dist(a, b) for a, b in zip(points, points[1:], strict=False))


def point_to_segment(point: Point, start: Point, end: Point) -> float:
    """Distance from a point to a line segment, not to its infinite line."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    span = dx * dx + dy * dy
    if span == 0:
        return math.dist(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / span
    t = max(0.0, min(1.0, t))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def distance_to(point: Point, polyline: tuple[Point, ...]) -> float:
    """How far a point is from the nearest place on a polyline."""
    if len(polyline) == 1:
        return math.dist(point, polyline[0])
    return min(point_to_segment(point, a, b) for a, b in zip(polyline, polyline[1:], strict=False))


def nearest_index(point: Point, polyline: tuple[Point, ...]) -> int:
    """Which sampled point of a guide a bit of ink is nearest to.

    Used as a position along the stroke rather than as a distance: comparing
    where the ink started with where it ended is what tells a downstroke from an
    upstroke.
    """
    best, best_at = math.inf, 0
    for index, candidate in enumerate(polyline):
        gap = math.dist(point, candidate)
        if gap < best:
            best, best_at = gap, index
    return best_at
