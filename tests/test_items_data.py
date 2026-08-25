"""Assertions about the committed items, and the flow that serves them.

These run against `data/items/`, so they fail when a hand-edit or a curriculum
revision breaks the authored content -- not merely when the code changes.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from pensum.catalogue.loader import Catalogue
from pensum.items.loader import ItemBank
from pensum.items.validate import validate
from pensum.web.app import create_app
from pensum.web.routes import CORE_SUBJECTS


@pytest.fixture(scope="module")
def bank() -> ItemBank:
    return ItemBank.load()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Catalogue.load(), ItemBank.load()))


def text_of(html: str) -> str:
    body = html[html.find("<main>") : html.find("</main>")] if "<main>" in html else html
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def test_committed_items_validate_against_the_catalogue() -> None:
    """Catches an orphaned goal code, which is otherwise silent at runtime."""
    assert validate() == []


def test_core_subjects_all_exist(bank: ItemBank) -> None:
    """CORE_SUBJECTS carries curriculum revisions, so it goes stale on a revision.

    A stale code empties the grade pages without erroring, so the staleness is
    made a CI failure instead of a support question.
    """
    catalogue = Catalogue.load()
    missing = [code for code in CORE_SUBJECTS if catalogue.subject(code) is None]
    assert not missing, f"CORE_SUBJECTS references curricula not in the catalogue: {missing}"


def test_every_goal_is_tested_or_explicitly_excused(bank: ItemBank) -> None:
    """Silence about a goal is indistinguishable from having forgotten it."""
    catalogue = Catalogue.load()
    for item_set in bank.item_sets:
        goal_set = catalogue.subject(item_set.subject).goal_set(item_set.goal_set)
        accounted = item_set.goals_covered | item_set.goals_excused
        assert {g.code for g in goal_set.goals} <= accounted


def test_not_assessable_entries_give_a_reason(bank: ItemBank) -> None:
    """An excused goal is a judgement someone made; it has to be readable."""
    for item_set in bank.item_sets:
        for excused in item_set.not_assessable:
            assert len(excused.reason.strip()) > 30, excused.goal


def test_unreviewed_items_are_withheld_by_default() -> None:
    """A merge alone must never put an unread question in front of a child."""
    strict = ItemBank.load()
    permissive = ItemBank.load(include_unreviewed=True)

    for item_set in strict.item_sets:
        served = strict.for_goal_set(item_set.goal_set)
        assert all(item.reviewed for item in served)
        assert len(served) <= len(permissive.for_goal_set(item_set.goal_set))


def test_quiz_runs_end_to_end(client: TestClient) -> None:
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    assert start.status_code == 303

    session_id = start.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/nb/quiz/{session_id}").status_code == 200

    session = client.app.state.sessions._sessions[session_id]
    for item in list(session.items):
        response = (
            next(c.id for c in item.choices if c.correct)
            if item.type == "multiple_choice"
            else str(item.answer)
        )
        feedback = client.post(
            f"/nb/quiz/{session_id}/answer", data={"item_id": item.id, "response": response}
        )
        assert feedback.status_code == 200

    result = client.get(f"/nb/quiz/{session_id}/result")
    assert result.status_code == 200
    body = text_of(result.text)
    assert "10 av 10" in body
    # Even a perfect score must not read as a verdict on the pupil.
    assert "ikke en vurdering" in body


def test_a_wrong_answer_names_the_goal_to_practise(client: TestClient) -> None:
    """The per-goal breakdown is the product; assert it actually points somewhere."""
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    session = client.app.state.sessions._sessions[session_id]

    missed = session.items[0]
    for item in list(session.items):
        if item is missed:
            wrong = (
                next(c.id for c in item.choices if not c.correct)
                if item.type == "multiple_choice"
                else "999999"
            )
            client.post(
                f"/nb/quiz/{session_id}/answer", data={"item_id": item.id, "response": wrong}
            )
        else:
            right = (
                next(c.id for c in item.choices if c.correct)
                if item.type == "multiple_choice"
                else str(item.answer)
            )
            client.post(
                f"/nb/quiz/{session_id}/answer", data={"item_id": item.id, "response": right}
            )

    body = text_of(client.get(f"/nb/quiz/{session_id}/result").text)
    goal_text = Catalogue.load().subject("MAT01-06").goal_sets[0].goal(missed.goal).text.get("nob")
    assert goal_text in body, "the missed goal's official wording should appear in the breakdown"


def test_answering_twice_conflicts(client: TestClient) -> None:
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    item = client.app.state.sessions._sessions[session_id].items[0]

    payload = {"item_id": item.id, "response": "a"}
    assert client.post(f"/nb/quiz/{session_id}/answer", data=payload).status_code == 200
    assert client.post(f"/nb/quiz/{session_id}/answer", data=payload).status_code == 409


def test_unknown_session_is_404(client: TestClient) -> None:
    assert client.get("/nb/quiz/does-not-exist").status_code == 404


def test_subject_without_items_offers_no_quiz() -> None:
    """Better to say a quiz is coming than to show a button that 404s.

    Uses an empty item bank rather than a real route: every checkpoint now has
    reviewed items, so the "coming soon" path can only be exercised against a
    catalogue with no items for the set.
    """
    empty = TestClient(create_app(Catalogue.load(), ItemBank([])))
    body = text_of(empty.get("/nb/klasse/2/MAT01-06").text)
    assert "Quiz kommer snart" in body
    assert empty.post("/nb/klasse/2/MAT01-06/quiz").status_code == 404


def test_quiz_questions_are_marked_as_ours(client: TestClient) -> None:
    """NLOD forbids presenting Udir's data misleadingly, so authored questions
    must never read as curriculum text."""
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    body = text_of(client.get(f"/nb/quiz/{session_id}").text)
    assert "laget av Pensum" in body


def test_a_wrong_answer_shows_what_was_given_and_what_was_right(client: TestClient) -> None:
    """Without both, a pupil cannot tell whether they misread, mistyped, or
    genuinely did not know."""
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    session = client.app.state.sessions._sessions[session_id]

    item = next(i for i in session.items if i.type == "multiple_choice")
    wrong = next(c for c in item.choices if not c.correct)

    body = client.post(
        f"/nb/quiz/{session_id}/answer", data={"item_id": item.id, "response": wrong.id}
    ).text

    assert "Du svarte" in body
    assert wrong.text.get("nb") in body, "the choice they picked should be shown, not its id"
    assert item.correct_text("nb") in body
    assert item.explanation.get("nb") in body


def test_a_correct_answer_does_not_belabour_it(client: TestClient) -> None:
    """No point echoing an answer back to someone who got it right."""
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    session = client.app.state.sessions._sessions[session_id]

    item = next(i for i in session.items if i.type == "multiple_choice")
    right = next(c for c in item.choices if c.correct)

    body = client.post(
        f"/nb/quiz/{session_id}/answer", data={"item_id": item.id, "response": right.id}
    ).text

    assert "Riktig!" in body
    assert "Du svarte" not in body


def test_goals_are_collapsible_on_the_subject_page(client: TestClient) -> None:
    """Twenty-odd goals shown at once bury everything below them."""
    body = client.get("/nb/klasse/2/MAT01-06").text
    assert "<details" in body and "<summary" in body


def test_the_quiz_call_to_action_precedes_the_goal_list(client: TestClient) -> None:
    """Collapsing the goals is what lets the quiz button surface; if the list
    came first that would be undone."""
    body = client.get("/nb/klasse/2/MAT01-06").text
    assert body.index('class="quiz-cta"') < body.index('class="goals"')


def test_subject_page_states_partial_coverage(client: TestClient) -> None:
    """Norsk 2. trinn tests a minority of its goals; the page must say so."""
    body = text_of(client.get("/nb/klasse/2/NOR01-08").text)
    assert "Quizen dekker 5 av 14" in body
    assert "Ikke i quizen" in body
    # The untestable goals are named as a category, not hidden behind the count.
    assert "kan ikke testes i en quiz" in body


def test_subject_page_marks_which_goals_are_in_the_quiz(client: TestClient) -> None:
    body = client.get("/nb/klasse/2/MAT01-06").text
    assert "goal--in-quiz" in body
    assert "goal--not-in-quiz" in body


def test_result_page_notes_partial_coverage(client: TestClient) -> None:
    """A score should read against what the quiz reached, not the whole trinn."""
    start = client.post("/nb/klasse/2/NOR01-08/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    session = client.app.state.sessions._sessions[session_id]
    for item in list(session.items):
        response = (
            next(c.id for c in item.choices if c.correct)
            if item.type == "multiple_choice"
            else str(item.answer)
        )
        client.post(
            f"/nb/quiz/{session_id}/answer", data={"item_id": item.id, "response": response}
        )

    body = text_of(client.get(f"/nb/quiz/{session_id}/result").text)
    assert "Quizen dekket" in body and "kompetansemål" in body
