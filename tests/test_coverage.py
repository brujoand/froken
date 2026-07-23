"""Quiz coverage -- how much of a checkpoint a quiz reaches.

The point of the feature is honesty: norsk leaves most of its goals untested
because they are speaking and writing, and the page must say so rather than let
a score imply full coverage.
"""

from __future__ import annotations

from froken.catalogue.loader import Catalogue
from froken.domain.models import Goal, GoalSet, LocalisedText
from froken.items.coverage import coverage
from froken.items.loader import ItemBank


def goal(code: str) -> Goal:
    return Goal(code=code, text=LocalisedText(by_language={"nob": code}))


def goal_set(*codes: str) -> GoalSet:
    return GoalSet(
        code="KVx",
        title=LocalisedText(),
        after_year=2,
        applies_to_years=(2,),
        goals=tuple(goal(c) for c in codes),
    )


def test_coverage_tags_each_goal_by_whether_a_quiz_reaches_it() -> None:
    cov = coverage(goal_set("KM1", "KM2", "KM3"), tested_codes={"KM1", "KM3"})
    assert cov.total == 3
    assert cov.tested == 2
    assert not cov.complete
    assert [(e.goal.code, e.in_quiz) for e in cov.entries] == [
        ("KM1", True),
        ("KM2", False),
        ("KM3", True),
    ]


def test_coverage_preserves_goal_order() -> None:
    cov = coverage(goal_set("KM3", "KM1", "KM2"), tested_codes=set())
    assert [e.goal.code for e in cov.entries] == ["KM3", "KM1", "KM2"]


def test_complete_only_when_every_goal_is_reached() -> None:
    gs = goal_set("KM1", "KM2")
    assert coverage(gs, {"KM1", "KM2"}).complete is True
    assert coverage(gs, {"KM1"}).complete is False


def test_empty_goal_set_is_not_complete() -> None:
    """No goals means nothing to be complete about -- avoids a 0/0 'all covered'."""
    assert coverage(goal_set(), set()).complete is False


def test_coverage_counts_only_served_items() -> None:
    """A goal whose only item is unreviewed is not, from a pupil's seat, tested."""
    strict = ItemBank.load()
    catalogue = Catalogue.load()

    for code in ["NOR01-08", "MAT01-06"]:
        for gs in catalogue.subject(code).goal_sets:
            cov = strict.coverage(gs)
            assert cov.tested == len(strict.tested_goals(gs.code))
            assert cov.tested <= cov.total


def test_norsk_2_trinn_is_only_partly_covered() -> None:
    """The case the feature exists for: most of the checkpoint is untestable."""
    catalogue = Catalogue.load()
    gs = catalogue.subject("NOR01-08").goal_sets[0]
    cov = ItemBank.load().coverage(gs)
    assert cov.tested < cov.total
    assert not cov.complete
