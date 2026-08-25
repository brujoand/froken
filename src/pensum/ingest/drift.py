"""Detecting when the vendored catalogue has fallen behind Udir.

The 2026 turnover is the case this exists for: all six core curricula were
superseded on a single date, with every competence goal renumbered. Nothing in
the repo would have noticed -- the committed data stays valid-looking forever,
and quiz items keyed to retired goal codes fail silently rather than loudly.

Cheap by design. It reads only the index endpoint, so it can run on a schedule
without being a burden on a public service that offers no SLA.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

# `UdirClient` is imported inside `check()` rather than here: it pulls in httpx,
# and everything above `check` is pure. That keeps the comparison logic -- and
# its tests -- importable without an HTTP client installed. The suite's actual
# network isolation comes from the autouse fixture in tests/conftest.py, not
# from dependency absence; httpx arrives transitively via Starlette's TestClient
# regardless.

# How far ahead to warn about an expiry. A curriculum revision means re-authoring
# every quiz item that references a renumbered goal, so a quarter's notice is the
# difference between planned work and an emergency.
EXPIRY_HORIZON = timedelta(days=90)


@dataclass(frozen=True)
class Finding:
    """One reason the catalogue needs attention."""

    subject: str
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.subject}: {self.detail}"


def _parse(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


def _split_revision(code: str) -> tuple[str, int] | None:
    """MAT01-06 -> ("MAT01", 6). None if the code is not revision-suffixed."""
    base, _, revision = code.rpartition("-")
    return (base, int(revision)) if base and revision.isdigit() else None


def _newer_revision(code: str, index: dict[str, Any]) -> str | None:
    """The highest published revision of `code`'s subject above `code` itself."""
    parsed = _split_revision(code)
    if parsed is None:
        return None
    base, revision = parsed

    newer = [
        other
        for other in index
        if (candidate := _split_revision(other))
        and candidate[0] == base
        and candidate[1] > revision
    ]
    return max(newer, key=lambda c: _split_revision(c)[1]) if newer else None


def compare(manifest: dict[str, Any], upstream: list[dict[str, Any]], today: date) -> list[Finding]:
    """Compare the committed manifest against Udir's current index.

    Pure, so the interesting cases are testable without a network call.
    """
    index = {
        entry["kode"]: entry for entry in upstream if isinstance(entry, dict) and entry.get("kode")
    }
    vendored: dict[str, Any] = manifest.get("subjects", {})
    findings: list[Finding] = []

    for code, meta in sorted(vendored.items()):
        entry = index.get(code)
        if entry is None:
            findings.append(
                Finding(code, "withdrawn", "no longer listed upstream; it may have been withdrawn")
            )
            continue

        if (upstream_modified := entry.get("sist-endret")) and upstream_modified != meta.get(
            "last_modified"
        ):
            findings.append(
                Finding(
                    code,
                    "modified",
                    f"changed upstream (sist-endret {meta.get('last_modified')} -> {upstream_modified})",
                )
            )

        # Supersession is the signal that matters most -- a successor renumbers
        # every goal, so it orphans quiz items rather than merely dating them.
        #
        # It cannot be read directly here: the index endpoint carries no
        # `erstattes-av`, only the per-subject detail does. Rather than fetch 50
        # detail documents on every scheduled run, infer it from a higher
        # revision of the same base code appearing upstream. NOR01-07 is why
        # this matters -- it has no `gyldig-til` at all, so a newer NOR01-08 in
        # the index is the *only* warning that it has been replaced.
        if successor := _newer_revision(code, index):
            findings.append(
                Finding(code, "superseded", f"{successor} is published and supersedes it")
            )

        valid_to = _parse(entry.get("gyldig-til"))
        if valid_to and valid_to < today:
            findings.append(Finding(code, "expired", f"expired on {valid_to}"))
        elif valid_to and valid_to - today <= EXPIRY_HORIZON:
            findings.append(
                Finding(
                    code, "expiring", f"expires on {valid_to}, in {(valid_to - today).days} days"
                )
            )

    return findings


async def check(manifest_path: Path, cache_dir: Path, today: date) -> list[Finding]:
    """Fetch Udir's index and report what has drifted."""
    from pensum.ingest.client import UdirClient

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    async with UdirClient(cache_dir, refresh=True) as client:
        index = await client.get("laereplaner-lk20")

    entries = index if isinstance(index, list) else index.get("data", [])
    return compare(manifest, entries, today)
