"""Route and rendering tests, run against the real committed catalogue.

Two things get asserted harder than the rest, because both are promises to the
user rather than implementation details: that Norwegian is genuinely first-class,
and that a pupil below a checkpoint year is never told they are behind.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from froken.catalogue.loader import Catalogue
from froken.i18n import UI_LOCALES, catalog
from froken.web.app import create_app

QUIZZABLE = ["MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04"]


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Catalogue.load()))


def text_of(html: str) -> str:
    body = html[html.find("<main>") : html.find("</main>")]
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)).strip()


def test_healthz(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_root_redirects_to_norwegian(client: TestClient) -> None:
    """Norwegian is the default. An unprefixed path is not a neutral one."""
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/nb/"


@pytest.mark.parametrize("locale", ["nb", "nn", "en"])
def test_home_renders_in_every_locale(client: TestClient, locale: str) -> None:
    response = client.get(f"/{locale}/")
    assert response.status_code == 200
    assert f'lang="{locale}"' in response.text


@pytest.mark.parametrize("grade", range(1, 11))
def test_every_grade_page_lists_subjects(client: TestClient, grade: int) -> None:
    response = client.get(f"/nb/klasse/{grade}")
    assert response.status_code == 200
    assert "kompetansemål" in text_of(response.text)


@pytest.mark.parametrize("grade", [0, 11, 99])
def test_grades_outside_grunnskole_are_404(client: TestClient, grade: int) -> None:
    assert client.get(f"/nb/klasse/{grade}").status_code == 404


def test_unknown_locale_is_404(client: TestClient) -> None:
    assert client.get("/de/").status_code == 404


def test_unknown_subject_is_404(client: TestClient) -> None:
    assert client.get("/nb/klasse/5/NOPE01-01").status_code == 404


@pytest.mark.parametrize("code", QUIZZABLE)
def test_subject_pages_render_for_every_grade(client: TestClient, code: str) -> None:
    for grade in range(1, 11):
        response = client.get(f"/nb/klasse/{grade}/{code}")
        assert response.status_code == 200, f"{code} grade {grade}"


def test_checkpoint_year_says_you_should_know_this(client: TestClient) -> None:
    body = text_of(client.get("/nb/klasse/2/MAT01-06").text)
    assert "Dette skal du kunne etter 2. trinn" in body


def test_below_checkpoint_says_you_are_working_towards_it(client: TestClient) -> None:
    """A 1st-grader is not behind on 2. trinn goals, and must not be told so.

    The reassurance note is the whole point of the distinction; asserting on it
    keeps a future template edit from quietly turning encouragement into a
    verdict.
    """
    body = text_of(client.get("/nb/klasse/1/MAT01-06").text)
    assert "Dette jobber du mot fram til 2. trinn" in body
    assert "Du trenger altså ikke kunne alt ennå" in body
    assert "Dette skal du kunne" not in body


def test_krle_is_offered_from_first_grade(client: TestClient) -> None:
    """KRLE's first checkpoint is after 4. trinn but it is taught from year 1."""
    response = client.get("/nb/klasse/1/RLE01-04")
    assert response.status_code == 200
    assert "fram til 4. trinn" in text_of(response.text)


def test_curriculum_text_is_shown_verbatim_and_marked_as_such(client: TestClient) -> None:
    """NLOD forbids presenting the data misleadingly, so official text is labelled.

    The goal text must appear exactly as ingested -- no truncation, no rewording.
    """
    catalogue = Catalogue.load()
    goal = catalogue.subject("MAT01-06").goal_sets[0].goals[0]
    html = client.get("/nb/klasse/2/MAT01-06").text

    assert goal.text.get("nob") in text_of(html)
    assert "gjengitt ordrett fra læreplanen" in text_of(html)
    assert goal.source_url in html


def test_every_page_carries_the_nlod_attribution(client: TestClient) -> None:
    """A licence condition, so it belongs on the page, not only in the README."""
    for path in ["/nb/", "/nb/klasse/3", "/nb/klasse/3/NOR01-08", "/en/"]:
        assert "data.udir.no" in client.get(path).text
        assert "NLOD" in client.get(path).text


def test_every_page_carries_the_disclaimer(client: TestClient) -> None:
    body = client.get("/nb/klasse/2/MAT01-06").text
    assert "uoffisielt" in body or "ikke tilknyttet" in body


def test_english_pages_use_english_chrome_and_english_curriculum(client: TestClient) -> None:
    body = text_of(client.get("/en/klasse/2/MAT01-06").text)
    assert "What you should know by the end of year 2" in body
    # Udir's own English translation of the goal, not ours.
    assert "group numbers" in body.lower()


def test_no_translation_key_leaks_into_rendered_html(client: TestClient) -> None:
    """A missing string renders as its dotted key, which is unmistakable in prose."""
    keys = set(catalog("nb")) | set(catalog("en"))
    for locale in UI_LOCALES:
        for path in [f"/{locale}/", f"/{locale}/klasse/4", f"/{locale}/klasse/4/SAF01-05"]:
            body = text_of(client.get(path).text)
            leaked = [key for key in keys if key in body]
            assert not leaked, f"{path} leaked {leaked}"
