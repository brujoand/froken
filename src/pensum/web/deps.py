"""What routes reach for: settings, the signed-in user, the admin gate.

All of it hangs off `app.state`, set once in the factory, so a test can build an
app with sign-in configured without touching the environment.
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from pensum.auth.cookies import CookieCodec, read_user
from pensum.auth.models import User
from pensum.auth.oidc import OidcClient
from pensum.config import Settings
from pensum.scores.store import AttemptStore


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_codec(request: Request) -> CookieCodec:
    return request.app.state.cookies


def get_oidc(request: Request) -> OidcClient | None:
    return request.app.state.oidc


def get_store(request: Request) -> AttemptStore | None:
    return request.app.state.attempts


def current_user(request: Request) -> User | None:
    """The signed-in user, or None -- which is an ordinary state, not an error."""
    if not get_settings(request).auth_enabled:
        return None
    return read_user(request, get_codec(request))


def require_admin(request: Request) -> User:
    """The gate on every admin page.

    Group membership is re-read from the cookie on each request, and the cookie
    was written from what the provider asserted at sign-in. So revoking someone
    in pocket-id takes effect when their session expires, not instantly -- which
    is the cost of not calling the provider on every page load, and is stated
    here rather than discovered later.
    """
    settings = get_settings(request)
    if not settings.auth_enabled:
        raise HTTPException(status_code=404, detail="sign-in is not configured")

    user = current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="sign in to view this page")
    if not user.in_group(settings.admin_group):
        raise HTTPException(status_code=403, detail="not an administrator")
    return user


def base_url(request: Request) -> str:
    """Pensum's own public origin.

    Behind a TLS-terminating proxy the request scheme is http, and a redirect
    URI built from it would not match the one registered with the provider.
    `PENSUM_BASE_URL` is the override for exactly that, and it wins when set.
    """
    configured = get_settings(request).base_url
    if configured:
        return configured
    return str(request.base_url).rstrip("/")


def is_secure(request: Request) -> bool:
    """Whether to mark cookies Secure. Derived, so local http still works."""
    return base_url(request).startswith("https://")


def safe_next(candidate: str | None, fallback: str) -> str:
    """Only ever redirect to a path on this site.

    `?next=` is attacker-controlled by definition. A value starting with `//`
    is a protocol-relative URL to another host and is refused along with
    anything carrying a scheme.
    """
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return fallback
    return candidate
