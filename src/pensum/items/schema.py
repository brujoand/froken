"""Quiz item schema.

Items are ours, not Udir's. Competence goals say what a pupil should be able to
do; turning that into a question is authorship, and the result must never be
mistakable for official curriculum text.

Two properties are load-bearing:

  * An item names the competence goal it tests. A curriculum revision renumbers
    goal codes, so this is what makes orphaned items a loud CI failure rather
    than a silent one.
  * A goal may honestly have no items. Many kompetansemaal describe things a
    pupil *does* -- utforske, samtale om, delta i -- which no written question
    can check. Recording that as `not_assessable` keeps coverage honest;
    inventing a proxy question would misrepresent the goal.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pensum.items.figures import Figure
from pensum.items.text import BOKMAAL, ENGLISH, AuthoredText

# Re-exported: `AuthoredText` reads as part of the item schema even though it
# lives next door so that figures can use it without an import cycle.
__all__ = [
    "BOKMAAL",
    "ENGLISH",
    "AuthoredText",
    "Choice",
    "ItemSet",
    "NotAssessable",
    "QuizItem",
]

ItemKind = Literal["multiple_choice", "numeric", "short_text"]

MIN_CHOICES = 3
MAX_DIFFICULTY = 3


class Choice(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    text: AuthoredText
    correct: bool = False


class QuizItem(BaseModel):
    """One question, testing one competence goal."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    type: ItemKind
    # Relative to the checkpoint, not absolute: a hard 2. trinn item is not a
    # hard 10. trinn item.
    difficulty: int = Field(ge=1, le=MAX_DIFFICULTY)
    prompt: AuthoredText
    explanation: AuthoredText

    # A picture of what the prompt describes, where the prompt describes
    # something you are supposed to see. Optional by construction: most
    # questions do not need one, and a figure that adds nothing is clutter on a
    # page a seven-year-old is reading.
    figure: Figure | None = None

    choices: tuple[Choice, ...] = ()
    answer: float | None = None
    tolerance: float = 0.0
    accept: dict[str, tuple[str, ...]] = Field(default_factory=dict)

    # The released build serves only reviewed items. Generation sets this false;
    # a human sets it true.
    reviewed: bool = False
    reviewed_by: str | None = None

    @model_validator(mode="after")
    def _check_shape_matches_kind(self) -> QuizItem:
        if self.type == "multiple_choice":
            if len(self.choices) < MIN_CHOICES:
                raise ValueError(f"{self.id}: multiple_choice needs at least {MIN_CHOICES} choices")
            correct = [c for c in self.choices if c.correct]
            if len(correct) != 1:
                raise ValueError(f"{self.id}: needs exactly one correct choice, got {len(correct)}")
            if len({c.id for c in self.choices}) != len(self.choices):
                raise ValueError(f"{self.id}: choice ids must be unique")

        elif self.type == "numeric":
            if self.answer is None:
                raise ValueError(f"{self.id}: numeric items need an answer")
            if self.tolerance < 0:
                raise ValueError(f"{self.id}: tolerance cannot be negative")

        elif self.type == "short_text" and not any(self.accept.values()):
            # There is no LLM at request time, so grading is exact matching
            # against a list the author wrote. Without one, nothing can be right.
            raise ValueError(f"{self.id}: short_text items need accepted answers")

        return self

    def response_text(self, response: str, locale: str = BOKMAAL) -> str:
        """A response as the pupil would recognise it.

        Multiple-choice answers are stored as choice ids, so echoing the raw
        value back would show "b" to a child who picked "En bok".
        """
        answer = response.strip()
        if self.type != "multiple_choice":
            return answer
        choice = next((c for c in self.choices if c.id == answer), None)
        return choice.text.get(locale) if choice else answer

    def correct_text(self, locale: str = BOKMAAL) -> str:
        """The right answer, phrased for display."""
        if self.type == "multiple_choice":
            choice = next((c for c in self.choices if c.correct), None)
            return choice.text.get(locale) if choice else ""
        if self.type == "numeric":
            # Render 7.0 as "7" -- a child asked for a whole number should not
            # be shown a decimal point they did not use.
            value = float(self.answer)
            return str(int(value)) if value.is_integer() else str(value).replace(".", ",")
        return next((c[0] for c in self.accept.values() if c), "")

    def is_correct(self, response: str) -> bool:
        """Grade a response. Deterministic and offline by construction."""
        answer = response.strip()
        if not answer:
            return False

        if self.type == "multiple_choice":
            return any(c.correct and c.id == answer for c in self.choices)

        if self.type == "numeric":
            try:
                # Norwegian pupils write decimals with a comma.
                value = float(answer.replace(",", "."))
            except ValueError:
                return False
            return abs(value - float(self.answer)) <= self.tolerance

        return any(
            _normalise(answer) == _normalise(candidate)
            for candidates in self.accept.values()
            for candidate in candidates
        )


class NotAssessable(BaseModel):
    """A goal deliberately left unquizzed, and why.

    Recorded rather than omitted so the gap is visible: an absent goal looks
    like an oversight, whereas this is a judgement someone made and signed.
    """

    model_config = ConfigDict(frozen=True)

    goal: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class ItemSet(BaseModel):
    """Every item authored for one goal set. One file per checkpoint."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    goal_set: str = Field(min_length=1)
    items: tuple[QuizItem, ...] = ()
    not_assessable: tuple[NotAssessable, ...] = ()

    @model_validator(mode="after")
    def _check_no_duplicate_ids(self) -> ItemSet:
        ids = [item.id for item in self.items]
        if len(set(ids)) != len(ids):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"{self.goal_set}: duplicate item ids {duplicates}")
        return self

    @property
    def goals_covered(self) -> set[str]:
        return {item.goal for item in self.items}

    @property
    def goals_excused(self) -> set[str]:
        return {entry.goal for entry in self.not_assessable}


def _normalise(text: str) -> str:
    """Casefold and collapse whitespace, so trivial variation is not punished."""
    return " ".join(text.strip().casefold().split()).rstrip(".!?")
