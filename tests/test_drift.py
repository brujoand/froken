"""Drift detection.

`compare` is pure, so every interesting case is testable without touching the
network. The cases that matter are the ones that would otherwise be silent:
a curriculum that is replaced without ever being marked expired.
"""

from __future__ import annotations

from datetime import date

from pensum.ingest.drift import compare

TODAY = date(2026, 7, 19)


def entry(code: str, *, valid_to: str | None = None, modified: str = "2026-01-01T00:00:00") -> dict:
    return {"kode": code, "gyldig-til": valid_to, "sist-endret": modified, "status": "publisert"}


def manifest(*codes: str, modified: str = "2026-01-01T00:00:00") -> dict:
    return {"subjects": {code: {"last_modified": modified} for code in codes}}


def kinds(findings: list) -> set[str]:
    return {f.kind for f in findings}


def test_current_catalogue_reports_nothing() -> None:
    findings = compare(manifest("MAT01-06"), [entry("MAT01-06")], TODAY)
    assert findings == []


def test_supersession_is_caught_without_any_expiry_date() -> None:
    """The NOR01-07 case, and the reason this check exists.

    Norsk was replaced by NOR01-08 while carrying no `gyldig-til` at all, so it
    reads as valid indefinitely. Nothing but the presence of a higher revision
    upstream reveals that the vendored copy is stale -- and the index endpoint,
    which is all this check reads, carries no `erstattes-av` to consult.
    """
    findings = compare(
        manifest("NOR01-07"),
        [entry("NOR01-07", valid_to=None), entry("NOR01-08", valid_to=None)],
        TODAY,
    )
    assert "superseded" in kinds(findings)
    assert "NOR01-08" in str(findings[0])


def test_only_the_highest_successor_is_reported() -> None:
    findings = compare(
        manifest("MAT01-05"),
        [entry("MAT01-05"), entry("MAT01-06"), entry("MAT01-07")],
        TODAY,
    )
    superseded = [f for f in findings if f.kind == "superseded"]
    assert len(superseded) == 1
    assert "MAT01-07" in str(superseded[0])


def test_a_revision_below_ours_is_not_a_successor() -> None:
    """The outgoing generation lingers in the index; it must not read as newer."""
    findings = compare(manifest("MAT01-06"), [entry("MAT01-06"), entry("MAT01-05")], TODAY)
    assert "superseded" not in kinds(findings)


def test_unrelated_subjects_are_not_confused_for_revisions() -> None:
    """MAT01 and MAT03 are different subjects, not revisions of one another."""
    findings = compare(manifest("MAT01-06"), [entry("MAT01-06"), entry("MAT03-02")], TODAY)
    assert findings == []


def test_expiry_inside_the_horizon_warns() -> None:
    findings = compare(
        manifest("MUS01-02"), [entry("MUS01-02", valid_to="2026-07-31T00:00:00")], TODAY
    )
    assert "expiring" in kinds(findings)


def test_expiry_far_out_is_quiet() -> None:
    findings = compare(
        manifest("KRO01-06"), [entry("KRO01-06", valid_to="2030-07-31T00:00:00")], TODAY
    )
    assert findings == []


def test_already_expired_is_reported_as_expired_not_expiring() -> None:
    findings = compare(
        manifest("OLD01-01"), [entry("OLD01-01", valid_to="2025-07-31T00:00:00")], TODAY
    )
    assert "expired" in kinds(findings)
    assert "expiring" not in kinds(findings)


def test_upstream_edit_is_reported() -> None:
    findings = compare(
        manifest("SAF01-05", modified="2026-01-01T00:00:00"),
        [entry("SAF01-05", modified="2026-06-01T00:00:00")],
        TODAY,
    )
    assert "modified" in kinds(findings)


def test_subject_withdrawn_upstream_is_reported() -> None:
    """Silence here would be indistinguishable from everything being fine."""
    findings = compare(manifest("GONE01-01"), [entry("MAT01-06")], TODAY)
    assert "withdrawn" in kinds(findings)
