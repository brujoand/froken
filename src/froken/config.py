"""Runtime configuration.

Everything has a working default. The container must start with no environment
set at all -- there are no secrets, no API keys and no external services, and
that should stay structurally true rather than documented.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    """Settings read once at startup."""

    # Generated items are written unreviewed and withheld until a human has read
    # them. Enabling this is for reviewing drafts locally; a released build
    # leaves it off, so a merge alone never puts an unread question in front of
    # a child.
    include_unreviewed_items: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        return cls(include_unreviewed_items=_flag("FROKEN_INCLUDE_UNREVIEWED"))


settings = Settings.from_env()
