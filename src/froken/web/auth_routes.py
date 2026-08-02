"""Sign in, come back, sign out.

Three routes, no pages. Every one of them ends in a redirect, so there is no
sign-in screen of ours to phish and no password Frøken could leak by having.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import replace

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse

from froken.auth.cookies import LoginFlow, clear_flow, clear_login, read_flow, set_flow, set_login
from froken.auth.oidc import (
    OidcError,
    decode_claims,
    new_verifier,
    user_from_claims,
    verify_claims,
)
from froken.i18n import DEFAULT_LOCALE
from froken.web.deps import base_url, get_codec, get_oidc, is_secure, safe_next

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", include_in_schema=False)

HOME = f"/{DEFAULT_LOCALE}/"


def _client(request: Request):
    client = get_oidc(request)
    if client is None:
        # Not misconfiguration: sign-in is off, so the route genuinely is not
        # there. 404 rather than 500 says so.
        raise HTTPException(status_code=404, detail="sign-in is not configured")
    return client


def _failed(destination: str) -> RedirectResponse:
    """Back where they started, with enough for the page to say what happened."""
    separator = "&" if "?" in destination else "?"
    response = RedirectResponse(f"{destination}{separator}signin=failed", status_code=303)
    clear_flow(response)
    return response


@router.get("/login")
async def login(request: Request, next: str = HOME) -> RedirectResponse:
    client = _client(request)
    provider = await client.provider()

    flow = LoginFlow(
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        verifier=new_verifier(),
        next_url=safe_next(next, HOME),
    )
    destination = client.authorization_url(
        provider,
        redirect_uri=f"{base_url(request)}/auth/callback",
        state=flow.state,
        nonce=flow.nonce,
        verifier=flow.verifier,
    )

    response = RedirectResponse(destination, status_code=303)
    set_flow(response, get_codec(request), flow, secure=is_secure(request))
    return response


@router.get("/callback")
async def callback(
    request: Request, code: str | None = None, state: str | None = None, error: str | None = None
) -> RedirectResponse:
    client = _client(request)
    flow = read_flow(request, get_codec(request))
    destination = flow.next_url if flow else HOME

    if flow is None:
        # No flow cookie: a stale tab, a bookmarked callback, or a cross-site
        # attempt to complete somebody else's login. Indistinguishable, and all
        # three are refused the same way.
        logger.warning("sign-in callback arrived without a login flow cookie")
        return _failed(destination)

    if error or not code or not state:
        logger.warning("sign-in callback reported an error: %s", error or "no code returned")
        return _failed(destination)

    # The state check is the CSRF defence: the callback must answer the flow
    # this browser started, not one someone else started for it.
    if not secrets.compare_digest(state, flow.state):
        logger.warning("sign-in callback state did not match the login flow")
        return _failed(destination)

    try:
        tokens = await client.exchange(
            await client.provider(),
            code=code,
            redirect_uri=f"{base_url(request)}/auth/callback",
            verifier=flow.verifier,
        )
        claims = decode_claims(str(tokens["id_token"]))
        verify_claims(claims, issuer=client.issuer, client_id=client.client_id, nonce=flow.nonce)
        user = user_from_claims(claims)

        # Some providers put group membership only in userinfo. Asking costs one
        # request at sign-in and saves an admin from an unexplained 403.
        if not user.groups and isinstance(tokens.get("access_token"), str):
            groups = await client.groups_from_userinfo(
                await client.provider(), str(tokens["access_token"])
            )
            if groups:
                user = replace(user, groups=groups)
    except OidcError as exc:
        logger.warning("sign-in failed: %s", exc)
        return _failed(destination)
    except Exception:  # noqa: BLE001 -- a provider outage must not 500 a child's page
        logger.exception("sign-in failed while talking to the provider")
        return _failed(destination)

    response = RedirectResponse(destination, status_code=303)
    set_login(response, get_codec(request), user, secure=is_secure(request))
    clear_flow(response)
    return response


@router.post("/logout")
async def logout(request: Request, next: str = HOME) -> RedirectResponse:
    """POST only: a GET would let any page sign a pupil out with an <img> tag."""
    response = RedirectResponse(safe_next(next, HOME), status_code=303)
    clear_login(response)
    clear_flow(response)
    return response
