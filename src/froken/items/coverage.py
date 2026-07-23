"""How much of a checkpoint a quiz actually reaches.

Many competence goals cannot be tested in writing -- exploring, discussing,
making something. Those are recorded as not assessable rather than quizzed, so a
checkpoint's quiz honestly covers only part of its curriculum. This turns that
fact into something the page can show, so "can you pass 2. klasse?" does not
quietly overstate itself.

Pure, so it is testable without the web layer.
"""

from __future__ import annotations

from dataclasses import dataclass

from froken.domain.models import Goal, GoalSet


@dataclass(frozen=True)
class GoalCoverage:
    """One competence goal, and whether the quiz reaches it."""

    goal: Goal
    in_quiz: bool


@dataclass(frozen=True)
class Coverage:
    """Every goal in a checkpoint, tagged with whether the quiz covers it."""

    entries: tuple[GoalCoverage, ...]

    @property
    def total(self) -> int:
        return len(self.entries)

    @property
    def tested(self) -> int:
        return sum(1 for entry in self.entries if entry.in_quiz)

    @property
    def complete(self) -> bool:
        """True when the quiz reaches every goal -- so the note can be dropped."""
        return self.total > 0 and self.tested == self.total


def coverage(goal_set: GoalSet, tested_codes: set[str]) -> Coverage:
    """Tag each goal in `goal_set` by whether a served item tests it.

    Computed against served (reviewed) items, not authored ones: a goal whose
    only questions are still unreviewed is not, from a pupil's seat, in the quiz.
    """
    return Coverage(tuple(GoalCoverage(goal, goal.code in tested_codes) for goal in goal_set.goals))
