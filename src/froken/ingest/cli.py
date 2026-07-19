"""Command line entry point for the curriculum ingest."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date
from pathlib import Path

from froken.ingest.drift import check
from froken.ingest.pipeline import ingest

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUT = REPO_ROOT / "data" / "curriculum"
DEFAULT_CACHE = DEFAULT_OUT / "raw"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest the LK20 grunnskole curriculum from Udir.")
    parser.add_argument(
        "--check-drift",
        action="store_true",
        help=(
            "Report how the vendored catalogue differs from Udir without writing "
            "anything. Exits non-zero when something needs attention."
        ),
    )
    parser.add_argument(
        "--as-of",
        type=date.fromisoformat,
        default=date.today(),
        help=(
            "Select curricula in force on this date (YYYY-MM-DD). Use a future date to "
            "ingest a published-but-not-yet-in-force revision."
        ),
    )
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE)
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Bypass the on-disk cache and re-fetch everything from Udir.",
    )
    args = parser.parse_args(argv)

    if args.check_drift:
        findings = asyncio.run(check(args.out / "manifest.json", args.cache, args.as_of))
        if not findings:
            print(f"Catalogue is current as of {args.as_of}.")
            return 0
        print(f"{len(findings)} curriculum change(s) need attention:\n")
        for finding in findings:
            print(f"  [{finding.kind}] {finding}")
        print("\nRe-ingest with: froken-ingest --as-of <date when the new plans take effect>")
        return 1

    subjects = asyncio.run(ingest(args.out, args.cache, args.as_of, refresh=args.refresh))

    print(f"Ingested {len(subjects)} grunnskole subjects in force on {args.as_of}:")
    for subject in subjects:
        goals = sum(len(gs.goals) for gs in subject.goal_sets)
        print(f"  {subject.code:<12} {len(subject.goal_sets)} goal sets, {goals} goals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
