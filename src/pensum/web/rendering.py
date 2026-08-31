"""Shared template plumbing.

Extracted so the catalogue and quiz routers render through one implementation:
`locale` and `lang` must be threaded identically everywhere, or a page ends up
with one locale's chrome around another's content.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from pensum.i18n import SUPPORTED_LOCALES, curriculum_language, translate
from pensum.web.deps import current_user, get_settings, sees_unreviewed

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def _day(value: datetime) -> str:
    """Day-precision, Norwegian order.

    Deliberately not the time of day: an admin needs to know a quiz was taken on
    Tuesday, not that it was at 20:14, and the minute a child sat down is
    surveillance rather than information.
    """
    return value.strftime("%d.%m.%Y")


templates.env.filters["day"] = _day


def validate_locale(locale: str) -> None:
    if locale not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=404, detail="unknown locale")


def context(request: Request, locale: str, **extra: object) -> dict[str, object]:
    """Base template context.

    `lang` is the Udir maalform for curriculum text; `t` translates our own
    strings. Keeping them distinct in the context is what stops a template
    quietly rendering official text through the UI translation path.

    `user` and `is_admin` are threaded through every page because the header
    renders on every page. When sign-in is not configured both are falsy and no
    template shows anything about accounts -- which is the whole default
    experience, not a degraded one.
    """
    settings = get_settings(request)
    user = current_user(request)
    return {
        "request": request,
        "locale": locale,
        "lang": curriculum_language(locale),
        "t": lambda key, **kwargs: translate(locale, key, **kwargs),
        "locales": SUPPORTED_LOCALES,
        "auth_enabled": settings.auth_enabled,
        # The footer carries the takedown contact on every page, so it is part
        # of the base context rather than something one page remembers to pass.
        "dmca_email": settings.dmca_email,
        "history_enabled": settings.history_enabled,
        "user": user,
        "is_admin": user is not None and user.in_group(settings.admin_group),
        # Whether this reader is being shown content no human has signed off.
        # In the context rather than passed per page, because every template
        # that can render a draft has to be able to label it -- an unmarked
        # draft is worse than a hidden one, since the reader cannot tell that
        # what they are judging is the thing awaiting judgement.
        "drafts_visible": sees_unreviewed(request),
        **extra,
    }
