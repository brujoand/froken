"""The nivåtest over HTTP, driven end to end against the real committed items.

The unit tests prove the search is right. These prove a pupil can actually reach
it: that the klasse hint is a hint and not a gate, that an adaptive test never
claims a length it has not decided, and that the result page says the true thing
in each of the three end states rather than rounding them all to a number.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from pensum.catalogue.loader import Catalogue
from pensum.i18n import translate
from pensum.items.loader import ItemBank
from pensum.quiz.placement import MAX_ITEMS
from pensum.quiz.run import PlacementRun
from pensum.web.app import create_app

SUBJECT = "MAT01-06"


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Catalogue.load()))


def text_of(html: str) -> str:
    body = html[html.find("<main>") : html.find("</main>")]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def start(client: TestClient, subject: str = SUBJECT, grade: str = "") -> str:
    """Begin a run and return its id."""
    response = client.post(f"/nb/nivatest/{subject}", data={"grade": grade}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers["location"].rsplit("/", 1)[-1]


def item_for(client: TestClient, run_id: str):
    """The question currently in front of the pupil, as an object."""
    run = client.app.state.sessions.get(run_id, __import__("datetime").datetime.now(
        __import__("datetime").UTC))
    assert isinstance(run, PlacementRun)
    return run, run.current()


def answer_all(client: TestClient, run_id: str, *, correct_up_to: int) -> PlacementRun:
    for _ in range(MAX_ITEMS + 5):
        run, item = item_for(client, run_id)
        if item is None:
            return run
        block = run.block
        assert block is not None
        if block.rung <= correct_up_to:
            response = _right(item)
        else:
            response = _wrong(item)
        posted = client.post(
            f"/nb/nivatest/run/{run_id}/answer",
            data={"item_id": item.id, "response": response},
        )
        assert posted.status_code == 200, posted.text
    pytest.fail("run did not finish over HTTP")


def _right(item) -> str:
    if item.type == "multiple_choice":
        return next(c.id for c in item.choices if c.correct)
    if item.type == "numeric":
        return str(item.answer)
    return next(c[0] for c in item.accept.values() if c)


def _wrong(item) -> str:
    if item.type == "numeric":
        return str(float(item.answer) + max(1.0, item.tolerance * 2 + 1))
    if item.type == "multiple_choice":
        return next(c.id for c in item.choices if not c.correct)
    return "definitely not the answer"


# --- getting in -----------------------------------------------------------


def test_the_start_page_offers_every_grade_and_an_i_do_not_know(client: TestClient) -> None:
    """The klasse is a hint about where to begin, so declining it must be allowed."""
    page = client.get(f"/nb/nivatest/{SUBJECT}")
    assert page.status_code == 200
    assert translate("nb", "placement.grade_unknown") in text_of(page.text)
    assert page.text.count('<option value="') == 11  # ten grades plus the blank


def test_an_unknown_grade_is_accepted_and_starts_at_the_bottom(client: TestClient) -> None:
    run_id = start(client, grade="")
    run, _ = item_for(client, run_id)
    assert run.grade is None
    assert run.blocks[0].rung == 0


def test_a_stated_grade_starts_one_rung_below_it(client: TestClient) -> None:
    run_id = start(client, grade="7")
    run, _ = item_for(client, run_id)
    assert run.blocks[0].rung == run.ladder.start_index(7)


def test_a_grade_outside_grunnskole_is_refused(client: TestClient) -> None:
    assert client.post(f"/nb/nivatest/{SUBJECT}", data={"grade": "11"}).status_code == 400
    assert client.post(f"/nb/nivatest/{SUBJECT}", data={"grade": "0"}).status_code == 400


def test_a_non_numeric_grade_is_refused_rather_than_crashing(client: TestClient) -> None:
    assert client.post(f"/nb/nivatest/{SUBJECT}", data={"grade": "sju"}).status_code == 400


def test_an_unknown_subject_is_404(client: TestClient) -> None:
    assert client.get("/nb/nivatest/NOPE01-01").status_code == 404


def test_an_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/nb/nivatest/run/not-a-real-id").status_code == 404


def test_a_trinntest_id_is_not_valid_on_a_nivatest_path(client: TestClient) -> None:
    """The two share a store, so the type check is what keeps the paths separate."""
    started = client.post(f"/nb/klasse/5/{SUBJECT}/quiz", follow_redirects=False)
    quiz_id = started.headers["location"].rsplit("/", 1)[-1]
    assert client.get(f"/nb/nivatest/run/{quiz_id}").status_code == 404


# --- the sitting ----------------------------------------------------------


def test_the_progress_line_never_claims_a_total(client: TestClient) -> None:
    """An adaptive test has not decided its length; a bar out of N would be a lie."""
    run_id = start(client, grade="5")
    page = client.get(f"/nb/nivatest/run/{run_id}")
    assert page.status_code == 200
    body = text_of(page.text)
    assert "Spørsmål 1" in body
    assert " av " not in body.split("Spørsmål 1")[1][:20]


def test_answering_returns_feedback_with_the_explanation(client: TestClient) -> None:
    run_id = start(client, grade="5")
    _, item = item_for(client, run_id)
    posted = client.post(
        f"/nb/nivatest/run/{run_id}/answer",
        data={"item_id": item.id, "response": _right(item)},
    )
    assert posted.status_code == 200
    assert item.explanation.get("nb") in posted.text


def test_re_answering_the_same_question_is_refused(client: TestClient) -> None:
    run_id = start(client, grade="5")
    _, item = item_for(client, run_id)
    data = {"item_id": item.id, "response": _right(item)}
    assert client.post(f"/nb/nivatest/run/{run_id}/answer", data=data).status_code == 200
    assert client.post(f"/nb/nivatest/run/{run_id}/answer", data=data).status_code == 409


def test_a_whole_run_finishes_and_never_repeats_a_question(client: TestClient) -> None:
    run_id = start(client, grade="5")
    run = answer_all(client, run_id, correct_up_to=3)
    served = [i.id for b in run.blocks for i in b.items]
    assert len(served) == len(set(served))
    assert run.asked <= MAX_ITEMS
    assert run.finished


# --- what the result page says --------------------------------------------


def test_a_bracketed_run_names_both_the_ceiling_and_the_frontier(client: TestClient) -> None:
    run_id = start(client, grade="5")
    run = answer_all(client, run_id, correct_up_to=3)
    outcome = run.outcome()
    assert outcome.ceiling is not None and outcome.ceiling.index == 3

    page = client.get(f"/nb/nivatest/run/{run_id}/result")
    assert page.status_code == 200
    body = text_of(page.text)
    assert str(outcome.ceiling.after_year) in body
    assert outcome.frontier is not None
    assert str(outcome.frontier.after_year) in body


def test_clearing_nothing_is_never_rendered_as_a_level(client: TestClient) -> None:
    """The page must say "we found nothing to measure from", not "level 0"."""
    run_id = start(client, grade="")
    run = answer_all(client, run_id, correct_up_to=-1)
    assert run.outcome().ceiling is None

    body = text_of(client.get(f"/nb/nivatest/run/{run_id}/result").text)
    expected = translate("nb", "placement_result.nothing_yet")
    assert expected.split("—")[0].strip()[:40] in body


def test_topping_out_says_the_ladder_ended_not_that_the_pupil_did(client: TestClient) -> None:
    run_id = start(client, grade="10")
    run = answer_all(client, run_id, correct_up_to=99)
    outcome = run.outcome()
    assert outcome.topped_out

    body = text_of(client.get(f"/nb/nivatest/run/{run_id}/result").text)
    assert translate("nb", "placement_result.topped_out").strip()[:40] in body


def test_the_result_page_always_disclaims_being_a_verdict(client: TestClient) -> None:
    run_id = start(client, grade="5")
    answer_all(client, run_id, correct_up_to=3)
    body = text_of(client.get(f"/nb/nivatest/run/{run_id}/result").text)
    assert translate("nb", "placement_result.not_a_verdict").strip()[:40] in body


def test_the_gaps_list_quotes_the_curriculum_verbatim(client: TestClient) -> None:
    """Udir's words, marked as Udir's -- an NLOD condition, not decoration."""
    run_id = start(client, grade="5")
    run = answer_all(client, run_id, correct_up_to=3)
    assert run.gaps(), "expected a pupil who failed a rung to have gaps"

    page = client.get(f"/nb/nivatest/run/{run_id}/result")
    assert translate("nb", "curriculum.verbatim_note").strip()[:30] in text_of(page.text)
    assert "<blockquote" in page.text


def test_a_thin_checkpoint_is_disclosed_on_the_result_page(client: TestClient) -> None:
    """Norsk reaches about a third of each checkpoint; a ceiling must say so."""
    run_id = start(client, "NOR01-08", grade="7")
    run = answer_all(client, run_id, correct_up_to=1)
    outcome = run.outcome()
    if outcome.ceiling is None:
        pytest.skip("no ceiling to qualify")

    bank = ItemBank.load()
    coverage = bank.coverage(outcome.ceiling.goal_set)
    assert not coverage.complete, "norsk was expected to be partially covered"
    body = text_of(client.get(f"/nb/nivatest/run/{run_id}/result").text)
    assert str(coverage.tested) in body and str(coverage.total) in body


# --- entry points ---------------------------------------------------------


def test_the_home_page_offers_both_questions(client: TestClient) -> None:
    body = client.get("/nb/").text
    assert "/nb/nivatest/" in body
    assert "/nb/klasse/1" in body


def test_the_subject_page_offers_the_nivatest_alongside_the_trinntest(client: TestClient) -> None:
    body = client.get(f"/nb/klasse/5/{SUBJECT}").text
    assert f"/nb/nivatest/{SUBJECT}" in body


def test_a_gap_list_built_from_single_questions_says_so(client: TestClient) -> None:
    """Eight goals shown as "0 av 1" reads as a finding unless the page says otherwise.

    A nivåtest spreads few questions over many goals, so this is the normal case
    rather than an edge one -- which is exactly why it has to be stated.
    """
    run_id = start(client, grade="5")
    run = answer_all(client, run_id, correct_up_to=3)
    tally = run.tally()
    assert run.gaps() and all(tally[code][1] <= 2 for code in run.gaps())

    body = text_of(client.get(f"/nb/nivatest/run/{run_id}/result").text)
    assert translate("nb", "placement_result.thin_evidence").strip()[:40] in body
