"""Domain model for the LK20 curriculum catalogue.

Mirrors Udir's own hierarchy -- laereplan (subject) -> kompetansemaalsett (goal
set) -> kompetansemaal (goal) -- because re-verifying our data against the
official source stays trivial when the shapes match.

Pure data. No I/O, no framework imports.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field

# Udir's language codes, as they appear in a `tittel.tekst[].spraak` field.
BOKMAAL = "nob"
NYNORSK = "nno"
ENGLISH = "eng"
DEFAULT = "default"

# Resolution order when the requested language is missing. Norwegian degrades to
# the other maalform before it degrades to English -- a Norwegian pupil is better
# served by nynorsk than by English.
FALLBACK_CHAINS: dict[str, tuple[str, ...]] = {
    BOKMAAL: (BOKMAAL, DEFAULT, NYNORSK, ENGLISH),
    NYNORSK: (NYNORSK, DEFAULT, BOKMAAL, ENGLISH),
    ENGLISH: (ENGLISH, BOKMAAL, DEFAULT, NYNORSK),
}


class Frozen(BaseModel):
    """Base for the immutable catalogue models."""

    model_config = ConfigDict(frozen=True)


class LocalisedText(Frozen):
    """A piece of curriculum text in every maalform Udir published it in.

    Udir supplies bokmaal, nynorsk, English and the Sami languages for each
    competence goal, so we never translate curriculum text ourselves -- it is a
    legal `forskrift` text and paraphrasing it would be a correctness bug.
    """

    by_language: dict[str, str] = Field(default_factory=dict)

    def get(self, language: str) -> str:
        """Return the text in `language`, falling back per FALLBACK_CHAINS.

        Falls back to any available text rather than raising: a missing
        translation should degrade the page, not break it.
        """
        for candidate in FALLBACK_CHAINS.get(language, (language, DEFAULT, BOKMAAL)):
            if text := self.by_language.get(candidate):
                return text
        return next(iter(self.by_language.values()), "")

    def has(self, language: str) -> bool:
        """True if `language` is present verbatim, without falling back."""
        return bool(self.by_language.get(language))


class Goal(Frozen):
    """A single kompetansemaal -- one thing a pupil should be able to do.

    `code` is Udir's stable KM identifier. Quiz items key off it, which is why a
    curriculum revision that renumbers KM codes orphans every item written
    against the old generation.
    """

    code: str
    text: LocalisedText
    core_elements: tuple[str, ...] = ()
    basic_skills: tuple[str, ...] = ()
    cross_curricular: tuple[str, ...] = ()

    @property
    def source_url(self) -> str:
        """The official Udir record, so every goal we display is verifiable."""
        return f"https://data.udir.no/kl06/v201906/kompetansemaal-lk20/{self.code}"


class GoalSet(Frozen):
    """The goals a pupil is expected to have reached by the end of `after_year`.

    LK20 defines these at checkpoints, not every year -- after 2., 4., 7. and 10.
    trinn for most subjects, though matematikk uniquely defines every trinn.
    `applies_to_years` comes from Udir's own `benyttes-paa-aarstrinn`, so the
    mapping from a pupil's klasse to their checkpoint is authoritative rather
    than something we invented.
    """

    code: str
    title: LocalisedText
    after_year: int
    applies_to_years: tuple[int, ...]
    goals: tuple[Goal, ...]

    def goal(self, code: str) -> Goal | None:
        return next((g for g in self.goals if g.code == code), None)


class Subject(Frozen):
    """A laereplan -- one school subject, at one curriculum revision.

    `code` carries the revision (MAT01-06, not MAT01), and `valid_from` /
    `valid_to` bound when it is in force. Both matter: LK20 subjects are
    periodically superseded, and the successor renumbers every goal.
    """

    code: str
    title: LocalisedText
    valid_from: date | None = None
    valid_to: date | None = None
    replaces: tuple[str, ...] = ()
    replaced_by: tuple[str, ...] = ()
    last_modified: str | None = None
    goal_sets: tuple[GoalSet, ...] = ()

    @property
    def base_code(self) -> str:
        """The revision-independent part, e.g. MAT01-06 -> MAT01."""
        return self.code.rsplit("-", 1)[0]

    def is_in_force(self, on: date) -> bool:
        not_yet = self.valid_from is not None and on < self.valid_from
        expired = self.valid_to is not None and on > self.valid_to
        return not (not_yet or expired)

    def goal_set(self, code: str) -> GoalSet | None:
        return next((gs for gs in self.goal_sets if gs.code == code), None)
