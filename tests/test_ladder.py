"""The per-subject ladder a nivåtest walks.

The rungs are Udir's checkpoints, so the risk here is not arithmetic -- it is
quietly inventing structure the curriculum does not have. Two shapes matter and
both are real: matematikk resolves to a single school year per rung, and
naturfag has an outright hole where no checkpoint covers 3. trinn.
"""

from __future__ import annotations

from pensum.catalogue.loader import Catalogue
from pensum.domain.ladder import Ladder
from pensum.domain.models import GoalSet, LocalisedText, Subject
from pensum.items.loader import ItemBank


def goal_set(code: str, after: int, applies: tuple[int, ...]) -> GoalSet:
    return GoalSet(
        code=code,
        title=LocalisedText(by_language={"nob": f"Etter {after}. trinn"}),
        after_year=after,
        applies_to_years=applies,
        goals=(),
    )


NORSK = Subject(
    code="NOR01-08",
    title=LocalisedText(by_language={"nob": "Norsk"}),
    goal_sets=(
        goal_set("A", 2, (1, 2)),
        goal_set("B", 4, (3, 4)),
        goal_set("C", 7, (5, 6, 7)),
        goal_set("D", 10, (8, 9, 10)),
    ),
)

# The real hole: nothing covers 3. trinn.
NATURFAG = Subject(
    code="NAT01-05",
    title=LocalisedText(by_language={"nob": "Naturfag"}),
    goal_sets=(
        goal_set("A", 2, (1, 2)),
        goal_set("B", 5, (4, 5)),
        goal_set("C", 7, (5, 6, 7)),
        goal_set("D", 10, (8, 9, 10)),
    ),
)

ALL = {"A", "B", "C", "D"}


def test_rungs_are_ordered_by_checkpoint_not_file_order() -> None:
    shuffled = NORSK.model_copy(update={"goal_sets": tuple(reversed(NORSK.goal_sets))})
    ladder = Ladder.build(shuffled, ALL)
    assert [r.after_year for r in ladder.rungs] == [2, 4, 7, 10]
    assert [r.index for r in ladder.rungs] == [0, 1, 2, 3]


def test_checkpoints_without_a_quiz_are_not_rungs() -> None:
    """A rung nobody can be tested on would read as a failure, not a gap."""
    ladder = Ladder.build(NORSK, {"A", "C", "D"})
    assert [r.goal_set.code for r in ladder.rungs] == ["A", "C", "D"]
    # And the indices close up, so the walk sees no hole.
    assert [r.index for r in ladder.rungs] == [0, 1, 2]


def test_empty_ladder_is_falsy() -> None:
    assert not Ladder.build(NORSK, set())


def test_spans_one_year_distinguishes_matematikk_from_the_rest() -> None:
    ladder = Ladder.build(NORSK, ALL)
    assert not any(r.spans_one_year for r in ladder.rungs)

    single = Subject(
        code="MAT01-06",
        title=LocalisedText(by_language={"nob": "Matematikk"}),
        goal_sets=(goal_set("A", 5, (5,)),),
    )
    assert Ladder.build(single, {"A"}).rungs[0].spans_one_year


def test_index_for_grade_uses_udirs_own_year_mapping() -> None:
    ladder = Ladder.build(NORSK, ALL)
    assert ladder.index_for_grade(1) == 0
    assert ladder.index_for_grade(4) == 1
    assert ladder.index_for_grade(6) == 2
    assert ladder.index_for_grade(10) == 3


def test_index_for_grade_reports_the_naturfag_hole_rather_than_papering_over_it() -> None:
    ladder = Ladder.build(NATURFAG, ALL)
    assert ladder.index_for_grade(3) is None
    assert ladder.index_for_grade(2) == 0
    assert ladder.index_for_grade(4) == 1


def test_start_index_is_one_rung_below_the_pupils_own() -> None:
    ladder = Ladder.build(NORSK, ALL)
    assert ladder.start_index(6) == 1  # own rung is 2 (etter 7.)
    assert ladder.start_index(10) == 2


def test_start_index_floors_at_the_bottom_of_the_ladder() -> None:
    """A 1st-grader has nothing below them; the test must not start at -1."""
    ladder = Ladder.build(NORSK, ALL)
    assert ladder.start_index(1) == 0
    assert ladder.start_index(2) == 0


def test_unknown_grade_starts_at_the_bottom() -> None:
    assert Ladder.build(NORSK, ALL).start_index(None) == 0


def test_start_index_handles_a_grade_that_falls_in_a_hole() -> None:
    """A 3rd-grader in naturfag still has to be able to start somewhere."""
    ladder = Ladder.build(NATURFAG, ALL)
    assert ladder.start_index(3) == 0


def test_every_real_subject_builds_a_usable_ladder() -> None:
    """Guards the premise the nivåtest rests on: every checkpoint has a quiz.

    If an ingest or an item-file rename breaks that, the ladder silently gets
    shorter and a ceiling starts meaning something different. Better a red build.
    """
    catalogue = Catalogue.load()
    bank = ItemBank.load()
    for code in ("MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04"):
        subject = catalogue.subject(code)
        assert subject is not None
        servable = {gs.code for gs in subject.goal_sets if bank.has_quiz(gs.code)}
        ladder = Ladder.build(subject, servable)
        assert len(ladder) == len(subject.goal_sets), f"{code} lost a rung"
        assert len(ladder) >= 3
