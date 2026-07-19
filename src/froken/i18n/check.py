"""Fail the build if the UI locales have drifted apart.

A missing translation degrades silently at runtime -- the string falls back to
bokmål and an English page quietly shows a Norwegian sentence. That is exactly
the kind of bug nobody files, so it gets a deterministic check instead.
"""

from __future__ import annotations

import sys

from froken.i18n import DEFAULT_LOCALE, UI_LOCALES, catalog


def main() -> int:
    catalogs = {locale: catalog(locale) for locale in UI_LOCALES}
    reference = set(catalogs[DEFAULT_LOCALE])

    problems: list[str] = []
    for locale, entries in catalogs.items():
        if locale == DEFAULT_LOCALE:
            continue
        if missing := sorted(reference - set(entries)):
            problems.append(f"{locale}.yaml is missing: {', '.join(missing)}")
        if extra := sorted(set(entries) - reference):
            problems.append(
                f"{locale}.yaml has keys absent from {DEFAULT_LOCALE}.yaml: {', '.join(extra)}"
            )

    if not reference:
        problems.append(f"{DEFAULT_LOCALE}.yaml is empty or missing")

    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
