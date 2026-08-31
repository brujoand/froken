"""Validate reading passages and speed bands against the curriculum catalogue.

Runs as a pre-commit hook and in CI, offline. The failures it catches are all
silent at runtime: a passage keyed to a renumbered goal is simply never served,
and a checkpoint with passages but no band shows a pace with nothing to read it
against. Both look like "the feature is quiet today" rather than like a bug.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from pensum.catalogue.loader import Catalogue
from pensum.reading.library import DEFAULT_READING_DIR, NORMS_FILE
from pensum.reading.schema import NormTable, ReadingSet


def validate(reading_dir: Path | None = None) -> list[str]:
    """Return a problem per line. Empty means everything checks out."""
    directory = reading_dir or DEFAULT_READING_DIR
    catalogue = Catalogue.load()
    problems: list[str] = []
    reading_sets: list[ReadingSet] = []

    for path in sorted(directory.glob("*/*.yaml")):
        where = path.relative_to(directory.parent)
        try:
            reading_sets.append(
                ReadingSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            )
        except (ValidationError, yaml.YAMLError) as exc:
            problems.append(f"{where}: {exc}")

    norms_path = directory / NORMS_FILE
    norms = NormTable()
    if not norms_path.exists():
        problems.append(f"{NORMS_FILE} is missing; every passage would show a pace with no band")
    else:
        try:
            norms = NormTable.model_validate(yaml.safe_load(norms_path.read_text(encoding="utf-8")))
        except (ValidationError, yaml.YAMLError) as exc:
            problems.append(f"{NORMS_FILE}: {exc}")

    for reading_set in reading_sets:
        subject = catalogue.subject(reading_set.subject)
        if subject is None:
            problems.append(f"{reading_set.goal_set}: unknown subject {reading_set.subject}")
            continue

        goal_set = subject.goal_set(reading_set.goal_set)
        if goal_set is None:
            problems.append(
                f"{reading_set.goal_set}: not a goal set of {reading_set.subject}; "
                "the curriculum may have been revised"
            )
            continue

        # The check that matters: a renumbered goal orphans its passages.
        known = {goal.code for goal in goal_set.goals}
        for text in reading_set.texts:
            if text.goal not in known:
                problems.append(
                    f"{text.id}: goal {text.goal} is not in {reading_set.goal_set}; "
                    "it may have been renumbered by a curriculum revision"
                )

        if norms.band(subject.code, goal_set.after_year) is None:
            problems.append(
                f"{reading_set.goal_set}: passages exist but no band is defined for "
                f"{subject.code} after {goal_set.after_year}. trinn"
            )

    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s) found in reading data.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
