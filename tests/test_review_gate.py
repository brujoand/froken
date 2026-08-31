"""Who is allowed to see content no human has read yet.

Pensum withholds unreviewed quiz items and unreviewed reading passages, because
generation writes them unreviewed and a merge alone must never put an unread
question in front of a child. An administrator is the exception: a draft has to
be readable in place before anyone can judge whether it is fit.

The assertions that matter here are the negative ones. Every other test in this
suite fails loudly when something stops working; these fail loudly when
something starts working for the wrong person.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pensum.auth.cookies import LOGIN_COOKIE, CookieCodec
from pensum.auth.models import User
from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.items.loader import ItemBank
from pensum.reading.library import ReadingLibrary
from pensum.web.app import create_app

ORIGIN = "https://pensum.example.com"
SECRET = "test-secret"

ADMIN = User(sub="u-admin", name="Voksen", groups=("pensum-admins",))
PUPIL = User(sub="u-1", name="Ola", groups=("pupils",))

# Norsk after 2. trinn. Every reading passage committed is unreviewed, which
# makes this the sharpest fixture available: a pupil must see no reading at all
# here, and an administrator must see the drafts.
READING_PATH = "/nb/klasse/2/NOR01-08/lesing"
SUBJECT_PATH = "/nb/klasse/2/NOR01-08"


def settings_with(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "oidc_issuer": "https://id.example.com",
        "oidc_client_id": "pensum",
        "oidc_client_secret": "s3cret",
        "admin_group": "pensum-admins",
        "base_url": ORIGIN,
        "session_secret": SECRET,
    }
    return Settings(**(defaults | overrides))


def build(settings: Settings | None = None) -> tuple[FastAPI, TestClient]:
    app = create_app(
        Catalogue.load(),
        ItemBank.load(),
        settings=settings if settings is not None else settings_with(),
        reading=ReadingLibrary.load(),
    )
    return app, TestClient(app, base_url=ORIGIN)


def sign_in(client: TestClient, user: User) -> None:
    client.cookies.set(
        LOGIN_COOKIE, CookieCodec(SECRET).dump_login(user), domain="pensum.example.com"
    )


# --- the negative cases ----------------------------------------------------


def test_an_anonymous_pupil_sees_no_unreviewed_reading() -> None:
    _, client = build()

    assert client.get(READING_PATH).status_code == 404
    assert "/lesing" not in client.get(SUBJECT_PATH).text


def test_a_signed_in_pupil_is_not_an_administrator() -> None:
    """Being signed in is not the qualification. Being in the group is."""
    _, client = build()
    sign_in(client, PUPIL)

    assert client.get(READING_PATH).status_code == 404
    assert "/lesing" not in client.get(SUBJECT_PATH).text


def test_an_unconfigured_instance_has_no_administrators() -> None:
    """With sign-in unconfigured, `current_user` is always None -- so nobody
    qualifies, even carrying a cookie minted with the right secret. This is the
    state the published image runs in."""
    _, client = build(Settings(session_secret=SECRET, base_url=ORIGIN))
    sign_in(client, ADMIN)

    assert client.get(READING_PATH).status_code == 404


def test_a_draft_is_never_reachable_by_guessing_its_url() -> None:
    """The listing is hidden from a pupil; so is the passage behind it."""
    library = ReadingLibrary.load(include_unreviewed=True)
    passage = library.for_goal_set("KV1107")[0]
    _, client = build()
    sign_in(client, PUPIL)

    assert client.get(f"{READING_PATH}/{passage.id}").status_code == 404
    assert (
        client.post(f"{READING_PATH}/{passage.id}/tid", data={"seconds": "30"}).status_code == 404
    )


# --- the positive case -----------------------------------------------------


def test_an_administrator_sees_the_drafts_and_that_they_are_drafts() -> None:
    _, client = build()
    sign_in(client, ADMIN)

    listing = client.get(READING_PATH)

    assert listing.status_code == 200
    assert "Sokken som rømte" in listing.text
    # Marked, not silently mixed in: an unmarked draft would be judged as if it
    # had already passed review.
    assert "Utkast" in listing.text


def test_an_administrator_can_read_a_draft_passage_through() -> None:
    library = ReadingLibrary.load(include_unreviewed=True)
    passage = library.for_goal_set("KV1107")[0]
    _, client = build()
    sign_in(client, ADMIN)

    page = client.get(f"{READING_PATH}/{passage.id}")
    scored = client.post(f"{READING_PATH}/{passage.id}/tid", data={"seconds": "45"})

    assert page.status_code == 200
    assert scored.status_code == 200


def test_the_subject_page_tells_an_administrator_why_it_looks_different() -> None:
    _, client = build()
    sign_in(client, ADMIN)

    page = client.get(SUBJECT_PATH)

    assert "/lesing" in page.text
    assert "logget inn som administrator" in page.text


# --- the deployment-wide switch still works --------------------------------


def test_the_env_switch_shows_drafts_to_everyone() -> None:
    """What a maintainer reviewing locally uses. Deliberately not the same
    mechanism as being an administrator, and emphatically not for the instance
    children use."""
    _, client = build(settings_with(include_unreviewed_items=True))

    assert client.get(READING_PATH).status_code == 200


@pytest.mark.parametrize("user", [None, PUPIL, ADMIN])
def test_reviewed_content_is_visible_to_everyone(user: User | None) -> None:
    """The gate only ever withholds drafts. Matematikk has reviewed items
    committed, so every reader sees its quiz."""
    _, client = build()
    if user is not None:
        sign_in(client, user)

    assert "Ta quizen" in client.get("/nb/klasse/2/MAT01-06").text
