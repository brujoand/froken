"""Loading authored quiz items from disk."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

import yaml

from froken.domain.models import GoalSet
from froken.items.coverage import Coverage, coverage
from froken.items.schema import ItemSet, QuizItem

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ITEMS_DIR = REPO_ROOT / "data" / "items"


class ItemBank:
    """Every authored item, indexed by goal set."""

    def __init__(self, item_sets: list[ItemSet], *, include_unreviewed: bool = False) -> None:
        self._sets = {item_set.goal_set: item_set for item_set in item_sets}
        self._include_unreviewed = include_unreviewed

    @classmethod
    def load(cls, items_dir: Path | None = None, *, include_unreviewed: bool = False) -> ItemBank:
        directory = items_dir or DEFAULT_ITEMS_DIR
        item_sets = [
            ItemSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(directory.glob("*/*.yaml"))
        ]
        return cls(item_sets, include_unreviewed=include_unreviewed)

    @property
    def item_sets(self) -> list[ItemSet]:
        return list(self._sets.values())

    def for_goal_set(self, code: str) -> list[QuizItem]:
        """Servable items for a goal set.

        Unreviewed items are withheld unless explicitly enabled. Generation
        writes them as unreviewed, so this is what keeps a draft question from
        reaching a child on the strength of a merge alone.
        """
        item_set = self._sets.get(code)
        if item_set is None:
            return []
        return [item for item in item_set.items if self._include_unreviewed or item.reviewed]

    def has_quiz(self, code: str) -> bool:
        return bool(self.for_goal_set(code))

    def tested_goals(self, code: str) -> set[str]:
        """Goal codes a served item actually tests, for the given goal set."""
        return {item.goal for item in self.for_goal_set(code)}

    def coverage(self, goal_set: GoalSet) -> Coverage:
        """How much of `goal_set` its quiz reaches, goal by goal."""
        return coverage(goal_set, self.tested_goals(goal_set.code))

    @cached_property
    def goal_codes(self) -> set[str]:
        """Every goal referenced by an item or excused as not assessable."""
        return {
            code
            for item_set in self._sets.values()
            for code in item_set.goals_covered | item_set.goals_excused
        }
