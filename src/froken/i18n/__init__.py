"""UI translations.

Deliberately separate from curriculum text. Those are different problems with
different sources: competence goals arrive from Udir already translated and are
reproduced verbatim, while these are our own strings. Merging the two mechanisms
would invite paraphrasing official text, which the NLOD terms forbid.

Bokmål is the source locale, so a missing translation degrades to Norwegian
rather than to English -- the client's first language is Norwegian.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Any

import yaml

from froken.domain.models import BOKMAAL, ENGLISH, NYNORSK

LOCALES_DIR = Path(__file__).parent / "locales"

# UI locale -> the Udir maalform its curriculum text should be rendered in.
CURRICULUM_LANGUAGE = {"nb": BOKMAAL, "nn": NYNORSK, "en": ENGLISH}

DEFAULT_LOCALE = "nb"
# Bokmaal and nynorsk are maalform of the same language, not a translation pair.
# Only `nb` and `en` have UI chrome; `nn` reuses the bokmaal chrome while showing
# nynorsk curriculum text, which is what a nynorsk reader actually needs.
UI_LOCALES = ("nb", "en")
SUPPORTED_LOCALES = ("nb", "nn", "en")


def _flatten(data: dict[str, Any], prefix: str = "") -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = str(value)
    return flat


@cache
def catalog(locale: str) -> dict[str, str]:
    """Flat `a.b.c` -> text mapping for one UI locale."""
    path = LOCALES_DIR / f"{locale}.yaml"
    if not path.exists():
        return {}
    return _flatten(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def ui_locale(locale: str) -> str:
    """The locale whose chrome to use. Nynorsk reads bokmål chrome."""
    return locale if locale in UI_LOCALES else DEFAULT_LOCALE


def translate(locale: str, key: str, **kwargs: Any) -> str:
    """Look up `key`, falling back to bokmål and then to the key itself.

    Returning the key rather than raising keeps a missing string from taking down
    a page; the i18n parity check is what stops one reaching production.
    """
    chrome = ui_locale(locale)
    text = catalog(chrome).get(key) or catalog(DEFAULT_LOCALE).get(key) or key
    return text.format(**kwargs) if kwargs else text


def curriculum_language(locale: str) -> str:
    """The Udir maalform code to render competence-goal text in."""
    return CURRICULUM_LANGUAGE.get(locale, BOKMAAL)
