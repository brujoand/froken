"""FastAPI application factory.

The catalogue loads once at startup and is then immutable. There is no database:
the whole dataset is a few megabytes of vendored JSON, and keeping it in memory
means a page load touches no network and no disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from froken import __version__
from froken.catalogue.loader import Catalogue
from froken.web.routes import router

STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.catalogue = Catalogue.load()
    yield


def create_app(catalogue: Catalogue | None = None) -> FastAPI:
    """Build the app. Pass `catalogue` to substitute a fixture in tests."""
    app = FastAPI(
        title="Frøken",
        version=__version__,
        lifespan=lifespan if catalogue is None else None,
        # No API consumers, and the docs would only expose internals.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    if catalogue is not None:
        app.state.catalogue = catalogue

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(router)
    return app


app = create_app()
