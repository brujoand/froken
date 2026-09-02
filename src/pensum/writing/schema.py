"""Letterforms, and what a pupil is asked to write.

Two different kinds of thing live here and only one of them is curriculum-shaped.

The *alphabet* is how a letter is drawn: a list of strokes, in order, each with
a direction. It belongs to no subject and no checkpoint, because an `a` is the
same `a` in norsk and in engelsk.

A *prompt* is the exercise: these characters, for this competence goal, at this
difficulty. It carries the same review gate as a quiz item and a reading
passage, so an unreviewed set is withheld until a human has looked at it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from pensum.writing.paths import PathError, flatten

# Same three-band scale quiz items and reading passages use, and the same
# meaning: relative to the checkpoint, not to a pupil.
MAX_DIFFICULTY = 3

# A prompt is one sitting. Longer than this and a six-year-old is being asked to
# trace a paragraph with a fingertip.
MAX_CHARACTERS = 12

# A glyph nobody could write in one sitting is an authoring mistake.
MAX_STROKES = 8

PromptKind = Literal["letters", "digits", "word"]

# The language the prompt is in, not the UI locale -- exactly as a reading
# passage: a norsk letter set stays norsk on the English site.
PromptLanguage = Literal["nb", "nn", "en"]


class Metrics(BaseModel):
    """The grid the letters are drawn on, and its four writing lines.

    In the data rather than only in code because the page rules the paper from
    these numbers: a baseline the letters do not sit on is worse than no line.
    """

    model_config = ConfigDict(frozen=True)

    width: float = Field(gt=0)
    height: float = Field(gt=0)
    ascender: float = Field(ge=0)
    x_height: float = Field(ge=0)
    baseline: float = Field(gt=0)
    descender: float = Field(gt=0)

    @model_validator(mode="after")
    def _check_ordering(self) -> Metrics:
        lines = [self.ascender, self.x_height, self.baseline, self.descender]
        if lines != sorted(lines):
            raise ValueError("writing lines must run top to bottom")
        if self.descender > self.height:
            raise ValueError("the descender line falls outside the box")
        return self


class Glyph(BaseModel):
    """One character, as the strokes that make it.

    The list is ordered and each path is directed, and both are content rather
    than presentation: half of what a first-grader is learning is that an `a`
    starts at the top and goes round to the left.
    """

    model_config = ConfigDict(frozen=True)

    char: str = Field(min_length=1, max_length=1)
    strokes: tuple[str, ...] = Field(min_length=1, max_length=MAX_STROKES)

    @model_validator(mode="after")
    def _check_drawable(self) -> Glyph:
        for stroke in self.strokes:
            try:
                flatten(stroke)
            except PathError as exc:
                raise ValueError(f"{self.char}: {exc}") from exc
        return self


class Alphabet(BaseModel):
    """Every letterform Pensum knows how to ask for."""

    model_config = ConfigDict(frozen=True)

    metrics: Metrics
    glyphs: tuple[Glyph, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique(self) -> Alphabet:
        seen = [glyph.char for glyph in self.glyphs]
        duplicates = {char for char in seen if seen.count(char) > 1}
        if duplicates:
            raise ValueError(f"the alphabet defines {sorted(duplicates)} more than once")
        return self

    @model_validator(mode="after")
    def _check_inside_the_box(self) -> Alphabet:
        """A stroke that leaves the em box would be clipped on the page, and the
        scorer would then mark a pupil down for not tracing something invisible."""
        for glyph in self.glyphs:
            for stroke in glyph.strokes:
                for x, y in flatten(stroke):
                    if not (0 <= x <= self.metrics.width and 0 <= y <= self.metrics.height):
                        raise ValueError(f"{glyph.char}: a stroke leaves the box at ({x}, {y})")
        return self

    def glyph(self, char: str) -> Glyph | None:
        return next((glyph for glyph in self.glyphs if glyph.char == char), None)

    def covers(self, text: str) -> bool:
        return all(self.glyph(char) is not None for char in text)


class WritingPrompt(BaseModel):
    """One set of characters to trace."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    # The competence goal this exercises. Same contract as a quiz item and a
    # reading passage: a curriculum revision that renumbers goals must orphan
    # this loudly rather than quietly mislabel it.
    goal: str = Field(min_length=1)
    language: PromptLanguage
    title: str = Field(min_length=1)
    kind: PromptKind
    text: str = Field(min_length=1, max_length=MAX_CHARACTERS)
    difficulty: int = Field(ge=1, le=MAX_DIFFICULTY)
    # "pensum" means we wrote it. Letter sets are ours by construction, but the
    # field stays so a word list taken from somewhere nameable has to name it.
    source: str = Field(min_length=1)
    reviewed: bool = False
    reviewed_by: str | None = None

    @property
    def characters(self) -> tuple[str, ...]:
        return tuple(self.text)

    @model_validator(mode="after")
    def _check_traceable(self) -> WritingPrompt:
        if any(char.isspace() for char in self.text):
            # One prompt is one word or one run of letters. Spacing between
            # words is a different lesson and would need its own scoring.
            raise ValueError(f"{self.id}: a prompt is written without spaces")
        if self.kind == "digits" and not self.text.isdigit():
            raise ValueError(f"{self.id}: a digits prompt holds only digits")
        if self.kind == "word" and not self.text.isalpha():
            raise ValueError(f"{self.id}: a word prompt holds only letters")
        return self


class WritingSet(BaseModel):
    """Every prompt authored for one checkpoint. One file on disk."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    goal_set: str = Field(min_length=1)
    prompts: tuple[WritingPrompt, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_unique_ids(self) -> WritingSet:
        seen = [prompt.id for prompt in self.prompts]
        duplicates = {value for value in seen if seen.count(value) > 1}
        if duplicates:
            raise ValueError(f"{self.goal_set}: duplicate prompt ids {sorted(duplicates)}")
        return self
