"""Validate writing prompts and letterforms against the curriculum catalogue.

Runs as a pre-commit hook and in CI, offline. The failures it catches are all
silent at runtime: a prompt keyed to a renumbered goal is simply never served,
and a prompt asking for a character the alphabet cannot draw is dropped by the
library rather than shown as an empty box. Both look like "the feature is quiet
today" rather than like a bug.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from pensum.catalogue.loader import Catalogue
from pensum.writing.library import ALPHABET_FILE, DEFAULT_WRITING_DIR
from pensum.writing.schema import Alphabet, WritingSet


def validate(writing_dir: Path | None = None) -> list[str]:
    """Return a problem per line. Empty means everything checks out."""
    directory = writing_dir or DEFAULT_WRITING_DIR
    catalogue = Catalogue.load()
    problems: list[str] = []
    writing_sets: list[WritingSet] = []

    for path in sorted(directory.glob("*/*.yaml")):
        where = path.relative_to(directory.parent)
        try:
            writing_sets.append(
                WritingSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            )
        except (ValidationError, yaml.YAMLError) as exc:
            problems.append(f"{where}: {exc}")

    alphabet_path = directory / ALPHABET_FILE
    alphabet: Alphabet | None = None
    if not alphabet_path.exists():
        problems.append(f"{ALPHABET_FILE} is missing; there would be nothing to trace")
    else:
        try:
            alphabet = Alphabet.model_validate(
                yaml.safe_load(alphabet_path.read_text(encoding="utf-8"))
            )
        except (ValidationError, yaml.YAMLError) as exc:
            problems.append(f"{ALPHABET_FILE}: {exc}")

    for writing_set in writing_sets:
        subject = catalogue.subject(writing_set.subject)
        if subject is None:
            problems.append(f"{writing_set.goal_set}: unknown subject {writing_set.subject}")
            continue

        goal_set = subject.goal_set(writing_set.goal_set)
        if goal_set is None:
            problems.append(
                f"{writing_set.goal_set}: not a goal set of {writing_set.subject}; "
                "the curriculum may have been revised"
            )
            continue

        # The check that matters: a renumbered goal orphans its prompts.
        known = {goal.code for goal in goal_set.goals}
        for prompt in writing_set.prompts:
            if prompt.goal not in known:
                problems.append(
                    f"{prompt.id}: goal {prompt.goal} is not in {writing_set.goal_set}; "
                    "it may have been renumbered by a curriculum revision"
                )
            if alphabet is not None:
                undrawable = [char for char in prompt.text if alphabet.glyph(char) is None]
                if undrawable:
                    problems.append(
                        f"{prompt.id}: the alphabet cannot draw {undrawable}; "
                        "the prompt would be withheld rather than shown"
                    )

    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s) found in writing data.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
