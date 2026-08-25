"""Checkpoint selection -- the rule that decides what a pupil is shown.

Getting this wrong is not a cosmetic bug: telling a 5th-grader they should have
mastered 7. trinn material, or hiding KRLE from a 2nd-grader because its first
checkpoint is after 4. trinn, both misrepresent the curriculum to a child.
"""

from __future__ import annotations

import pytest

from pensum.domain.grades import checkpoint_for, grade_range, subjects_for_grade
from pensum.domain.models import GoalSet, LocalisedText, Subject


def goal_set(code: str, after: int, applies: tuple[int, ...]) -> GoalSet:
    return GoalSet(
        code=code,
        title=LocalisedText(by_language={"nob": f"Etter {after}. trinn"}),
        after_year=after,
        applies_to_years=applies,
        goals=(),
    )


# Shapes taken from the real catalogue.
MATEMATIKK = Subject(  # every trinn 2-10
    code="MAT01-06",
    title=LocalisedText(by_language={"nob": "Matematikk"}),
    goal_sets=tuple(goal_set(f"KV{1020 + n}", n, (n,)) for n in range(2, 11)),
)
MATEMATIKK = MATEMATIKK.model_copy(
    update={"goal_sets": (goal_set("KV1021", 2, (1, 2)), *MATEMATIKK.goal_sets[1:])}
)

NORSK = Subject(  # checkpoints after 2, 4, 7, 10
    code="NOR01-08",
    title=LocalisedText(by_language={"nob": "Norsk"}),
    goal_sets=(
        goal_set("A", 2, (1, 2)),
        goal_set("B", 4, (3, 4)),
        goal_set("C", 7, (5, 6, 7)),
        goal_set("D", 10, (8, 9, 10)),
    ),
)

KRLE = Subject(  # no 2. trinn checkpoint at all; first set spans years 1-4
    code="RLE01-04",
    title=LocalisedText(by_language={"nob": "KRLE"}),
    goal_sets=(
        goal_set("A", 4, (1, 2, 3, 4)),
        goal_set("B", 7, (5, 6, 7)),
        goal_set("C", 10, (8, 9, 10)),
    ),
)


@pytest.mark.parametrize("grade", range(1, 11))
def test_every_grade_resolves_for_core_subjects(grade: int) -> None:
    for subject in (MATEMATIKK, NORSK, KRLE):
        assert checkpoint_for(subject, grade) is not None, f"{subject.code} grade {grade}"


@pytest.mark.parametrize(
    ("grade", "expected_set", "final"),
    [
        (1, "A", False),  # working towards the 2. trinn checkpoint
        (2, "A", True),  # due to have reached it
        (3, "B", False),
        (4, "B", True),
        (5, "C", False),
        (7, "C", True),
        (10, "D", True),
    ],
)
def test_norsk_checkpoints(grade: int, expected_set: str, final: bool) -> None:
    checkpoint = checkpoint_for(NORSK, grade)
    assert checkpoint is not None
    assert checkpoint.goal_set.code == expected_set
    assert checkpoint.is_final_year is final


def test_krle_first_checkpoint_covers_the_earliest_grades() -> None:
    """KRLE defines nothing until after 4. trinn, but teaches from year 1.

    Udir's `benyttes-paa-aarstrinn` says so; without it we would either hide the
    subject from a 1st-grader or invent a 2. trinn goal set that does not exist.
    """
    for grade in (1, 2, 3):
        checkpoint = checkpoint_for(KRLE, grade)
        assert checkpoint is not None
        assert checkpoint.goal_set.code == "A"
        assert checkpoint.is_final_year is False

    assert checkpoint_for(KRLE, 4).is_final_year is True


def test_matematikk_has_a_checkpoint_every_year_from_2() -> None:
    for grade in range(2, 11):
        checkpoint = checkpoint_for(MATEMATIKK, grade)
        assert checkpoint is not None
        assert checkpoint.is_final_year is True, f"grade {grade}"


def test_first_grade_is_never_a_final_year() -> None:
    """No LK20 subject sets a checkpoint after 1. trinn."""
    for subject in (MATEMATIKK, NORSK, KRLE):
        assert checkpoint_for(subject, 1).is_final_year is False


@pytest.mark.parametrize("grade", [0, -1, 11, 13])
def test_grades_outside_grunnskole_resolve_to_nothing(grade: int) -> None:
    assert checkpoint_for(NORSK, grade) is None


def test_subject_with_no_grunnskole_goal_sets_is_omitted() -> None:
    vgs_only = Subject(code="XXX01-01", title=LocalisedText(), goal_sets=())
    assert checkpoint_for(vgs_only, 5) is None
    assert subjects_for_grade([NORSK, vgs_only], 5) == [NORSK]
    assert grade_range(vgs_only) is None


def test_grade_range_spans_the_whole_subject() -> None:
    assert grade_range(NORSK) == (1, 10)
    assert grade_range(KRLE) == (1, 10)


def test_fallback_when_upstream_omits_applies_to_years() -> None:
    """A data gap must degrade to the next checkpoint, not to an empty page."""
    sparse = Subject(
        code="GAP01-01",
        title=LocalisedText(),
        goal_sets=(goal_set("A", 4, ()), goal_set("B", 7, ())),
    )
    assert checkpoint_for(sparse, 2).goal_set.code == "A"
    assert checkpoint_for(sparse, 5).goal_set.code == "B"
    assert checkpoint_for(sparse, 9) is None
