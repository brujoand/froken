"""Validate authored items against the curriculum catalogue.

Runs as a pre-commit hook and in CI, offline. Schema validity is only half of
it: an item can be perfectly well-formed and still reference a competence goal
that no longer exists, which is exactly what a curriculum revision produces.
That failure is silent at runtime -- the item simply never gets selected -- so it
is made loud here instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml
from pydantic import ValidationError

from froken.catalogue.loader import Catalogue
from froken.items.loader import DEFAULT_ITEMS_DIR
from froken.items.schema import ItemSet


def validate(items_dir: Path | None = None) -> list[str]:
    """Return a problem per line. Empty means everything checks out."""
    directory = items_dir or DEFAULT_ITEMS_DIR
    catalogue = Catalogue.load()
    problems: list[str] = []
    item_sets: list[ItemSet] = []

    for path in sorted(directory.glob("*/*.yaml")):
        where = path.relative_to(directory.parent)
        try:
            item_sets.append(
                ItemSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            )
        except (ValidationError, yaml.YAMLError) as exc:
            problems.append(f"{where}: {exc}")

    for item_set in item_sets:
        subject = catalogue.subject(item_set.subject)
        if subject is None:
            problems.append(f"{item_set.goal_set}: unknown subject {item_set.subject}")
            continue

        goal_set = subject.goal_set(item_set.goal_set)
        if goal_set is None:
            problems.append(
                f"{item_set.goal_set}: not a goal set of {item_set.subject}; "
                "the curriculum may have been revised"
            )
            continue

        # The check that matters: a renumbered goal orphans its items.
        known = {goal.code for goal in goal_set.goals}
        for item in item_set.items:
            if item.goal not in known:
                problems.append(
                    f"{item.id}: goal {item.goal} is not in {item_set.goal_set}; "
                    "it may have been renumbered by a curriculum revision"
                )
        for excused in item_set.not_assessable:
            if excused.goal not in known:
                problems.append(
                    f"{item_set.goal_set}: not_assessable names {excused.goal}, "
                    "which is not in this goal set"
                )

        # Every goal should be either tested or explicitly excused. Silence
        # about a goal is indistinguishable from forgetting it.
        unaccounted = sorted(known - item_set.goals_covered - item_set.goals_excused)
        if unaccounted:
            problems.append(
                f"{item_set.goal_set}: {len(unaccounted)} goal(s) neither tested nor "
                f"marked not_assessable: {', '.join(unaccounted)}"
            )

    return problems


def main() -> int:
    problems = validate()
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        print(f"\n{len(problems)} problem(s) found in authored items.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
