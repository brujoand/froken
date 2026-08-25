"""Loading the vendored curriculum catalogue.

The app reads only what is committed under `data/curriculum/`. It never calls
Udir at request time, so a pupil's page load never depends on an upstream service
that offers no SLA -- and the container needs no network access at all.
"""

from __future__ import annotations

from functools import cached_property
from pathlib import Path

from pensum.domain.models import Subject

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "curriculum"


class Catalogue:
    """An immutable, indexed view of every vendored subject."""

    def __init__(self, subjects: list[Subject]) -> None:
        self._subjects = sorted(subjects, key=lambda s: s.code)

    @classmethod
    def load(cls, data_dir: Path | None = None) -> Catalogue:
        directory = (data_dir or DEFAULT_DATA_DIR) / "subjects"
        subjects = [
            Subject.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(directory.glob("*.json"))
        ]
        if not subjects:
            raise FileNotFoundError(
                f"no curriculum found in {directory}; run `pensum-ingest` to populate it"
            )
        return cls(subjects)

    @property
    def subjects(self) -> list[Subject]:
        return list(self._subjects)

    @cached_property
    def _by_code(self) -> dict[str, Subject]:
        return {s.code: s for s in self._subjects}

    @cached_property
    def _goals_by_code(self) -> dict[str, str]:
        """Goal code -> owning subject code. Used to validate quiz items."""
        return {
            goal.code: subject.code
            for subject in self._subjects
            for goal_set in subject.goal_sets
            for goal in goal_set.goals
        }

    def subject(self, code: str) -> Subject | None:
        return self._by_code.get(code)

    def has_goal(self, code: str) -> bool:
        return code in self._goals_by_code

    def goal_codes(self) -> set[str]:
        return set(self._goals_by_code)
