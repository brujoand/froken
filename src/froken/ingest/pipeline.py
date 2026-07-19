"""Fetch the grunnskole curriculum from Udir and vendor it as reviewable JSON.

Discovery is by validity date, not a hardcoded list of curriculum codes. That is
the load-bearing decision in this module: LK20 subjects are periodically
superseded, and the successor renumbers every competence goal. Selecting by date
turns a curriculum revision into a re-run of this script rather than a rewrite of
the repo.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from froken import __version__
from froken.domain.models import Goal, GoalSet, Subject
from froken.ingest.client import UdirClient
from froken.ingest.normalise import goal_from, goal_set_from, subject_from

PUBLISHED = "status_publisert"
GRUNNSKOLE = "opplaeringsnivaa_grunnskole"


def _is_published(entry: dict[str, Any]) -> bool:
    return str(entry.get("status", "")).endswith(PUBLISHED)


def _in_force(entry: dict[str, Any], on: date) -> bool:
    def parse(value: Any) -> date | None:
        if not isinstance(value, str):
            return None
        try:
            return datetime.fromisoformat(value).date()
        except ValueError:
            return None

    valid_from, valid_to = parse(entry.get("gyldig-fra")), parse(entry.get("gyldig-til"))
    not_yet = valid_from is not None and on < valid_from
    expired = valid_to is not None and on > valid_to
    return not (not_yet or expired)


async def discover(client: UdirClient, as_of: date) -> list[str]:
    """Curriculum codes that are published and in force on `as_of`.

    The index endpoint carries validity and status for every plan in one call,
    so the expensive per-subject fetches only happen for real candidates.
    """
    index = await client.get("laereplaner-lk20")
    entries = index if isinstance(index, list) else index.get("data", [])
    return [
        entry["kode"]
        for entry in entries
        if isinstance(entry, dict)
        and entry.get("kode")
        and _is_published(entry)
        and _in_force(entry, as_of)
    ]


async def _fetch_object(client: UdirClient, resource: str, code: str) -> dict[str, Any]:
    """Fetch a Grep resource that must be a JSON object.

    Every detail endpoint returns one; a list here means the API shape changed
    under us, which should fail the ingest loudly rather than produce a subject
    with silently missing goals.
    """
    payload = await client.get(resource, code)
    if not isinstance(payload, dict):
        raise TypeError(f"expected an object from {resource}/{code}, got {type(payload).__name__}")
    return payload


async def _fetch_goal(client: UdirClient, code: str) -> Goal:
    return goal_from(await _fetch_object(client, "kompetansemaal-lk20", code))


async def _fetch_goal_set(client: UdirClient, code: str) -> GoalSet | None:
    payload = await _fetch_object(client, "kompetansemaalsett-lk20", code)

    # Cheap check before fetching every goal individually: a VGS or yrkesfag set
    # is discarded anyway, and there are a lot of them.
    if goal_set_from(payload, []) is None:
        return None

    goals = await asyncio.gather(
        *(_fetch_goal(client, ref["kode"]) for ref in payload.get("kompetansemaal", []))
    )
    return goal_set_from(payload, list(goals))


async def fetch_subject(client: UdirClient, code: str) -> Subject | None:
    """Fetch one curriculum in full, or None if it is not a grunnskole subject."""
    payload = await _fetch_object(client, "laereplaner-lk20", code)

    levels = {
        str(entry.get("kode", "")) for entry in payload.get("opplaeringsnivaa") or [] if entry
    }
    if not any(level.endswith("grunnskole") for level in levels):
        return None

    refs = payload.get("kompetansemaal-kapittel", {}).get("kompetansemaalsett", [])
    goal_sets = await asyncio.gather(*(_fetch_goal_set(client, ref["kode"]) for ref in refs))
    resolved = [gs for gs in goal_sets if gs is not None]
    if not resolved:
        return None

    return subject_from(payload, resolved)


def _drop_superseded(subjects: list[Subject]) -> list[Subject]:
    """Discard a revision when the revision that replaces it is also present.

    Date filtering alone is not enough: NOR01-07 carries no `gyldig-til`, so it
    reads as in force indefinitely even though NOR01-08 supersedes it on
    2026-08-01. Shipping both would give norsk two competing sets of goals for
    the same grade, and `erstattes-av` is the only signal that disambiguates
    them.
    """
    present = {s.code for s in subjects}
    return [s for s in subjects if not any(code in present for code in s.replaced_by)]


def _serialise(subject: Subject) -> str:
    """Stable JSON, so a curriculum change shows up as a reviewable diff.

    The diff *is* the review artifact when upstream revises a subject, which is
    why sorting and indentation matter more here than they usually would.
    """
    # Trailing newline included deliberately: without it every re-ingest fights
    # the end-of-file-fixer hook, and the resulting churn buries the real diff.
    return (
        json.dumps(
            subject.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


async def ingest(
    out_dir: Path, cache_dir: Path, as_of: date, *, refresh: bool = False
) -> list[Subject]:
    """Fetch every in-force grunnskole curriculum and write it under `out_dir`."""
    subjects_dir = out_dir / "subjects"
    subjects_dir.mkdir(parents=True, exist_ok=True)

    async with UdirClient(cache_dir, refresh=refresh) as client:
        codes = await discover(client, as_of)
        fetched = await asyncio.gather(*(fetch_subject(client, code) for code in codes))

    subjects = _drop_superseded(sorted((s for s in fetched if s is not None), key=lambda s: s.code))

    manifest: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "ingested_with": __version__,
        "source": "https://data.udir.no/kl06/v201906/",
        "subjects": {},
    }

    for subject in subjects:
        body = _serialise(subject)
        (subjects_dir / f"{subject.code}.json").write_text(body, encoding="utf-8")
        manifest["subjects"][subject.code] = {
            "valid_from": subject.valid_from.isoformat() if subject.valid_from else None,
            "valid_to": subject.valid_to.isoformat() if subject.valid_to else None,
            "replaced_by": list(subject.replaced_by),
            "last_modified": subject.last_modified,
            "goal_sets": len(subject.goal_sets),
            "goals": sum(len(gs.goals) for gs in subject.goal_sets),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        }

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return subjects
