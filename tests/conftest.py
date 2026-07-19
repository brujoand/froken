"""Shared test configuration.

The suite must never reach the network. Udir is a public service with no SLA, and
a test that quietly depends on it turns their maintenance window into our red
build -- while also being slow and non-deterministic for everyone.
"""

from __future__ import annotations

import httpx
import pytest


@pytest.fixture(autouse=True)
def no_outbound_http(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that opens a real HTTP connection.

    Blocks httpx's real transports only. `ASGITransport` is a different class and
    keeps working, so `TestClient` still drives the app in-process -- which is
    the distinction that matters: exercising our own routes is fine, leaving the
    machine is not.
    """

    def refuse(self: object, *args: object, **kwargs: object) -> None:
        raise RuntimeError("a test attempted a real HTTP request; use recorded fixtures instead")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuse)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refuse)
