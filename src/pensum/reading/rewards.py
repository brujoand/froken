"""What the reading screen celebrates.

Pensum says on every other page that it is practice and not assessment, and
three of the four rewards here are in some tension with that. They are here
because they were asked for, and the tension is managed rather than hidden:

* **Finishing** is the reward that cannot mislead. A slow reader who reaches the
  last word gets exactly what a fast one gets.
* **Stars** come from accuracy, and accuracy comes from a recogniser that
  mishears children, dialects and second-language speakers more than it
  mishears anyone else. So the thresholds are set low enough that an ordinary
  reading earns three, the caveat is rendered next to them, and a reading that
  was never listened to shows none at all rather than a generous guess.
* **Hitting the band** is awarded for reaching it *or passing it*, never for
  landing inside it. Rewarding only the middle would turn a guideline range into
  a target with a penalty on both sides, which is the reading the norms file
  spends a paragraph refusing.
* **Personal bests and streaks** are not computed here. They are per-pupil
  history, and Pensum keeps none: they live in the pupil's own browser, so the
  server never learns that a child read the same passage twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from pensum.reading.fluency import ABOVE, WITHIN, Fluency, Verdict

# Deliberately forgiving. Three stars is "you read this", not "you read this
# better than the machine expected".
THREE_STARS = 0.90
TWO_STARS = 0.75
ONE_STAR = 0.55

MAX_STARS = 3


@dataclass(frozen=True)
class Rewards:
    """The badges one reading earned, and nothing about who earned them."""

    # Reached the last word of the passage.
    finished: bool
    # Out of MAX_STARS, or None when nothing was listened to and there is
    # therefore no accuracy to turn into stars.
    stars: int | None
    # Reached or passed the band for the checkpoint. None when there is no band,
    # or the reading was too short to measure.
    band_hit: bool | None

    @property
    def any_earned(self) -> bool:
        return self.finished or bool(self.stars) or bool(self.band_hit)


def stars_for(accuracy: float | None) -> int | None:
    if accuracy is None:
        return None
    if accuracy >= THREE_STARS:
        return 3
    if accuracy >= TWO_STARS:
        return 2
    if accuracy >= ONE_STAR:
        return 1
    return 0


def earned(fluency: Fluency, verdict: Verdict | None) -> Rewards:
    """Badges for a checked reading."""
    return Rewards(
        finished=fluency.finished,
        stars=stars_for(fluency.accuracy),
        band_hit=verdict in (WITHIN, ABOVE) if verdict is not None else None,
    )


def earned_timed(*, finished: bool, band_hit: bool | None) -> Rewards:
    """Badges for a reading that was timed but not listened to.

    No stars: there is no accuracy, and inventing one from the clock would hand
    out three stars for reading fast and none for reading carefully.
    """
    return Rewards(finished=finished, stars=None, band_hit=band_hit)
