"""Item schema, grading, selection and scoring."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from froken.items.schema import AuthoredText, Choice, ItemSet, QuizItem
from froken.quiz.scoring import PASS_THRESHOLD, score, select
from froken.quiz.session import SESSION_TTL, SessionStore

NOW = datetime(2026, 7, 19, 10, 0, tzinfo=UTC)


def text(value: str = "x") -> AuthoredText:
    return AuthoredText(nb=value, en=value)


def mc(item_id: str = "KM1-01", goal: str = "KM1", correct_id: str = "a") -> QuizItem:
    return QuizItem(
        id=item_id,
        goal=goal,
        type="multiple_choice",
        difficulty=1,
        prompt=text(),
        explanation=text(),
        choices=tuple(Choice(id=c, text=text(c), correct=c == correct_id) for c in "abc"),
    )


def numeric(answer: float, tolerance: float = 0.0, goal: str = "KM1") -> QuizItem:
    return QuizItem(
        id=f"{goal}-n",
        goal=goal,
        type="numeric",
        difficulty=1,
        prompt=text(),
        explanation=text(),
        answer=answer,
        tolerance=tolerance,
    )


# Schema -------------------------------------------------------------------


def test_multiple_choice_needs_exactly_one_correct_answer() -> None:
    with pytest.raises(ValidationError, match="exactly one correct"):
        QuizItem(
            id="x",
            goal="KM1",
            type="multiple_choice",
            difficulty=1,
            prompt=text(),
            explanation=text(),
            choices=tuple(Choice(id=c, text=text(), correct=True) for c in "abc"),
        )


def test_multiple_choice_needs_enough_choices() -> None:
    with pytest.raises(ValidationError, match="at least"):
        QuizItem(
            id="x",
            goal="KM1",
            type="multiple_choice",
            difficulty=1,
            prompt=text(),
            explanation=text(),
            choices=(Choice(id="a", text=text(), correct=True), Choice(id="b", text=text())),
        )


def test_numeric_needs_an_answer() -> None:
    with pytest.raises(ValidationError, match="need an answer"):
        QuizItem(
            id="x", goal="KM1", type="numeric", difficulty=1, prompt=text(), explanation=text()
        )


def test_short_text_needs_accepted_answers() -> None:
    """Grading is offline exact matching, so without a list nothing can be right."""
    with pytest.raises(ValidationError, match="accepted answers"):
        QuizItem(
            id="x", goal="KM1", type="short_text", difficulty=1, prompt=text(), explanation=text()
        )


def test_bokmaal_is_required_on_authored_text() -> None:
    with pytest.raises(ValidationError):
        AuthoredText(en="English only")


def test_duplicate_item_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate item ids"):
        ItemSet(subject="MAT01-06", goal_set="KV1021", items=(mc("dup"), mc("dup")))


# Grading ------------------------------------------------------------------


def test_multiple_choice_grading() -> None:
    item = mc(correct_id="b")
    assert item.is_correct("b") is True
    assert item.is_correct("a") is False
    assert item.is_correct("") is False


def test_numeric_accepts_a_norwegian_decimal_comma() -> None:
    """Norwegian pupils write 3,5 -- marking that wrong would be a bug about us."""
    item = numeric(3.5)
    assert item.is_correct("3,5") is True
    assert item.is_correct("3.5") is True


def test_numeric_respects_tolerance() -> None:
    item = numeric(10, tolerance=0.5)
    assert item.is_correct("10.4") is True
    assert item.is_correct("11") is False


def test_numeric_rejects_nonsense() -> None:
    assert numeric(7).is_correct("sju") is False


def test_short_text_ignores_case_spacing_and_final_punctuation() -> None:
    item = QuizItem(
        id="x",
        goal="KM1",
        type="short_text",
        difficulty=1,
        prompt=text(),
        explanation=text(),
        accept={"nb": ("trekant",), "en": ("triangle",)},
    )
    for answer in ["trekant", "Trekant", "  TREKANT  ", "trekant."]:
        assert item.is_correct(answer) is True, answer
    assert item.is_correct("firkant") is False


def test_short_text_accepts_either_language() -> None:
    """A bilingual child should not be marked wrong for answering in English."""
    item = QuizItem(
        id="x",
        goal="KM1",
        type="short_text",
        difficulty=1,
        prompt=text(),
        explanation=text(),
        accept={"nb": ("trekant",), "en": ("triangle",)},
    )
    assert item.is_correct("triangle") is True


# Selection ----------------------------------------------------------------


def test_selection_spreads_across_goals_before_repeating_one() -> None:
    """One well-covered goal must not crowd out the rest of the checkpoint.

    Five goals, four items each, ten questions: every goal should appear, or a
    quiz claiming to cover "2. klasse" would really be testing two of its goals.
    """
    items = [mc(f"KM{g}-{i}", goal=f"KM{g}") for g in range(5) for i in range(4)]
    selected = select(items, count=10, seed=1)
    assert len(selected) == 10
    assert len({i.goal for i in selected}) == 5


def test_selection_is_reproducible_for_a_given_seed() -> None:
    items = [mc(f"KM{g}-{i}", goal=f"KM{g}") for g in range(5) for i in range(4)]
    assert [i.id for i in select(items, 10, seed=7)] == [i.id for i in select(items, 10, seed=7)]


def test_selection_copes_with_fewer_items_than_asked_for() -> None:
    assert len(select([mc("a", goal="KM1"), mc("b", goal="KM2")], count=10, seed=1)) == 2


def test_selection_of_nothing_is_empty_not_an_error() -> None:
    assert select([], count=10) == []


# Sessions and scoring -----------------------------------------------------


def build_session(store: SessionStore, items: list[QuizItem]):
    return store.create("MAT01-06", "KV1021", 2, items, NOW)


def test_answering_advances_and_finishes() -> None:
    store = SessionStore()
    session = build_session(store, [mc("a", goal="KM1"), mc("b", goal="KM2")])

    assert session.current().id == "a"
    session.answer("a", "a")
    assert session.current().id == "b"
    session.answer("b", "a")
    assert session.finished is True
    assert session.current() is None


def test_re_answering_is_refused() -> None:
    """The score should reflect the first attempt, not the one after the
    explanation was read."""
    store = SessionStore()
    session = build_session(store, [mc("a", goal="KM1", correct_id="a")])

    assert session.answer("a", "b") is not None
    assert session.answer("a", "a") is None
    assert score(session).correct == 0


def test_unknown_item_is_refused() -> None:
    store = SessionStore()
    session = build_session(store, [mc("a", goal="KM1")])
    assert session.answer("nope", "a") is None


def test_expired_sessions_are_dropped() -> None:
    store = SessionStore()
    session = build_session(store, [mc("a", goal="KM1")])

    assert store.get(session.id, NOW) is not None
    assert store.get(session.id, NOW + SESSION_TTL + timedelta(minutes=1)) is None
    assert len(store) == 0


def test_scoring_breaks_results_down_by_goal() -> None:
    """The per-goal breakdown is the useful half: "62%" tells a pupil nothing
    they can act on."""
    store = SessionStore()
    session = build_session(
        store,
        [
            mc("a1", goal="KM1", correct_id="a"),
            mc("a2", goal="KM1", correct_id="a"),
            mc("b1", goal="KM2", correct_id="a"),
        ],
    )
    session.answer("a1", "a")
    session.answer("a2", "a")
    session.answer("b1", "c")

    result = score(session)
    assert (result.correct, result.total) == (2, 3)
    assert [g.goal for g in result.strong] == ["KM1"]
    assert [g.goal for g in result.needs_practice] == ["KM2"]


def test_pass_threshold() -> None:
    store = SessionStore()
    items = [mc(f"i{i}", goal=f"KM{i}", correct_id="a") for i in range(10)]
    session = build_session(store, items)
    for index, item in enumerate(items):
        session.answer(item.id, "a" if index < 7 else "b")

    result = score(session)
    assert result.percentage == 70
    assert result.share >= PASS_THRESHOLD
    assert result.passed is True


def test_unanswered_items_do_not_count_toward_the_total() -> None:
    """An abandoned quiz should not read as a pile of wrong answers."""
    store = SessionStore()
    session = build_session(store, [mc("a", goal="KM1", correct_id="a"), mc("b", goal="KM2")])
    session.answer("a", "a")

    result = score(session)
    assert (result.correct, result.total) == (1, 1)
    assert result.percentage == 100


# Showing the answer back --------------------------------------------------


def test_multiple_choice_echoes_the_choice_text_not_its_id() -> None:
    """A child who picked "En bok" must not be told they answered "b"."""
    item = QuizItem(
        id="x",
        goal="KM1",
        type="multiple_choice",
        difficulty=1,
        prompt=text(),
        explanation=text(),
        choices=(
            Choice(id="a", text=AuthoredText(nb="En bok", en="A book")),
            Choice(id="b", text=AuthoredText(nb="En mynt", en="A coin"), correct=True),
        )
        + (Choice(id="c", text=text("c")),),
    )
    assert item.response_text("a") == "En bok"
    assert item.response_text("a", "en") == "A book"
    assert item.correct_text() == "En mynt"


def test_unknown_choice_id_falls_back_to_the_raw_value() -> None:
    assert mc().response_text("zzz") == "zzz"


def test_numeric_answers_display_without_a_spurious_decimal() -> None:
    """A child asked for a whole number should not see "7.0" as the answer."""
    assert numeric(7).correct_text() == "7"
    assert numeric(3.5).correct_text() == "3,5"


def test_short_text_shows_the_first_accepted_answer() -> None:
    item = QuizItem(
        id="x",
        goal="KM1",
        type="short_text",
        difficulty=1,
        prompt=text(),
        explanation=text(),
        accept={"nb": ("trekant", "tre kant"), "en": ("triangle",)},
    )
    assert item.correct_text() == "trekant"
    assert item.response_text("  Trekant ") == "Trekant"
