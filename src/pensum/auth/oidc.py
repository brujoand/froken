"""The OIDC authorization-code flow, with PKCE.

Written against the spec rather than against a library, because the surface
Pensum needs is small and a generic OAuth client is mostly code for grant types
we will never use.

One deliberate omission: the id_token's signature is not verified. It is fetched
by us, over TLS, directly from the provider's token endpoint in exchange for a
code we just minted -- which is the case OpenID Connect Core 3.1.3.7 clause 6
explicitly allows to skip signature validation. There is no path here by which a
token reaches us via the browser, so there is no JWKS to fetch and no key
rotation to get wrong. The claims that *can* be lied about without a signature
-- issuer, audience, nonce -- are all checked below.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from pensum.auth.models import User
from pensum.config import Settings

DISCOVERY_PATH = "/.well-known/openid-configuration"

# `groups` is what pocket-id emits group membership under, and it is the whole
# reason admin does not need a hardcoded allowlist. `email` is requested because
# providers commonly refuse to emit a display name without it.
SCOPES = "openid profile email groups"

# The provider is a service on the same network, not a third party over the
# internet. A slow one should surface as an error page, not a hung worker.
TIMEOUT = httpx.Timeout(10.0)


class OidcError(RuntimeError):
    """The flow failed. The message is for the log, never for the pupil."""


@dataclass(frozen=True)
class Provider:
    """The endpoints discovery told us about."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str | None = None
    end_session_endpoint: str | None = None

    @classmethod
    def from_document(cls, document: dict[str, object]) -> Provider:
        def required(key: str) -> str:
            value = document.get(key)
            if not isinstance(value, str) or not value:
                raise OidcError(f"discovery document is missing {key}")
            return value

        def optional(key: str) -> str | None:
            value = document.get(key)
            return value if isinstance(value, str) and value else None

        return cls(
            issuer=required("issuer"),
            authorization_endpoint=required("authorization_endpoint"),
            token_endpoint=required("token_endpoint"),
            userinfo_endpoint=optional("userinfo_endpoint"),
            end_session_endpoint=optional("end_session_endpoint"),
        )


def new_verifier() -> str:
    """A PKCE code verifier: 43-128 characters of unreserved alphabet."""
    return secrets.token_urlsafe(64)


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def decode_claims(id_token: str) -> dict[str, object]:
    """Read a JWT's payload without verifying its signature.

    Safe only because of where the token came from -- see the module docstring.
    """
    parts = id_token.split(".")
    if len(parts) != 3:
        raise OidcError("id_token is not a JWT")
    payload = parts[1]
    # JWT strips base64 padding; Python's decoder insists on it.
    padded = payload + "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (ValueError, json.JSONDecodeError) as exc:
        raise OidcError("id_token payload is not readable JSON") from exc
    if not isinstance(claims, dict):
        raise OidcError("id_token payload is not an object")
    return claims


def verify_claims(claims: dict[str, object], *, issuer: str, client_id: str, nonce: str) -> None:
    """Check the three claims a forged token would have to get wrong.

    Without signature verification these are what stand between us and a token
    minted elsewhere: it must come from our provider, be addressed to us, and
    answer the nonce we generated for this specific browser.
    """
    if claims.get("iss") != issuer:
        raise OidcError("id_token issuer does not match the configured provider")

    audience = claims.get("aud")
    audiences = audience if isinstance(audience, list) else [audience]
    if client_id not in audiences:
        raise OidcError("id_token was not issued for this client")

    # An absent nonce is as bad as a wrong one: it is the only thing tying the
    # token to the browser that started the flow.
    if claims.get("nonce") != nonce:
        raise OidcError("id_token nonce does not match the one we sent")


def user_from_claims(claims: dict[str, object]) -> User:
    """Turn verified claims into the little we keep.

    The display name falls back through the claims a provider might actually
    populate, and finally to the subject -- an admin page listing "unknown" for
    half a class helps nobody.
    """
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub:
        raise OidcError("id_token carries no subject")

    name = next(
        (
            claims[key]
            for key in ("name", "preferred_username", "nickname", "email")
            if isinstance(claims.get(key), str) and claims[key]
        ),
        sub,
    )

    raw_groups = claims.get("groups")
    groups = (
        tuple(g for g in raw_groups if isinstance(g, str)) if isinstance(raw_groups, list) else ()
    )

    return User(sub=sub, name=str(name), groups=groups)


class OidcClient:
    """Talks to the provider. One instance per app, discovery cached."""

    def __init__(self, settings: Settings) -> None:
        if not settings.auth_enabled:
            raise OidcError("sign-in is not configured")
        # Narrowed for the type checker; auth_enabled already proved all three.
        self.issuer = str(settings.oidc_issuer)
        self.client_id = str(settings.oidc_client_id)
        self.client_secret = str(settings.oidc_client_secret)
        self._provider: Provider | None = None

    async def provider(self) -> Provider:
        """Discovery, fetched once and then remembered.

        A provider that moves its endpoints does so on a restart's timescale,
        not a request's, and re-fetching per sign-in would make every login wait
        on two round trips instead of one.
        """
        if self._provider is None:
            async with httpx.AsyncClient(timeout=TIMEOUT) as client:
                response = await client.get(f"{self.issuer}{DISCOVERY_PATH}")
            if response.status_code != 200:
                raise OidcError(f"discovery failed with HTTP {response.status_code}")
            self._provider = Provider.from_document(response.json())
        return self._provider

    def authorization_url(
        self, provider: Provider, *, redirect_uri: str, state: str, nonce: str, verifier: str
    ) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.client_id,
                "redirect_uri": redirect_uri,
                "scope": SCOPES,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge_for(verifier),
                "code_challenge_method": "S256",
            }
        )
        return f"{provider.authorization_endpoint}?{query}"

    async def exchange(
        self, provider: Provider, *, code: str, redirect_uri: str, verifier: str
    ) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.post(
                provider.token_endpoint,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code_verifier": verifier,
                },
                headers={"Accept": "application/json"},
            )
        if response.status_code != 200:
            raise OidcError(f"token exchange failed with HTTP {response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("id_token"), str):
            raise OidcError("token response carried no id_token")
        return payload

    async def groups_from_userinfo(self, provider: Provider, access_token: str) -> tuple[str, ...]:
        """Ask userinfo for groups, for providers that omit them from the token.

        Pocket-id puts groups in the id_token, so this is a fallback rather than
        the path. It exists because the alternative failure -- an admin who is
        in the group and still gets a 403 -- is opaque to diagnose from the
        outside.
        """
        if provider.userinfo_endpoint is None:
            return ()
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            response = await client.get(
                provider.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
        if response.status_code != 200:
            return ()
        payload = response.json()
        raw = payload.get("groups") if isinstance(payload, dict) else None
        return tuple(g for g in raw if isinstance(g, str)) if isinstance(raw, list) else ()
