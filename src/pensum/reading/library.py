"""Loading reading passages and speed bands from disk.

Same shape as `pensum.items.loader`, deliberately: one YAML file per checkpoint,
loaded once at startup, immutable afterwards. A passage that has not been read
by a human is withheld under the same switch that withholds an unreviewed quiz
item.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pensum.reading.schema import NormTable, ReadingNorm, ReadingSet, ReadingText

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_READING_DIR = REPO_ROOT / "data" / "reading"
NORMS_FILE = "norms.yaml"


class ReadingLibrary:
    """Every authored passage, indexed by goal set, plus the speed bands."""

    def __init__(
        self,
        reading_sets: list[ReadingSet],
        norms: NormTable,
        *,
        include_unreviewed: bool = False,
    ) -> None:
        self._sets = {reading_set.goal_set: reading_set for reading_set in reading_sets}
        self._norms = norms
        self._include_unreviewed = include_unreviewed

    @classmethod
    def load(
        cls, reading_dir: Path | None = None, *, include_unreviewed: bool = False
    ) -> ReadingLibrary:
        directory = reading_dir or DEFAULT_READING_DIR
        reading_sets = [
            ReadingSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            for path in sorted(directory.glob("*/*.yaml"))
        ]
        norms_path = directory / NORMS_FILE
        norms = (
            NormTable.model_validate(yaml.safe_load(norms_path.read_text(encoding="utf-8")))
            if norms_path.exists()
            else NormTable()
        )
        return cls(reading_sets, norms, include_unreviewed=include_unreviewed)

    @property
    def norms(self) -> NormTable:
        return self._norms

    @property
    def reading_sets(self) -> list[ReadingSet]:
        return list(self._sets.values())

    def for_goal_set(self, code: str, *, unreviewed: bool = False) -> list[ReadingText]:
        """Servable passages for a goal set.

        Same contract as `ItemBank.for_goal_set`: withheld by default, widened
        by the deployment-wide switch or by a request that has established it
        may see drafts. False at every layer, so a forgotten argument fails
        closed.
        """
        reading_set = self._sets.get(code)
        if reading_set is None:
            return []
        widened = self._include_unreviewed or unreviewed
        return [t for t in reading_set.texts if widened or t.reviewed]

    def has_reading(self, code: str, *, unreviewed: bool = False) -> bool:
        return bool(self.for_goal_set(code, unreviewed=unreviewed))

    def text(self, goal_set: str, text_id: str, *, unreviewed: bool = False) -> ReadingText | None:
        return next(
            (t for t in self.for_goal_set(goal_set, unreviewed=unreviewed) if t.id == text_id),
            None,
        )

    def band(self, subject: str, after_year: int) -> ReadingNorm | None:
        return self._norms.band(subject, after_year)

    @property
    def goal_codes(self) -> set[str]:
        """Every goal a passage claims to exercise, for the orphan check."""
        return {text.goal for rs in self._sets.values() for text in rs.texts}
