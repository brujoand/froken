"""Assertions about the committed items, and the flow that serves them.

These run against `data/items/`, so they fail when a hand-edit or a curriculum
revision breaks the authored content -- not merely when the code changes.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from froken.catalogue.loader import Catalogue
from froken.items.loader import ItemBank
from froken.items.validate import validate
from froken.web.app import create_app
from froken.web.routes import CORE_SUBJECTS


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


def test_subject_without_items_offers_no_quiz(client: TestClient) -> None:
    """Better to say a quiz is coming than to show a button that 404s."""
    body = text_of(client.get("/nb/klasse/10/NOR01-08").text)
    assert "Quiz kommer snart" in body
    assert client.post("/nb/klasse/10/NOR01-08/quiz").status_code == 404


def test_quiz_questions_are_marked_as_ours(client: TestClient) -> None:
    """NLOD forbids presenting Udir's data misleadingly, so authored questions
    must never read as curriculum text."""
    start = client.post("/nb/klasse/2/MAT01-06/quiz", follow_redirects=False)
    session_id = start.headers["location"].rsplit("/", 1)[-1]
    body = text_of(client.get(f"/nb/quiz/{session_id}").text)
    assert "laget av Frøken" in body
