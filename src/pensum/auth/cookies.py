"""Signed cookies: who is signed in, and one in-flight login.

Both cookies are signed and read back with a maximum age, and neither is ever
trusted as an identifier on its own -- a tampered or stale cookie reads as "not
signed in", which is the same state a first-time visitor is in.

Server-side session storage would be the alternative. It is not worth it here: a
name and a group list are all that is kept, the provider remains the authority
on both, and a signed cookie means a restart does not log out a room full of
children.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from pensum.auth.models import User

LOGIN_COOKIE = "pensum_session"
FLOW_COOKIE = "pensum_login_flow"

# A school day, so a pupil is not asked to sign in again between homework and
# bedtime. Short enough that a shared family tablet does not stay signed in as
# one child indefinitely.
LOGIN_MAX_AGE = 12 * 60 * 60
# The round trip to the provider and back. Anything longer is an abandoned tab.
FLOW_MAX_AGE = 10 * 60


@dataclass(frozen=True)
class LoginFlow:
    """The bits of a login that have to survive the trip to the provider."""

    state: str
    nonce: str
    verifier: str
    next_url: str


class CookieCodec:
    """Signs and reads Pensum's two cookies with one secret, two salts.

    Distinct salts matter: a login-flow cookie must not be replayable as a login
    cookie even though both are signed by the same key.
    """

    def __init__(self, secret: str) -> None:
        self._login = URLSafeTimedSerializer(secret, salt="pensum-login")
        self._flow = URLSafeTimedSerializer(secret, salt="pensum-login-flow")

    def dump_login(self, user: User) -> str:
        return self._login.dumps(user.as_claims())

    def load_login(self, raw: str) -> User | None:
        payload = _load(self._login, raw, LOGIN_MAX_AGE)
        return User.from_cookie(payload) if payload is not None else None

    def dump_flow(self, flow: LoginFlow) -> str:
        return self._flow.dumps(
            {
                "state": flow.state,
                "nonce": flow.nonce,
                "verifier": flow.verifier,
                "next": flow.next_url,
            }
        )

    def load_flow(self, raw: str) -> LoginFlow | None:
        payload = _load(self._flow, raw, FLOW_MAX_AGE)
        if not isinstance(payload, dict):
            return None
        values = [payload.get(key) for key in ("state", "nonce", "verifier", "next")]
        if not all(isinstance(v, str) and v for v in values):
            return None
        state, nonce, verifier, next_url = values
        return LoginFlow(state=state, nonce=nonce, verifier=verifier, next_url=next_url)


def _load(serializer: URLSafeTimedSerializer, raw: str, max_age: int) -> object | None:
    try:
        return serializer.loads(raw, max_age=max_age)
    except (BadSignature, SignatureExpired):
        # Expired, tampered with, or signed by a previous process's generated
        # secret. All three mean the same thing to a caller.
        return None


def read_user(request: Request, codec: CookieCodec) -> User | None:
    raw = request.cookies.get(LOGIN_COOKIE)
    return codec.load_login(raw) if raw else None


def read_flow(request: Request, codec: CookieCodec) -> LoginFlow | None:
    raw = request.cookies.get(FLOW_COOKIE)
    return codec.load_flow(raw) if raw else None


def set_login(response: Response, codec: CookieCodec, user: User, *, secure: bool) -> None:
    _set(response, LOGIN_COOKIE, codec.dump_login(user), LOGIN_MAX_AGE, secure=secure)


def set_flow(response: Response, codec: CookieCodec, flow: LoginFlow, *, secure: bool) -> None:
    _set(response, FLOW_COOKIE, codec.dump_flow(flow), FLOW_MAX_AGE, secure=secure)


def clear_login(response: Response) -> None:
    response.delete_cookie(LOGIN_COOKIE, path="/")


def clear_flow(response: Response) -> None:
    response.delete_cookie(FLOW_COOKIE, path="/")


def _set(response: Response, name: str, value: str, max_age: int, *, secure: bool) -> None:
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path="/",
        httponly=True,
        # Lax, not Strict: the provider redirects the browser back to us with a
        # top-level GET, and Strict would drop the flow cookie on exactly that
        # navigation.
        samesite="lax",
        secure=secure,
    )
