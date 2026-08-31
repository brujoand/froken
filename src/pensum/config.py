"""Runtime configuration.

Everything has a working default. The container must start with no environment
set at all -- and when it does, Pensum behaves exactly as it always has: no
sign-in, nothing written to disk, nothing recorded about anyone.

Two optional subsystems change that, and both are off unless explicitly
configured:

* **Sign-in** turns on when an OIDC issuer, client id and client secret are all
  present. Signing in is never required -- an anonymous pupil gets the same quiz
  and is still not recorded.
* **Score history** turns on when a database path is set. Only attempts by a
  signed-in pupil are ever written, so the two switches are independent but only
  useful together.

Keeping both off by default is what keeps "the public image needs no
configuration and stores nothing" structurally true rather than documented.
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


def _text(name: str) -> str | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return raw.strip() or None


@dataclass(frozen=True)
class Settings:
    """Settings read once at startup."""

    # Generated items are written unreviewed and withheld until a human has read
    # them. Enabling this is for reviewing drafts locally; a released build
    # leaves it off, so a merge alone never puts an unread question in front of
    # a child.
    include_unreviewed_items: bool = False

    # --- Reading fluency ----------------------------------------------------
    # Where the CTranslate2 Whisper models live -- either one model per
    # language in "nb"/"en" subdirectories, or a single multilingual model at
    # this path. Unset -- the default, and what the published image ships with
    # -- means the reading page still shows the passage and times the reading,
    # but nothing is transcribed and no accuracy is reported. The models are
    # hundreds of megabytes and are not ours to redistribute, so they are
    # fetched rather than committed: see bin/fetch_speech_models.
    speech_model_dir: Path | None = None

    # Whether to light words up while the pupil is still reading. Costs real
    # CPU: a window of recent audio is transcribed every couple of seconds, on
    # top of the single pass at the end that produces the score. Worth it on a
    # machine serving a household, and the first thing to turn off on one
    # serving a school. Ignored entirely when no models are configured.
    speech_live: bool = True

    # --- Sign-in (pocket-id, or any OIDC provider) --------------------------
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None

    # The group a pocket-id account must be in to read other people's scores.
    # Membership is managed in pocket-id, not here, so granting or revoking
    # admin never means redeploying Pensum.
    admin_group: str = "pensum-admins"

    # Pensum's own public origin, used to build the redirect URI. Derived from
    # the incoming request when unset, which is right for a direct deployment
    # and wrong behind a proxy that terminates TLS -- hence the override.
    base_url: str | None = None

    # Signs the login cookie. Generated per process when unset, so a restart
    # silently signs everyone out. That is the right default for a secret we
    # would otherwise have to invent: losing a session is an inconvenience, a
    # predictable signing key is not.
    session_secret: str = ""

    # --- Score history ------------------------------------------------------
    # Unset means attempts are scored, shown and then forgotten, exactly as
    # before. Set it to record them -- and mount it on a volume, or a restart
    # takes the history with it.
    database_path: Path | None = None

    @property
    def speech_enabled(self) -> bool:
        """Whether a recording can be checked, as opposed to merely timed.

        Only says a directory was configured and exists; whether vosk is
        importable and a model actually loads is settled in
        `pensum.reading.transcribe.load_transcriber`, which degrades to None.
        """
        return self.speech_model_dir is not None and self.speech_model_dir.is_dir()

    @property
    def auth_enabled(self) -> bool:
        """Sign-in needs all three halves of an OIDC client to work at all."""
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)

    @property
    def history_enabled(self) -> bool:
        """Recording a score needs both somewhere to put it and a name to put on it."""
        return self.database_path is not None and self.auth_enabled

    @classmethod
    def from_env(cls) -> Settings:
        database = _text("PENSUM_DATABASE_PATH")
        speech_models = _text("PENSUM_SPEECH_MODEL_DIR")
        issuer = _text("PENSUM_OIDC_ISSUER")
        base_url = _text("PENSUM_BASE_URL")
        return cls(
            include_unreviewed_items=_flag("PENSUM_INCLUDE_UNREVIEWED"),
            speech_model_dir=Path(speech_models) if speech_models else None,
            speech_live=_flag("PENSUM_SPEECH_LIVE", default=True),
            # Trailing slashes matter: the issuer is concatenated with the
            # discovery path, and `aud`/`iss` comparisons are exact.
            oidc_issuer=issuer.rstrip("/") if issuer else None,
            oidc_client_id=_text("PENSUM_OIDC_CLIENT_ID"),
            oidc_client_secret=_text("PENSUM_OIDC_CLIENT_SECRET"),
            admin_group=_text("PENSUM_ADMIN_GROUP") or "pensum-admins",
            base_url=base_url.rstrip("/") if base_url else None,
            session_secret=_text("PENSUM_SESSION_SECRET") or secrets.token_urlsafe(32),
            database_path=Path(database) if database else None,
        )


settings = Settings.from_env()
