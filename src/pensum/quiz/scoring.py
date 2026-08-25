"""Selecting items and scoring an attempt.

The result is deliberately framed as feedback, not a verdict. Pensum cannot
assess whether a pupil has met a competence goal -- it can only say which
questions they got right -- and the difference matters when the user is seven.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from pensum.items.schema import QuizItem
from pensum.quiz.session import DEFAULT_LENGTH, QuizSession

# A practice threshold, not a grade boundary. Udir sets no such number; this one
# is ours and exists only to make "how did I do?" answerable.
PASS_THRESHOLD = 0.7


def select(
    items: list[QuizItem], count: int = DEFAULT_LENGTH, seed: int | None = None
) -> list[QuizItem]:
    """Pick `count` items, spreading them across goals before repeating any.

    Sampling naively would let one well-covered goal dominate a quiz while
    others never appear. Round-robin over goals keeps a short quiz representative
    of the checkpoint as a whole, which is the entire premise of "can you pass
    2. klasse?".
    """
    if not items:
        return []

    rng = random.Random(seed)  # noqa: S311 -- quiz variety, not cryptography

    by_goal: dict[str, list[QuizItem]] = {}
    for item in items:
        by_goal.setdefault(item.goal, []).append(item)
    for pool in by_goal.values():
        rng.shuffle(pool)

    goals = sorted(by_goal)
    rng.shuffle(goals)

    selected: list[QuizItem] = []
    while len(selected) < count:
        exhausted = True
        for goal in goals:
            if by_goal[goal]:
                selected.append(by_goal[goal].pop())
                exhausted = False
                if len(selected) == count:
                    break
        if exhausted:
            break  # fewer items exist than were asked for

    rng.shuffle(selected)
    return selected


@dataclass(frozen=True)
class GoalResult:
    """How an attempt went for one competence goal."""

    goal: str
    correct: int
    total: int

    @property
    def all_correct(self) -> bool:
        return self.correct == self.total


@dataclass(frozen=True)
class Result:
    """The outcome of an attempt."""

    correct: int
    total: int
    by_goal: tuple[GoalResult, ...]

    @property
    def share(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def percentage(self) -> int:
        return round(self.share * 100)

    @property
    def passed(self) -> bool:
        return self.share >= PASS_THRESHOLD

    @property
    def strong(self) -> tuple[GoalResult, ...]:
        return tuple(g for g in self.by_goal if g.all_correct)

    @property
    def needs_practice(self) -> tuple[GoalResult, ...]:
        """The useful half of the result: what to go and work on."""
        return tuple(g for g in self.by_goal if not g.all_correct)


def score(session: QuizSession) -> Result:
    """Grade an attempt, per goal as well as overall.

    The per-goal breakdown is the point. "62%" tells a pupil nothing they can
    act on; "you have this, go practise counting backwards" does.
    """
    tally: dict[str, list[int]] = {}
    correct = 0

    for item in session.items:
        response = session.answers.get(item.id)
        if response is None:
            continue
        got_it = item.is_correct(response)
        correct += got_it
        counts = tally.setdefault(item.goal, [0, 0])
        counts[0] += got_it
        counts[1] += 1

    by_goal = tuple(
        GoalResult(goal=goal, correct=c, total=t) for goal, (c, t) in sorted(tally.items())
    )
    return Result(correct=correct, total=sum(g.total for g in by_goal), by_goal=by_goal)
