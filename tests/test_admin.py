"""The admin view, and the path by which a score gets there.

Two things are asserted harder than the rest, because both are promises rather
than implementation details: that an anonymous pupil is still never recorded,
and that nobody outside the configured group can read anyone's scores.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pensum.auth.cookies import LOGIN_COOKIE, CookieCodec
from pensum.auth.models import User
from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.items.schema import QuizItem
from pensum.web.app import create_app

ORIGIN = "https://pensum.example.com"
SECRET = "test-secret"
ADMIN = User(sub="u-admin", name="Voksen", groups=("pensum-admins",))
PUPIL = User(sub="u-1", name="Ola", groups=("pupils",))

# One checkpoint with reviewed items committed, so the walk-through below is
# exercising the real item bank rather than a fixture.
QUIZ_PATH = "/nb/klasse/2/MAT01-06"


def settings_with(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "oidc_issuer": "https://id.example.com",
        "oidc_client_id": "pensum",
        "oidc_client_secret": "s3cret",
        "admin_group": "pensum-admins",
        "base_url": ORIGIN,
        "session_secret": SECRET,
        "database_path": tmp_path / "pensum.db",
    }
    return Settings(**(defaults | overrides))


def build(settings: Settings) -> tuple[FastAPI, TestClient]:
    app = create_app(Catalogue.load(), settings=settings)
    return app, TestClient(app, base_url=ORIGIN)


def sign_in(client: TestClient, user: User) -> None:
    client.cookies.set(
        LOGIN_COOKIE, CookieCodec(SECRET).dump_login(user), domain="pensum.example.com"
    )


def correct_response(item: QuizItem) -> str:
    """The right answer, in the form the answer endpoint expects."""
    if item.type == "multiple_choice":
        return next(choice.id for choice in item.choices if choice.correct)
    if item.type == "numeric":
        return str(item.answer)
    return item.accept["nb"][0]


def take_quiz(app: FastAPI, client: TestClient, *, right: int = 99) -> str:
    """Start a quiz, answer `right` of it correctly, and open the result."""
    started = client.post(f"{QUIZ_PATH}/quiz", follow_redirects=False)
    assert started.status_code == 303
    session_id = started.headers["location"].rsplit("/", 1)[-1]

    session = app.state.sessions.get(session_id, datetime.now(UTC))
    assert session is not None
    for index, item in enumerate(list(session.items)):
        response = correct_response(item) if index < right else ""
        client.post(
            f"/nb/quiz/{session_id}/answer",
            data={"item_id": item.id, "response": response},
        )

    assert client.get(f"/nb/quiz/{session_id}/result").status_code == 200
    return session_id


# Recording ----------------------------------------------------------------


def test_a_signed_in_pupil_who_finishes_a_quiz_is_recorded(tmp_path: Path) -> None:
    app, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)
    take_quiz(app, client)

    [stored] = app.state.attempts.attempts_for("u-1")
    assert stored.user_name == "Ola"
    assert stored.subject == "MAT01-06"
    assert stored.grade == 2
    assert stored.correct == stored.total > 0


def test_an_anonymous_pupil_is_never_recorded(tmp_path: Path) -> None:
    """The promise that survives this whole feature. Signing in is opt-in."""
    app, client = build(settings_with(tmp_path))
    take_quiz(app, client)

    assert app.state.attempts.users() == []


def test_an_abandoned_quiz_is_not_a_result(tmp_path: Path) -> None:
    """Half a quiz is not a score, and leaves nothing behind."""
    app, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)

    started = client.post(f"{QUIZ_PATH}/quiz", follow_redirects=False)
    session_id = started.headers["location"].rsplit("/", 1)[-1]
    session = app.state.sessions.get(session_id, datetime.now(UTC))
    client.post(
        f"/nb/quiz/{session_id}/answer",
        data={"item_id": session.items[0].id, "response": ""},
    )
    client.get(f"/nb/quiz/{session_id}/result")

    assert app.state.attempts.users() == []


def test_reloading_the_result_does_not_record_a_second_attempt(tmp_path: Path) -> None:
    app, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)
    session_id = take_quiz(app, client)

    client.get(f"/nb/quiz/{session_id}/result")
    client.get(f"/nb/quiz/{session_id}/result")

    assert len(app.state.attempts.attempts_for("u-1")) == 1


def test_nothing_is_recorded_when_no_database_is_configured(tmp_path: Path) -> None:
    """The default. Sign-in without a database is still a site that forgets."""
    app, client = build(settings_with(tmp_path, database_path=None))
    sign_in(client, PUPIL)
    take_quiz(app, client)

    assert app.state.attempts is None


def test_a_signed_in_pupil_is_told_their_score_was_kept(tmp_path: Path) -> None:
    """Said on the page it becomes true on, not only in a privacy note."""
    app, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)
    session_id = take_quiz(app, client)

    body = client.get(f"/nb/quiz/{session_id}/result").text
    assert "resultatet blir lagret" in body


def test_an_anonymous_pupil_is_told_nothing_about_storage(tmp_path: Path) -> None:
    app, client = build(settings_with(tmp_path))
    session_id = take_quiz(app, client)

    assert "resultatet blir lagret" not in client.get(f"/nb/quiz/{session_id}/result").text


# Access -------------------------------------------------------------------


def test_the_admin_pages_do_not_exist_without_sign_in_configured(tmp_path: Path) -> None:
    _, client = build(Settings())
    assert client.get("/nb/admin").status_code == 404


def test_an_anonymous_visitor_is_asked_to_sign_in(tmp_path: Path) -> None:
    _, client = build(settings_with(tmp_path))
    assert client.get("/nb/admin").status_code == 401


def test_a_pupil_cannot_read_the_roster(tmp_path: Path) -> None:
    """Group membership is the whole gate. A signed-in child is not an admin."""
    _, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)

    assert client.get("/nb/admin").status_code == 403


def test_a_pupil_cannot_read_another_pupils_history(tmp_path: Path) -> None:
    app, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)
    take_quiz(app, client)

    assert client.get("/nb/admin/elev/u-1").status_code == 403


def test_membership_of_another_group_is_not_enough(tmp_path: Path) -> None:
    _, client = build(settings_with(tmp_path, admin_group="laerere"))
    sign_in(client, ADMIN)

    assert client.get("/nb/admin").status_code == 403


def test_a_forged_admin_cookie_is_not_signed_in(tmp_path: Path) -> None:
    _, client = build(settings_with(tmp_path))
    client.cookies.set(
        LOGIN_COOKIE,
        CookieCodec("a-different-secret").dump_login(ADMIN),
        domain="pensum.example.com",
    )

    assert client.get("/nb/admin").status_code == 401


# The pages ----------------------------------------------------------------


@pytest.fixture
def populated(tmp_path: Path) -> tuple[FastAPI, TestClient]:
    """One pupil with one finished quiz, viewed by an admin."""
    app, client = build(settings_with(tmp_path))
    sign_in(client, PUPIL)
    take_quiz(app, client)
    sign_in(client, ADMIN)
    return app, client


def test_the_roster_lists_the_pupil_and_links_to_them(
    populated: tuple[FastAPI, TestClient],
) -> None:
    _, client = populated
    body = client.get("/nb/admin").text

    assert "Ola" in body
    assert "/nb/admin/elev/u-1" in body


def test_the_roster_repeats_that_this_is_not_an_assessment(
    populated: tuple[FastAPI, TestClient],
) -> None:
    """An adult reading percentages is likelier to mistake them for a verdict."""
    _, client = populated
    assert "ikke vurdering" in client.get("/nb/admin").text


def test_a_pupils_page_shows_the_attempt_broken_down_by_goal(
    populated: tuple[FastAPI, TestClient],
) -> None:
    app, client = populated
    [stored] = app.state.attempts.attempts_for("u-1")
    catalogue = Catalogue.load()
    goals = {
        goal.code: goal for goal in catalogue.subject("MAT01-06").goal_set(stored.goal_set).goals
    }

    body = client.get("/nb/admin/elev/u-1").text

    assert "Ola" in body
    # Udir's wording, quoted and linked, exactly as everywhere else on the site.
    first = goals[stored.by_goal[0].goal]
    assert first.text.get("nob") in body
    assert first.source_url in body


def test_a_pupil_with_no_attempts_is_a_404(populated: tuple[FastAPI, TestClient]) -> None:
    """Also true of a user who does not exist -- and telling them apart would
    turn the page into a user-enumeration oracle."""
    _, client = populated
    assert client.get("/nb/admin/elev/u-nobody").status_code == 404


def test_the_roster_renders_in_english_too(populated: tuple[FastAPI, TestClient]) -> None:
    _, client = populated
    body = client.get("/en/admin").text

    assert "Pupil" in body
    assert "admin.column.pupil" not in body


def test_the_header_offers_sign_in_only_when_it_is_configured(tmp_path: Path) -> None:
    """An unconfigured Pensum shows no trace of accounts, because it has none."""
    _, plain = build(Settings())
    assert "/auth/login" not in plain.get("/nb/").text

    _, configured = build(settings_with(tmp_path))
    assert "/auth/login" in configured.get("/nb/").text


def test_the_admin_link_appears_only_for_an_admin(tmp_path: Path) -> None:
    _, client = build(settings_with(tmp_path))

    sign_in(client, PUPIL)
    assert "/nb/admin" not in client.get("/nb/").text

    sign_in(client, ADMIN)
    assert "/nb/admin" in client.get("/nb/").text
