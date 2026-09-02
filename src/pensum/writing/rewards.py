"""What the writing screen celebrates.

The same shape as the reading screen's rewards, and for the same reasons: a
child needs the practice to go somewhere, and the way it goes somewhere must not
quietly become a verdict.

* **Finishing** is the reward that cannot mislead. Every character was written.
  A wobbly finger gets exactly what a steady one gets.
* **Stars** come from how close the tracing was, and the thresholds are set
  low enough that an ordinary attempt earns three. The scorer marks a fingertip
  on glass, which is a blunter instrument than a pencil, and thresholds tuned
  for a pencil would tell every six-year-old they were failing at something they
  were doing correctly.
* **Neat all the way through** is the one that needs the whole prompt to be
  good rather than the average of it, so a word is not carried by its easy
  letters.

Personal bests and streaks are not computed here, exactly as with reading:
Pensum keeps no history of this exercise at all.
"""

from __future__ import annotations

from dataclasses import dataclass

from pensum.writing.tracing import Mark

# Deliberately forgiving. Three stars is "you wrote this", not "you wrote this
# better than the geometry expected".
THREE_STARS = 0.82
TWO_STARS = 0.65
ONE_STAR = 0.45

MAX_STARS = 3

# Every character above this, rather than the mean above it.
EVEN_THROUGHOUT = 0.70


@dataclass(frozen=True)
class Rewards:
    """The badges one attempt earned, and nothing about who earned them."""

    finished: bool
    stars: int
    even: bool

    @property
    def any_earned(self) -> bool:
        return self.finished or bool(self.stars) or self.even


def stars_for(score: float) -> int:
    if score >= THREE_STARS:
        return 3
    if score >= TWO_STARS:
        return 2
    if score >= ONE_STAR:
        return 1
    return 0


def earned(marked: Mark) -> Rewards:
    return Rewards(
        finished=marked.finished,
        stars=stars_for(marked.score),
        even=marked.finished and all(glyph.score >= EVEN_THROUGHOUT for glyph in marked.glyphs),
    )
