"""The bracket search behind the nivåtest.

The claim this module makes about a child -- "you are working at 4.-trinn level"
-- is the strongest thing Pensum says to anyone, so the tests are mostly about
the ways it must refuse to say it: when the budget ran out mid-climb, when the
pupil cleared nothing, when the ladder simply ended.

The simulation at the bottom is the load-bearing one. It plays every possible
pupil against every real subject and asserts the walk both terminates and lands
on the right rung.
"""

from __future__ import annotations

import pytest

from pensum.catalogue.loader import Catalogue
from pensum.domain.ladder import Ladder
from pensum.domain.models import GoalSet, LocalisedText, Subject
from pensum.items.loader import ItemBank
from pensum.quiz.placement import (
    DEEPEN_SIZE,
    MAX_ITEMS,
    PROBE_SIZE,
    Placement,
    Verdict,
    verdict,
)


def ladder_of(size: int) -> Ladder:
    subject = Subject(
        code="TST01-01",
        title=LocalisedText(by_language={"nob": "Test"}),
        goal_sets=tuple(
            GoalSet(
                code=f"KV{i}",
                title=LocalisedText(by_language={"nob": f"Etter {i + 2}. trinn"}),
                after_year=i + 2,
                applies_to_years=(i + 2,),
                goals=(),
            )
            for i in range(size)
        ),
    )
    return Ladder.build(subject, {f"KV{i}" for i in range(size)})


# --- grading one block ---------------------------------------------------


@pytest.mark.parametrize(
    ("correct", "expected"),
    [
        (5, Verdict.MASTERED),
        (4, Verdict.MASTERED),
        (3, Verdict.FRONTIER),
        (2, Verdict.BELOW),
        (0, Verdict.BELOW),
    ],
)
def test_block_thresholds(correct: int, expected: Verdict) -> None:
    assert verdict(correct, PROBE_SIZE) is expected


def test_mastery_is_a_stronger_claim_than_its_opposite() -> None:
    """Deliberately asymmetric: 4/5 climbs, but it takes 2/5 to descend."""
    assert verdict(4, 5) is Verdict.MASTERED
    assert verdict(3, 5) is Verdict.FRONTIER


def test_an_abandoned_block_does_not_crash() -> None:
    assert verdict(0, 0) is Verdict.BELOW


# --- the walk ------------------------------------------------------------


def test_it_starts_one_rung_below_the_pupils_own() -> None:
    ladder = ladder_of(5)
    step = Placement.begin(ladder, grade=6).next_step()
    assert step is not None
    assert step.rung.index == ladder.start_index(6)


def test_mastering_a_rung_jumps_to_the_middle_of_what_is_left() -> None:
    """Not the next rung up -- the middle of the unknown span above it.

    A one-rung step cannot cross matematikk's nine rungs inside the budget,
    which is the whole reason the walk bisects rather than climbs.
    """
    run = Placement.begin(ladder_of(5), grade=None).record(0, 5, 5)
    step = run.next_step()
    assert step is not None and step.rung.index == 3


def test_failing_a_rung_searches_below_it() -> None:
    run = Placement.begin(ladder_of(5), grade=6).record(2, 1, 5)
    step = run.next_step()
    assert step is not None and step.rung.index < 2


def test_a_closed_bracket_stops_the_search_and_deepens_the_frontier() -> None:
    """Cleared 0, failed 1: there is nothing between them left to ask about."""
    run = Placement.begin(ladder_of(5), grade=None).record(0, 5, 5).record(1, 0, 5)
    assert run.search_closed
    step = run.next_step()
    assert step is not None
    assert step.rung.index == 1
    assert step.deepening


def test_a_middling_block_does_not_by_itself_prove_the_rungs_below_it() -> None:
    """3/5 at rung 1 bounds the search from above; it does not close it.

    Treating a half-answered block as "you are here" would place a pupil who
    guessed their way to three marks a full rung above where they can work, so
    the search keeps going downwards until something is actually cleared.
    """
    run = Placement.begin(ladder_of(5), grade=None).record(1, 3, 5)
    assert not run.search_closed
    step = run.next_step()
    assert step is not None and step.rung.index == 0 and not step.deepening


def test_a_rung_is_deepened_only_once() -> None:
    """Otherwise a stubborn frontier absorbs the whole budget on one rung."""
    run = Placement.begin(ladder_of(5), grade=None).record(0, 5, 5).record(1, 3, 5)
    assert run.search_closed
    assert run.next_step().deepening
    run = run.record(1, 3, 5)
    assert run.next_step() is None


def test_deepening_pools_both_blocks_rather_than_reading_only_the_first() -> None:
    """Otherwise the deepening block costs five questions and changes nothing."""
    run = Placement.begin(ladder_of(5), grade=None).record(1, 3, 5).record(1, 5, 5)
    assert run._verdict_at(1) is Verdict.MASTERED
    assert run.outcome().ceiling is not None
    assert run.outcome().ceiling.index == 1


def test_deepening_is_skipped_when_the_budget_cannot_fund_a_full_block() -> None:
    run = Placement.begin(ladder_of(9), grade=None)
    for rung in range(MAX_ITEMS // PROBE_SIZE - 1):
        run = run.record(rung, 5, 5)
    run = run.record(4, 3, 5)  # frontier, and now at the budget
    assert run.spent == MAX_ITEMS
    assert run.next_step() is None


# --- what the run is allowed to claim ------------------------------------


def test_clearing_nothing_is_not_reported_as_level_zero() -> None:
    run = Placement.begin(ladder_of(5), grade=None).record(0, 0, 5).record(0, 0, 5)
    outcome = run.outcome()
    assert outcome.ceiling is None
    assert outcome.frontier is not None and outcome.frontier.index == 0
    assert not outcome.conclusive


def test_topping_out_reports_no_frontier_and_is_still_conclusive() -> None:
    ladder = ladder_of(3)
    run = Placement.begin(ladder, grade=None).record(0, 5, 5).record(1, 5, 5).record(2, 5, 5)
    outcome = run.outcome()
    assert outcome.ceiling is not None and outcome.ceiling.index == ladder.top
    assert outcome.frontier is None
    assert outcome.topped_out
    assert outcome.conclusive
    assert not outcome.bracketed
    assert run.next_step() is None


def test_running_out_of_budget_mid_climb_is_not_conclusive() -> None:
    """Five mastered rungs in a row is not a ceiling; it is an unfinished climb."""
    run = Placement.begin(ladder_of(9), grade=None)
    for rung in range(MAX_ITEMS // PROBE_SIZE):
        run = run.record(rung, 5, 5)
    assert run.next_step() is None
    outcome = run.outcome()
    assert outcome.ceiling is not None and outcome.ceiling.index == 4
    assert outcome.frontier is None
    assert not outcome.topped_out
    assert not outcome.conclusive


def test_an_empty_ladder_asks_nothing() -> None:
    empty = Ladder(subject="TST01-01", rungs=())
    assert Placement.begin(empty, grade=5).next_step() is None


# --- the property that matters -------------------------------------------


def simulate(ladder: Ladder, true_ceiling: int | None, grade: int | None) -> Placement:
    """Play a pupil whose real ceiling is `true_ceiling` (None = below rung 0).

    Deterministic on purpose: the point is to test the search, not to model
    guessing. Rungs at or below the ceiling come back 5/5, the rung just above
    comes back 3/5 -- the honest "half of it" a real frontier looks like -- and
    anything higher comes back 0/5.
    """
    run = Placement.begin(ladder, grade)
    for _ in range(50):  # generous; a failure to terminate should trip the assert
        step = run.next_step()
        if step is None:
            return run
        i = step.rung.index
        if true_ceiling is not None and i <= true_ceiling:
            correct = step.count
        elif i == (0 if true_ceiling is None else true_ceiling + 1):
            correct = round(step.count * 0.6)
        else:
            correct = 0
        run = run.record(i, correct, step.count)
    pytest.fail(f"walk did not terminate on {ladder.subject} for ceiling {true_ceiling}")


def real_ladders() -> list[Ladder]:
    catalogue = Catalogue.load()
    bank = ItemBank.load()
    ladders = []
    for code in ("MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04"):
        subject = catalogue.subject(code)
        assert subject is not None
        servable = {gs.code for gs in subject.goal_sets if bank.has_quiz(gs.code)}
        ladders.append(Ladder.build(subject, servable))
    return ladders


@pytest.mark.parametrize("ladder", real_ladders(), ids=lambda lad: lad.subject)
def test_every_pupil_on_every_real_ladder_terminates_within_budget(ladder: Ladder) -> None:
    for ceiling in [None, *range(len(ladder))]:
        for grade in [None, 1, 3, 5, 7, 10]:
            run = simulate(ladder, ceiling, grade)
            assert run.spent <= MAX_ITEMS, f"{ladder.subject} overspent for ceiling {ceiling}"


@pytest.mark.parametrize("ladder", real_ladders(), ids=lambda lad: lad.subject)
def test_a_conclusive_run_lands_on_the_true_ceiling(ladder: Ladder) -> None:
    """Where the budget reaches, the answer has to be right -- not merely close."""
    for ceiling in [None, *range(len(ladder))]:
        for grade in [None, 1, 3, 5, 7, 10]:
            outcome = simulate(ladder, ceiling, grade).outcome()
            if not outcome.conclusive:
                continue
            found = outcome.ceiling.index if outcome.ceiling else None
            assert found == ceiling, (
                f"{ladder.subject}: pupil at {ceiling} (grade {grade}) placed at {found}"
            )


@pytest.mark.parametrize("ladder", real_ladders(), ids=lambda lad: lad.subject)
def test_starting_near_the_pupils_own_grade_is_cheaper_than_starting_at_the_bottom(
    ladder: Ladder,
) -> None:
    """The whole justification for asking which klasse they are in.

    Only checked on the long ladder, since it is the only one where the saving
    can exist: with four rungs the climb from the bottom is short anyway.
    """
    if len(ladder) < 6:
        pytest.skip("ladder too short for the start rung to matter")
    top = len(ladder) - 1
    grade = ladder[top].after_year
    assert simulate(ladder, top, grade).spent < simulate(ladder, top, None).spent


def test_deepening_buys_extra_items_at_the_frontier() -> None:
    run = simulate(ladder_of(4), true_ceiling=1, grade=None)
    at_frontier = [p for p in run.probes if p.rung == 2]
    assert len(at_frontier) == 2
    assert sum(p.total for p in at_frontier) == PROBE_SIZE + DEEPEN_SIZE


@pytest.mark.parametrize("ladder", real_ladders(), ids=lambda lad: lad.subject)
def test_every_pupil_who_clears_anything_is_placed_within_budget(ladder: Ladder) -> None:
    """The guarantee the feature rests on, asserted rather than hoped for.

    Every real subject, every possible true ceiling, every starting grade: if
    the pupil clears so much as the bottom rung, the run finds their level
    without running out of questions. This is the test that failed for
    matematikk under a one-rung-at-a-time walk, and it is why the search
    bisects.

    Pupils who clear nothing are excluded on purpose -- "no ceiling found" is a
    correct answer, not a failure to converge, and it is asserted separately in
    `test_clearing_nothing_is_not_reported_as_level_zero`.
    """
    for ceiling in range(len(ladder)):
        for grade in [None, 1, 3, 5, 7, 10]:
            outcome = simulate(ladder, ceiling, grade).outcome()
            assert outcome.conclusive, (
                f"{ladder.subject}: pupil at rung {ceiling} (grade {grade}) "
                f"ran out after {outcome.items_spent} items"
            )
            assert outcome.ceiling is not None and outcome.ceiling.index == ceiling
