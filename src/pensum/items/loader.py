"""Loading authored quiz items from disk."""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

import yaml

from pensum.domain.models import GoalSet
from pensum.items.coverage import Coverage, coverage
from pensum.items.schema import ItemSet, QuizItem

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

    def for_goal_set(self, code: str, *, unreviewed: bool = False) -> list[QuizItem]:
        """Servable items for a goal set.

        Unreviewed items are withheld unless something explicitly asks for them.
        Generation writes them as unreviewed, so this is what keeps a draft
        question from reaching a child on the strength of a merge alone.

        Two things can ask. `include_unreviewed` on the bank is the
        deployment-wide switch, which is what a local review session uses.
        `unreviewed=True` per call is for a request that has established it may
        see them -- an administrator, who needs to read a draft in place before
        deciding whether to mark it reviewed.

        The default is False at every layer. A caller that forgets the argument
        gets the safe answer, which is the only acceptable direction for this
        particular mistake.
        """
        item_set = self._sets.get(code)
        if item_set is None:
            return []
        widened = self._include_unreviewed or unreviewed
        return [item for item in item_set.items if widened or item.reviewed]

    def has_quiz(self, code: str, *, unreviewed: bool = False) -> bool:
        return bool(self.for_goal_set(code, unreviewed=unreviewed))

    def tested_goals(self, code: str, *, unreviewed: bool = False) -> set[str]:
        """Goal codes a served item actually tests, for the given goal set."""
        return {item.goal for item in self.for_goal_set(code, unreviewed=unreviewed)}

    def coverage(self, goal_set: GoalSet, *, unreviewed: bool = False) -> Coverage:
        """How much of `goal_set` its quiz reaches, goal by goal.

        Follows whatever the caller can see, so an administrator reading drafts
        is shown the coverage those drafts actually produce rather than the
        coverage a pupil would get.
        """
        return coverage(goal_set, self.tested_goals(goal_set.code, unreviewed=unreviewed))

    @cached_property
    def goal_codes(self) -> set[str]:
        """Every goal referenced by an item or excused as not assessable."""
        return {
            code
            for item_set in self._sets.values()
            for code in item_set.goals_covered | item_set.goals_excused
        }
