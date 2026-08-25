"""A nivåtest as a sitting: drawing blocks, answering them, advancing the search.

The interesting failures here are not arithmetic. They are a question asked
twice because the frontier got deepened, a rung scored as failed because the
bank could not fill it, and a run that keeps going after the bank is dry -- each
of which reaches the pupil as a wrong statement about what they can do.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from pensum.auth.models import User
from pensum.catalogue.loader import Catalogue
from pensum.domain.ladder import Ladder
from pensum.items.loader import ItemBank
from pensum.items.schema import AuthoredText, Choice, QuizItem
from pensum.quiz.placement import MAX_ITEMS, PROBE_SIZE
from pensum.quiz.run import PlacementRun
from pensum.quiz.scoring import select
from pensum.quiz.session import SessionStore

NOW = datetime(2026, 8, 25, 9, 0, tzinfo=UTC)


def item(id_: str, goal: str, *, right: str = "a") -> QuizItem:
    def text(s: str) -> AuthoredText:
        return AuthoredText(nb=s, en=s)

    return QuizItem(
        id=id_,
        goal=goal,
        type="multiple_choice",
        difficulty=1,
        prompt=text(id_),
        explanation=text("because"),
        choices=(
            Choice(id="a", text=text("a"), correct=right == "a"),
            Choice(id="b", text=text("b"), correct=right == "b"),
            Choice(id="c", text=text("c"), correct=right == "c"),
        ),
        reviewed=True,
    )


def bank_of(per_rung: int, rungs: int = 4) -> dict[str, list[QuizItem]]:
    return {
        f"KV{r}": [item(f"KV{r}-{n}", f"KM{r}{n % 3}") for n in range(per_rung)]
        for r in range(rungs)
    }


def drawer(bank: dict[str, list[QuizItem]]):
    def draw(goal_set: str, count: int, exclude: set[str]) -> list[QuizItem]:
        return select([i for i in bank.get(goal_set, []) if i.id not in exclude], count, seed=1)

    return draw


def ladder_of(rungs: int = 4) -> Ladder:
    from pensum.domain.models import GoalSet, LocalisedText, Subject

    subject = Subject(
        code="TST01-01",
        title=LocalisedText(by_language={"nob": "Test"}),
        goal_sets=tuple(
            GoalSet(
                code=f"KV{r}",
                title=LocalisedText(by_language={"nob": f"Etter {r + 2}. trinn"}),
                after_year=r + 2,
                applies_to_years=(r + 2,),
                goals=(),
            )
            for r in range(rungs)
        ),
    )
    return Ladder.build(subject, {f"KV{r}" for r in range(rungs)})


def right_answer(item: QuizItem) -> str:
    """A response the item grades as correct, whatever kind it is.

    The real banks hold numeric and short_text items as well as multiple choice,
    so a simulation that only knows about choices silently stops exercising two
    thirds of the schema.
    """
    if item.type == "multiple_choice":
        return next(c.id for c in item.choices if c.correct)
    if item.type == "numeric":
        return str(item.answer)
    return next(c[0] for c in item.accept.values() if c)


def wrong_answer(item: QuizItem) -> str:
    """A response the item grades as incorrect. Asserted, not assumed."""
    if item.type == "numeric":
        return str(float(item.answer) + max(1.0, item.tolerance * 2 + 1))
    if item.type == "multiple_choice":
        return next(c.id for c in item.choices if not c.correct)
    return "definitely not the answer"


def play(run: PlacementRun, draw, *, correct_up_to: int) -> PlacementRun:
    """Answer everything, getting rungs at or below `correct_up_to` right."""
    for _ in range(200):
        current = run.current()
        if current is None:
            return run
        block = run.block
        assert block is not None
        if block.rung <= correct_up_to:
            response = right_answer(current)
            assert current.is_correct(response)
        else:
            response = wrong_answer(current)
            assert not current.is_correct(response)
        run.answer(current.id, response, draw)
    pytest.fail("run did not finish")


# --- drawing --------------------------------------------------------------


def test_a_run_starts_with_a_block_in_hand() -> None:
    bank = bank_of(20)
    run = PlacementRun.begin("TST01-01", ladder_of(), 4, drawer(bank), NOW)
    assert run.block is not None
    assert len(run.block.items) == PROBE_SIZE
    assert run.current() is not None


def test_no_question_is_asked_twice_in_one_run() -> None:
    """The frontier gets deepened, so the second block there must not repeat."""
    bank = bank_of(20)
    draw = drawer(bank)
    run = play(PlacementRun.begin("TST01-01", ladder_of(), None, draw, NOW), draw, correct_up_to=1)
    served = [i.id for b in run.blocks for i in b.items]
    assert len(served) == len(set(served))


def test_the_deepening_block_at_the_frontier_is_fresh() -> None:
    bank = bank_of(20)
    draw = drawer(bank)
    run = play(PlacementRun.begin("TST01-01", ladder_of(), None, draw, NOW), draw, correct_up_to=1)
    deepened = [b for b in run.blocks if b.deepening]
    assert deepened, "expected the frontier to be deepened"
    first = next(b for b in run.blocks if b.rung == deepened[0].rung and not b.deepening)
    assert not ({i.id for i in first.items} & {i.id for i in deepened[0].items})


def test_a_rung_the_bank_cannot_fill_ends_the_run_rather_than_failing_the_pupil() -> None:
    """Scoring an empty block would report a rung they never saw as their ceiling."""
    bank = bank_of(20)
    bank["KV3"] = []
    draw = drawer(bank)
    run = play(PlacementRun.begin("TST01-01", ladder_of(), None, draw, NOW), draw, correct_up_to=3)
    assert all(b.rung != 3 for b in run.blocks)
    assert not any(p.rung == 3 for p in run.placement.probes)
    assert run.finished


def test_a_dry_bank_does_not_spin() -> None:
    run = PlacementRun.begin("TST01-01", ladder_of(), None, drawer({}), NOW)
    assert run.blocks == []
    assert run.finished
    assert run.current() is None


def test_a_short_block_is_scored_on_what_was_actually_asked() -> None:
    """Thin banks are the norm, so a four-item block must not be graded out of five."""
    bank = bank_of(20)
    bank["KV1"] = bank["KV1"][:2]
    draw = drawer(bank)
    run = PlacementRun.begin("TST01-01", ladder_of(), 3, draw, NOW)
    while run.block is not None and run.block.rung == run.blocks[0].rung:
        current = run.current()
        if current is None:
            break
        run.answer(current.id, right_answer(current), draw)
    first = run.placement.probes[0]
    assert first.total == len(run.blocks[0].items)


# --- answering ------------------------------------------------------------


def test_re_answering_is_refused() -> None:
    bank = bank_of(20)
    draw = drawer(bank)
    run = PlacementRun.begin("TST01-01", ladder_of(), 4, draw, NOW)
    first = run.current()
    assert first is not None
    assert run.answer(first.id, "a", draw) is not None
    assert run.answer(first.id, "b", draw) is None


def test_an_unknown_item_is_refused() -> None:
    bank = bank_of(20)
    draw = drawer(bank)
    run = PlacementRun.begin("TST01-01", ladder_of(), 4, draw, NOW)
    assert run.answer("not-a-real-id", "a", draw) is None


def test_a_run_never_exceeds_the_item_budget() -> None:
    bank = bank_of(40)
    draw = drawer(bank)
    run = play(PlacementRun.begin("TST01-01", ladder_of(9), None, draw, NOW), draw, correct_up_to=8)
    assert run.asked <= MAX_ITEMS


# --- what it reports ------------------------------------------------------


def test_the_tally_spans_rungs_not_just_the_frontier() -> None:
    bank = bank_of(20)
    draw = drawer(bank)
    run = play(PlacementRun.begin("TST01-01", ladder_of(), None, draw, NOW), draw, correct_up_to=1)
    goals_asked = {i.goal for b in run.blocks for i in b.items}
    assert set(run.tally()) == goals_asked
    assert run.gaps(), "a pupil who failed a rung should have gaps to show"


def test_the_tally_ignores_unanswered_questions() -> None:
    bank = bank_of(20)
    draw = drawer(bank)
    run = PlacementRun.begin("TST01-01", ladder_of(), 4, draw, NOW)
    first = run.current()
    assert first is not None
    run.answer(first.id, "a", draw)
    assert sum(total for _, total in run.tally().values()) == 1


def test_attribution_is_captured_at_the_start() -> None:
    bank = bank_of(20)
    user = User(sub="abc", name="Kari")
    run = PlacementRun.begin("TST01-01", ladder_of(), 4, drawer(bank), NOW, user=user)
    assert run.attributed and run.user_name == "Kari"
    assert not PlacementRun.begin("TST01-01", ladder_of(), 4, drawer(bank), NOW).attributed


# --- storage --------------------------------------------------------------


def test_the_store_holds_a_placement_run_and_expires_it() -> None:
    """The expiry sweep must not be implemented twice; that is why the store is duck-typed."""
    store = SessionStore()
    run = PlacementRun.begin("TST01-01", ladder_of(), 4, drawer(bank_of(20)), NOW)
    store.put(run, NOW)
    assert store.get(run.id, NOW) is run
    assert store.get(run.id, NOW + timedelta(hours=3)) is None
    assert len(store) == 0


# --- against the real bank ------------------------------------------------


def real_ladders() -> list[tuple[Ladder, ItemBank]]:
    catalogue = Catalogue.load()
    bank = ItemBank.load()
    out = []
    for code in ("MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04"):
        subject = catalogue.subject(code)
        assert subject is not None
        servable = {gs.code for gs in subject.goal_sets if bank.has_quiz(gs.code)}
        out.append((Ladder.build(subject, servable), bank))
    return out


@pytest.mark.parametrize(
    "ladder,bank", real_ladders(), ids=[lad.subject for lad, _ in real_ladders()]
)
def test_the_real_banks_can_actually_carry_a_run(ladder: Ladder, bank: ItemBank) -> None:
    """The banks are shallow. A run must still finish without repeating itself.

    This is the test that would go red if authoring thinned a checkpoint further,
    which matters more than it looks: the failure mode is not an exception, it is
    a pupil quietly being placed on less evidence than the search asked for.
    """

    def draw(goal_set: str, count: int, exclude: set[str]) -> list[QuizItem]:
        return select([i for i in bank.for_goal_set(goal_set) if i.id not in exclude], count)

    for ceiling in range(len(ladder)):
        for grade in (None, 2, 5, 10):
            run = play(
                PlacementRun.begin(ladder.subject, ladder, grade, draw, NOW),
                draw,
                correct_up_to=ceiling,
            )
            served = [i.id for b in run.blocks for i in b.items]
            assert len(served) == len(set(served)), f"{ladder.subject} repeated a question"
            assert run.asked <= MAX_ITEMS
            assert run.finished
