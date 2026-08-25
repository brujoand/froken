"""Sign-in: token handling, cookies, and the redirect dance.

The id_token's signature is deliberately not verified (see `pensum.auth.oidc`),
which puts the whole weight of the flow's integrity on three things: the state
parameter, the nonce, and the audience check. Those get the hardest tests here.
"""

from __future__ import annotations

import base64
import json

import pytest
from fastapi.testclient import TestClient

from pensum.auth.cookies import FLOW_COOKIE, LOGIN_COOKIE, CookieCodec, LoginFlow
from pensum.auth.models import User
from pensum.auth.oidc import (
    OidcClient,
    OidcError,
    Provider,
    challenge_for,
    decode_claims,
    new_verifier,
    user_from_claims,
    verify_claims,
)
from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.web.app import create_app
from pensum.web.deps import safe_next

ISSUER = "https://id.example.com"
CLIENT_ID = "pensum"

PROVIDER = Provider(
    issuer=ISSUER,
    authorization_endpoint=f"{ISSUER}/authorize",
    token_endpoint=f"{ISSUER}/api/oidc/token",
    userinfo_endpoint=f"{ISSUER}/api/oidc/userinfo",
)


def id_token(**claims: object) -> str:
    """A JWT with a real payload and a nonsense signature.

    The signature is never read, so a test that forged a valid one would be
    testing nothing the code does.
    """
    payload = {"iss": ISSUER, "aud": CLIENT_ID, "sub": "u-1", "nonce": "n", **claims}
    encode = lambda raw: base64.urlsafe_b64encode(raw).decode().rstrip("=")  # noqa: E731
    return ".".join(
        [
            encode(json.dumps({"alg": "RS256"}).encode()),
            encode(json.dumps(payload).encode()),
            "not-a-signature",
        ]
    )


def auth_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "oidc_issuer": ISSUER,
        "oidc_client_id": CLIENT_ID,
        "oidc_client_secret": "s3cret",
        "admin_group": "pensum-admins",
        "base_url": "https://pensum.example.com",
        "session_secret": "test-secret",
    }
    return Settings(**(defaults | overrides))


# Claims -------------------------------------------------------------------


def test_decode_claims_reads_an_unpadded_payload() -> None:
    """JWTs strip base64 padding; Python's decoder insists on it."""
    claims = decode_claims(id_token(name="Åse", groups=["pensum-admins"]))
    assert claims["name"] == "Åse"
    assert claims["groups"] == ["pensum-admins"]


@pytest.mark.parametrize("token", ["", "a.b", "not.a.jwt"])
def test_decode_claims_refuses_a_non_token(token: str) -> None:
    with pytest.raises(OidcError):
        decode_claims(token)


def test_verify_claims_accepts_our_own_token() -> None:
    verify_claims(decode_claims(id_token()), issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_verify_claims_rejects_another_issuer() -> None:
    claims = decode_claims(id_token(iss="https://evil.example.com"))
    with pytest.raises(OidcError, match="issuer"):
        verify_claims(claims, issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_verify_claims_rejects_a_token_minted_for_someone_else() -> None:
    """Without a signature check, audience is what stops token replay."""
    claims = decode_claims(id_token(aud="some-other-client"))
    with pytest.raises(OidcError, match="issued for this client"):
        verify_claims(claims, issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_verify_claims_accepts_an_audience_list_containing_us() -> None:
    claims = decode_claims(id_token(aud=["other", CLIENT_ID]))
    verify_claims(claims, issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_verify_claims_rejects_a_wrong_nonce() -> None:
    claims = decode_claims(id_token(nonce="somebody-elses"))
    with pytest.raises(OidcError, match="nonce"):
        verify_claims(claims, issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_verify_claims_rejects_a_missing_nonce() -> None:
    """Absent is as bad as wrong: it is the only tie to this browser."""
    claims = decode_claims(id_token())
    del claims["nonce"]
    with pytest.raises(OidcError, match="nonce"):
        verify_claims(claims, issuer=ISSUER, client_id=CLIENT_ID, nonce="n")


def test_user_from_claims_prefers_a_real_name() -> None:
    user = user_from_claims(
        {"sub": "u-1", "name": "Ola", "preferred_username": "ola", "groups": ["pupils"]}
    )
    assert user == User(sub="u-1", name="Ola", groups=("pupils",))


def test_user_from_claims_falls_back_through_to_the_subject() -> None:
    """A roster of "unknown" helps nobody; the id is at least distinguishing."""
    assert user_from_claims({"sub": "u-2"}).name == "u-2"
    assert user_from_claims({"sub": "u-2", "email": "a@example.com"}).name == "a@example.com"


def test_user_from_claims_ignores_a_malformed_groups_claim() -> None:
    assert user_from_claims({"sub": "u-1", "groups": "admins"}).groups == ()


# PKCE ---------------------------------------------------------------------


def test_pkce_challenge_is_the_unpadded_s256_of_the_verifier() -> None:
    verifier = new_verifier()
    challenge = challenge_for(verifier)
    assert "=" not in challenge
    assert challenge != verifier
    assert challenge_for(verifier) == challenge


# Cookies ------------------------------------------------------------------


def test_a_login_cookie_round_trips() -> None:
    codec = CookieCodec("secret")
    user = User(sub="u-1", name="Ola", groups=("pensum-admins",))
    assert codec.load_login(codec.dump_login(user)) == user


def test_a_login_cookie_signed_with_another_secret_is_not_signed_in() -> None:
    """Tampering, expiry and a restarted process all collapse to "not signed in"."""
    forged = CookieCodec("other-secret").dump_login(User(sub="u-1", name="Ola"))
    assert CookieCodec("secret").load_login(forged) is None


def test_a_flow_cookie_cannot_be_replayed_as_a_login_cookie() -> None:
    """One secret, two salts -- and this is why the salts have to differ."""
    codec = CookieCodec("secret")
    flow = codec.dump_flow(LoginFlow(state="s", nonce="n", verifier="v", next_url="/nb/"))
    assert codec.load_login(flow) is None


@pytest.mark.parametrize(
    "candidate", [None, "", "https://evil.example.com", "//evil.example.com", "nb/"]
)
def test_open_redirects_are_refused(candidate: str | None) -> None:
    assert safe_next(candidate, "/nb/") == "/nb/"


def test_a_local_path_survives_as_a_redirect_target() -> None:
    assert safe_next("/nb/klasse/2/MAT01-06", "/nb/") == "/nb/klasse/2/MAT01-06"


# Routes -------------------------------------------------------------------


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An app with sign-in configured, and discovery stubbed out.

    The suite refuses real HTTP, so the provider is faked at the two methods
    that would leave the machine.

    Driven over https, because the cookies are marked Secure whenever the
    configured origin is -- a client on http would silently never send them
    back, and every assertion here would pass for the wrong reason.
    """

    async def provider(self: OidcClient) -> Provider:
        return PROVIDER

    monkeypatch.setattr(OidcClient, "provider", provider)
    return TestClient(
        create_app(Catalogue.load(), settings=auth_settings()),
        base_url="https://pensum.example.com",
    )


def test_login_redirects_to_the_provider_with_pkce_and_state(client: TestClient) -> None:
    response = client.get("/auth/login?next=/nb/klasse/2", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{ISSUER}/authorize?")
    assert "code_challenge_method=S256" in location
    assert f"client_id={CLIENT_ID}" in location
    # The redirect URI must be the configured origin, not the test client's.
    assert "redirect_uri=https%3A%2F%2Fpensum.example.com%2Fauth%2Fcallback" in location
    assert FLOW_COOKIE in response.cookies


def test_login_is_not_a_route_when_sign_in_is_unconfigured() -> None:
    plain = TestClient(create_app(Catalogue.load(), settings=Settings()))
    assert plain.get("/auth/login").status_code == 404


def test_a_completed_callback_signs_the_user_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = _start_flow(client)

    async def exchange(self: OidcClient, provider: Provider, **kwargs: object) -> dict[str, object]:
        assert kwargs["code"] == "the-code"
        assert kwargs["verifier"] == flow.verifier
        return {"id_token": id_token(nonce=flow.nonce, name="Ola", groups=["pupils"])}

    monkeypatch.setattr(OidcClient, "exchange", exchange)

    response = client.get(
        f"/auth/callback?code=the-code&state={flow.state}", follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/nb/klasse/2"

    codec = CookieCodec("test-secret")
    assert codec.load_login(client.cookies[LOGIN_COOKIE]) == User(
        sub="u-1", name="Ola", groups=("pupils",)
    )


def test_a_callback_with_a_forged_state_does_not_sign_anyone_in(client: TestClient) -> None:
    """The CSRF defence: the callback must answer the flow this browser began."""
    _start_flow(client)
    response = client.get("/auth/callback?code=c&state=forged", follow_redirects=False)

    assert response.status_code == 303
    assert "signin=failed" in response.headers["location"]
    assert LOGIN_COOKIE not in response.cookies


def test_a_callback_without_a_flow_cookie_is_refused(client: TestClient) -> None:
    response = client.get("/auth/callback?code=c&state=s", follow_redirects=False)
    assert "signin=failed" in response.headers["location"]
    assert LOGIN_COOKIE not in response.cookies


def test_a_provider_error_leaves_the_pupil_able_to_carry_on(client: TestClient) -> None:
    """Sign-in is optional, so its failure is a notice, never an error page."""
    _start_flow(client)
    response = client.get("/auth/callback?error=access_denied", follow_redirects=True)

    assert response.status_code == 200
    assert "Innloggingen gikk ikke gjennom" in response.text


def test_a_token_answering_the_wrong_nonce_does_not_sign_anyone_in(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    flow = _start_flow(client)

    async def exchange(self: OidcClient, provider: Provider, **kwargs: object) -> dict[str, object]:
        return {"id_token": id_token(nonce="a-different-login")}

    monkeypatch.setattr(OidcClient, "exchange", exchange)
    response = client.get(f"/auth/callback?code=c&state={flow.state}", follow_redirects=False)

    assert "signin=failed" in response.headers["location"]
    assert LOGIN_COOKIE not in response.cookies


def test_groups_are_fetched_from_userinfo_when_the_token_omits_them(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Not pocket-id's behaviour, but the alternative failure is a silent 403."""
    flow = _start_flow(client)

    async def exchange(self: OidcClient, provider: Provider, **kwargs: object) -> dict[str, object]:
        return {"id_token": id_token(nonce=flow.nonce, name="Kari"), "access_token": "at"}

    async def groups(self: OidcClient, provider: Provider, token: str) -> tuple[str, ...]:
        assert token == "at"
        return ("pensum-admins",)

    monkeypatch.setattr(OidcClient, "exchange", exchange)
    monkeypatch.setattr(OidcClient, "groups_from_userinfo", groups)

    client.get(f"/auth/callback?code=c&state={flow.state}", follow_redirects=False)
    user = CookieCodec("test-secret").load_login(client.cookies[LOGIN_COOKIE])
    assert user is not None and user.groups == ("pensum-admins",)


def test_logout_clears_the_login_cookie(client: TestClient) -> None:
    client.cookies.set(LOGIN_COOKIE, CookieCodec("test-secret").dump_login(User("u-1", "Ola")))
    response = client.post("/auth/logout?next=/nb/", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/nb/"
    # Asserted on the header rather than the client's jar: expiring a cookie is
    # something the response does, and the jar only reflects it when the
    # domain and path happen to line up.
    expiry = response.headers["set-cookie"]
    assert f'{LOGIN_COOKIE}=""' in expiry
    assert "Max-Age=0" in expiry


def test_logout_refuses_a_get(client: TestClient) -> None:
    """A GET would let any page sign a pupil out with an <img> tag."""
    assert client.get("/auth/logout").status_code == 405


def test_cookies_are_not_marked_secure_on_a_plain_http_deployment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secure is derived, not assumed -- `bin/run_local` serves over http."""

    async def provider(self: OidcClient) -> Provider:
        return PROVIDER

    monkeypatch.setattr(OidcClient, "provider", provider)
    local = TestClient(
        create_app(Catalogue.load(), settings=auth_settings(base_url="http://localhost:8000"))
    )
    response = local.get("/auth/login", follow_redirects=False)
    assert "secure" not in response.headers["set-cookie"].lower()


def _start_flow(client: TestClient) -> LoginFlow:
    """Drive /auth/login and read back the flow it stored in the cookie."""
    client.get("/auth/login?next=/nb/klasse/2", follow_redirects=False)
    flow = CookieCodec("test-secret").load_flow(client.cookies[FLOW_COOKIE])
    assert flow is not None
    return flow
