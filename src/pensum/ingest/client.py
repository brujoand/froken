"""A polite, caching client for Udir's open Grep API.

Udir publishes this as a public good with no SLA and no authentication. We fetch
it rarely (a maintainer run, plus a weekly drift check), cache every response,
and cap concurrency. The web app never touches it -- by the time a pupil loads a
page, the curriculum is vendored JSON in the image.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://data.udir.no/kl06/v201906"

# Deliberately low. There is no rate limit published, which is a reason to be
# careful rather than a licence to hammer.
MAX_CONCURRENCY = 4
MAX_ATTEMPTS = 4


class UdirClient:
    """Fetches Grep resources, caching raw responses on disk.

    The cache is keyed by resource type and code and is gitignored. It exists so
    re-running the ingest after a normalise change costs nothing -- roughly 800
    requests otherwise.
    """

    def __init__(self, cache_dir: Path, *, refresh: bool = False) -> None:
        self._cache_dir = cache_dir
        self._refresh = refresh
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> UdirClient:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=httpx.Timeout(30.0),
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client:
            await self._client.aclose()

    def _cache_path(self, resource: str, code: str) -> Path:
        return self._cache_dir / resource / f"{code}.json"

    async def get(self, resource: str, code: str = "") -> dict[str, Any] | list[Any]:
        """Fetch `{BASE_URL}/{resource}/{code}`, via cache when possible."""
        path = self._cache_path(resource, code or "_index")
        if not self._refresh and path.exists():
            return json.loads(path.read_text(encoding="utf-8"))

        payload = await self._fetch(f"/{resource}/{code}" if code else f"/{resource}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return payload

    async def _fetch(self, url: str) -> Any:
        if self._client is None:
            raise RuntimeError("UdirClient must be used as an async context manager")
        last: Exception | None = None

        for attempt in range(MAX_ATTEMPTS):
            async with self._semaphore:
                try:
                    response = await self._client.get(url)
                    response.raise_for_status()
                    return response.json()
                except (httpx.HTTPError, json.JSONDecodeError) as exc:
                    # A 404 means we asked for something that does not exist;
                    # retrying will not change that.
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404:
                        raise
                    last = exc

            if attempt < MAX_ATTEMPTS - 1:
                await asyncio.sleep(2**attempt)

        raise RuntimeError(f"giving up on {url} after {MAX_ATTEMPTS} attempts") from last
