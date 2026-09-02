"""FastAPI application factory.

The catalogue loads once at startup and is then immutable. There is no database
behind the curriculum: the whole dataset is a few megabytes of vendored JSON, and
keeping it in memory means a page load touches no network and no disk.

Three optional subsystems attach here, all off unless configured: sign-in
against an OIDC provider, a SQLite file of finished attempts, and the speech
models that turn a reading from timed into checked. With none of them set --
which is what `docker run` with no environment gives you -- this is the app that
stores nothing about anybody and makes no outbound request.
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
from pensum.reading.library import ReadingLibrary
from pensum.reading.streams import StreamStore
from pensum.reading.transcribe import Transcriber, load_transcriber
from pensum.scores.store import AttemptStore
from pensum.web.admin_routes import router as admin_router
from pensum.web.auth_routes import router as auth_router
from pensum.web.quiz_routes import router as quiz_router
from pensum.web.reading_routes import router as reading_router
from pensum.web.routes import router
from pensum.web.writing_routes import router as writing_router
from pensum.writing.library import WritingLibrary

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.catalogue = Catalogue.load()
    unreviewed = app.state.settings.include_unreviewed_items
    app.state.items = ItemBank.load(include_unreviewed=unreviewed)
    app.state.reading = ReadingLibrary.load(include_unreviewed=unreviewed)
    app.state.writing = WritingLibrary.load(include_unreviewed=unreviewed)
    yield


def create_app(
    catalogue: Catalogue | None = None,
    items: ItemBank | None = None,
    settings: Settings | None = None,
    reading: ReadingLibrary | None = None,
    writing: WritingLibrary | None = None,
    transcriber: Transcriber | None = None,
) -> FastAPI:
    """Build the app. Pass the collaborators to substitute them in tests."""
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
        app.state.reading = reading if reading is not None else ReadingLibrary.load()
        app.state.writing = writing if writing is not None else WritingLibrary.load()
    else:
        if reading is not None:
            app.state.reading = reading
        if writing is not None:
            app.state.writing = writing

    # Loaded here rather than in the lifespan so a test can inject a fake
    # without a model on disk. None -- no models configured -- is the default
    # and is an ordinary state: readings are then timed but not checked.
    app.state.transcriber = (
        transcriber if transcriber is not None else load_transcriber(active.speech_model_dir)
    )

    # Quiz sessions live here rather than in a store: an unfinished quiz is not
    # a result, so a restart losing in-flight quizzes is the accepted cost.
    app.state.sessions = SessionStore()

    # Readings in progress, held only while they are in progress. Same trade as
    # quiz sessions: a restart loses an in-flight reading, which is not a result.
    app.state.streams = StreamStore()

    app.state.cookies = CookieCodec(active.session_secret)
    # Constructed eagerly so half-configured sign-in fails at startup rather
    # than on the first child who clicks it. Discovery stays lazy -- the
    # provider does not have to be up before Pensum is.
    app.state.oidc = OidcClient(active) if active.auth_enabled else None
    app.state.attempts = AttemptStore(active.database_path) if active.history_enabled else None

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    app.include_router(quiz_router)
    app.include_router(reading_router)
    app.include_router(writing_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    return app


app = create_app()
