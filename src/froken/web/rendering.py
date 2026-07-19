"""Shared template plumbing.

Extracted so the catalogue and quiz routers render through one implementation:
`locale` and `lang` must be threaded identically everywhere, or a page ends up
with one locale's chrome around another's content.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.templating import Jinja2Templates

from froken.i18n import SUPPORTED_LOCALES, curriculum_language, translate

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)


def validate_locale(locale: str) -> None:
    if locale not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=404, detail="unknown locale")


def context(request: Request, locale: str, **extra: object) -> dict[str, object]:
    """Base template context.

    `lang` is the Udir maalform for curriculum text; `t` translates our own
    strings. Keeping them distinct in the context is what stops a template
    quietly rendering official text through the UI translation path.
    """
    return {
        "request": request,
        "locale": locale,
        "lang": curriculum_language(locale),
        "t": lambda key, **kwargs: translate(locale, key, **kwargs),
        "locales": SUPPORTED_LOCALES,
        **extra,
    }
