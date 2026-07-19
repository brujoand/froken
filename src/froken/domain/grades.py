"""Mapping a pupil's klasse to the curriculum checkpoint that governs it.

This is the one place grade logic lives. It is small on purpose: LK20 already
answers the question, and the job here is to use its answer rather than invent
one.
"""

from __future__ import annotations

from dataclasses import dataclass

from froken.domain.models import GoalSet, Subject

# Norwegian grunnskole: 1.-10. klasse.
FIRST_GRADE = 1
LAST_GRADE = 10


@dataclass(frozen=True)
class Checkpoint:
    """The goal set governing a pupil, and whether they are at its end.

    `is_final_year` distinguishes "you should master this by June" from "this is
    what you are working towards" -- the difference between a 2nd-grader and a
    1st-grader looking at the same 2. trinn goal set. The UI wording depends on
    it, and getting it wrong would tell a 5th-grader they had failed material
    they are not due to have covered for two more years.
    """

    goal_set: GoalSet
    grade: int
    is_final_year: bool


def checkpoint_for(subject: Subject, grade: int) -> Checkpoint | None:
    """Return the goal set that applies to `grade`, or None if none does.

    Prefers Udir's own `benyttes-paa-aarstrinn`, which states outright which
    school years each checkpoint covers -- KRLE's 4. trinn set, for instance,
    covers years 1 through 4, so a 1st-grader in KRLE is working towards it.

    Falls back to "the earliest checkpoint at or after this grade" only when
    upstream omits that field, so a data gap degrades to a sensible answer
    instead of an empty page.
    """
    if not FIRST_GRADE <= grade <= LAST_GRADE:
        return None

    for goal_set in subject.goal_sets:
        if grade in goal_set.applies_to_years:
            return Checkpoint(
                goal_set=goal_set,
                grade=grade,
                is_final_year=grade == goal_set.after_year,
            )

    upcoming = [gs for gs in subject.goal_sets if gs.after_year >= grade]
    if not upcoming:
        return None
    goal_set = min(upcoming, key=lambda gs: gs.after_year)
    return Checkpoint(
        goal_set=goal_set,
        grade=grade,
        is_final_year=grade == goal_set.after_year,
    )


def subjects_for_grade(subjects: list[Subject], grade: int) -> list[Subject]:
    """Those subjects that have any curriculum covering `grade`.

    Not every subject starts in 1. klasse -- KRLE's first goal set is after 4.
    trinn and covers years 1-4, while some subjects genuinely have nothing until
    later. Showing a pupil an empty subject would be worse than omitting it.
    """
    return [s for s in subjects if checkpoint_for(s, grade) is not None]


def grade_range(subject: Subject) -> tuple[int, int] | None:
    """The span of school years `subject` covers, or None if it covers none."""
    years = [y for gs in subject.goal_sets for y in gs.applies_to_years]
    return (min(years), max(years)) if years else None
