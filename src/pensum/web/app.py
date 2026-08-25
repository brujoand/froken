"""FastAPI application factory.

The catalogue loads once at startup and is then immutable. There is no database
behind the curriculum: the whole dataset is a few megabytes of vendored JSON, and
keeping it in memory means a page load touches no network and no disk.

Two optional subsystems attach here, both off unless configured: sign-in against
an OIDC provider, and a SQLite file of finished attempts. With neither set --
which is what `docker run` with no environment gives you -- this is the app that
stores nothing about anybody.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from pensum import __version__
from pensum.auth.cookies import CookieCodec
from pensum.auth.oidc import OidcClient
from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.config import settings as env_settings
from pensum.items.loader import ItemBank
from pensum.quiz.session import SessionStore
from pensum.scores.store import AttemptStore
from pensum.web.admin_routes import router as admin_router
from pensum.web.auth_routes import router as auth_router
from pensum.web.placement_routes import router as placement_router
from pensum.web.quiz_routes import router as quiz_router
from pensum.web.routes import router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.catalogue = Catalogue.load()
    app.state.items = ItemBank.load(include_unreviewed=app.state.settings.include_unreviewed_items)
    yield


def create_app(
    catalogue: Catalogue | None = None,
    items: ItemBank | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    """Build the app. Pass `catalogue`/`items`/`settings` to substitute in tests."""
    active = settings if settings is not None else env_settings

    app = FastAPI(
        title="Pensum",
        version=__version__,
        lifespan=lifespan if catalogue is None else None,
        # No API consumers, and the docs would only expose internals.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = active
    if catalogue is not None:
        app.state.catalogue = catalogue
        app.state.items = items if items is not None else ItemBank.load()

    # Quiz sessions live here rather than in a store: an unfinished quiz is not
    # a result, so a restart losing in-flight quizzes is the accepted cost.
    app.state.sessions = SessionStore()

    app.state.cookies = CookieCodec(active.session_secret)
    # Constructed eagerly so half-configured sign-in fails at startup rather
    # than on the first child who clicks it. Discovery stays lazy -- the
    # provider does not have to be up before Pensum is.
    app.state.oidc = OidcClient(active) if active.auth_enabled else None
    app.state.attempts = AttemptStore(active.database_path) if active.history_enabled else None

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(quiz_router)
    app.include_router(placement_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


app = create_app()
