"""Turning Udir Grep API payloads into our domain models.

Kept free of I/O so it can be tested against recorded fixtures. The API has a
few shapes that break naive parsers, and each is handled here:

  * `tittel` is sometimes a plain string and sometimes a multilingual
    `{"tekst": [{"spraak": ..., "verdi": ...}]}` object, depending on the
    endpoint.
  * Related-entity lists wrap their payload in a `referanse` key.
  * Goal-set titles are unreliable free text -- observed in the wild:
    "7.trinn" (no space), "10. trinn " (trailing space), and both "vg1" and
    "Vg1". The aarstrinn *codes* are clean, so we never parse the titles.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from pensum.domain.models import Goal, GoalSet, LocalisedText, Subject

_AARSTRINN = re.compile(r"^aarstrinn(\d+)$")


def localised(value: Any) -> LocalisedText:
    """Read a Udir localised value in every language it carries.

    Three shapes in the wild: a bare string (goal-set titles), a `{"tekst": [...]}`
    wrapper (`tittel`), and a bare list of language entries (`kortform`).
    """
    if value is None:
        return LocalisedText()
    if isinstance(value, str):
        return LocalisedText(by_language={"default": value})

    entries = value if isinstance(value, list) else value.get("tekst")
    if not entries:
        return LocalisedText()

    by_language = {
        entry["spraak"]: entry["verdi"]
        for entry in entries
        if entry.get("spraak") and entry.get("verdi")
    }
    return LocalisedText(by_language=by_language)


def _refs(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    """Codes from a related-entity list, unwrapping Udir's `referanse` nesting."""
    out: list[str] = []
    for entry in payload.get(key) or []:
        if not isinstance(entry, dict):
            continue
        target = entry.get("referanse") if "referanse" in entry else entry
        if isinstance(target, dict) and (code := target.get("kode")):
            out.append(code)
    return tuple(out)


def _years(payload: dict[str, Any], key: str) -> tuple[int, ...]:
    """School years from an aarstrinn list, e.g. `aarstrinn2` -> 2.

    Non-grunnskole levels (`vg1`, `vg2`, ...) do not match and are dropped, which
    is exactly how VGS and yrkesfag goal sets get filtered out of a grunnskole
    ingest.
    """
    years: list[int] = []
    for entry in payload.get(key) or []:
        target = entry.get("referanse", entry) if isinstance(entry, dict) else {}
        if match := _AARSTRINN.match(str(target.get("kode", ""))):
            years.append(int(match.group(1)))
    return tuple(sorted(years))


def _parse_date(value: Any) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def goal_from(payload: dict[str, Any]) -> Goal:
    """Build a Goal from a `kompetansemaal-lk20/{KM}` response.

    This endpoint is the only source of multilingual goal text -- the goal-set
    endpoint returns a single maalform and ignores `?lang=`.
    """
    return Goal(
        code=payload["kode"],
        text=localised(payload.get("tittel")),
        core_elements=_refs(payload, "tilknyttede-kjerneelementer"),
        basic_skills=_refs(payload, "tilknyttede-grunnleggende-ferdigheter"),
        cross_curricular=_refs(payload, "tilknyttede-tverrfaglige-temaer"),
    )


def goal_set_from(payload: dict[str, Any], goals: list[Goal]) -> GoalSet | None:
    """Build a GoalSet from a `kompetansemaalsett-lk20/{KV}` response.

    Returns None for sets that are not grunnskole -- those have no `aarstrinnN`
    in `etter-aarstrinn`. NAT01-04 alone carries twelve such sets (one per
    yrkesfag programme), so this filter is load-bearing, not defensive.
    """
    after = _years(payload, "etter-aarstrinn")
    if not after:
        return None

    return GoalSet(
        code=payload["kode"],
        title=localised(payload.get("tittel")),
        after_year=after[-1],
        # Falls back to the checkpoint year itself if upstream omits the field,
        # so the set is never silently unreachable from any grade.
        applies_to_years=_years(payload, "benyttes-paa-aarstrinn") or after,
        goals=tuple(goals),
    )


def subject_from(payload: dict[str, Any], goal_sets: list[GoalSet]) -> Subject:
    """Build a Subject from a `laereplaner-lk20/{KODE}` response."""
    period = payload.get("gyldighetsperiode") or {}

    def _period(key: str) -> date | None:
        # The date lives under `dato`. The sibling `overskrift` is a localised
        # label template ("Gjelder fra <>"), not a value -- reading `verdi` off
        # it yields the label and silently loses every validity window.
        value = period.get(key)
        return _parse_date(value.get("dato") if isinstance(value, dict) else value)

    return Subject(
        code=payload["kode"],
        title=localised(payload.get("tittel")),
        short_title=localised(payload.get("kortform")),
        valid_from=_period("gyldig-fra"),
        valid_to=_period("gyldig-til"),
        replaces=_refs(payload, "erstatter"),
        replaced_by=_refs(payload, "erstattes-av"),
        last_modified=payload.get("sist-endret"),
        goal_sets=tuple(sorted(goal_sets, key=lambda gs: gs.after_year)),
    )
