"""Reading texts and speed expectations.

Both are ours, and neither is Udir's. LK20 says a pupil should read "med flyt";
it names no words-per-minute figure anywhere, so any number Pensum shows is an
authored guideline that has to carry its source with it. That is why
`ReadingNorm` requires a source and a caveat -- a band with no provenance would
read as official, and there is nothing official to read.

The texts are written for Pensum rather than quoted. A reading exercise wants a
passage of a known length at a known level, and authoring one avoids reproducing
someone else's work in a public image.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# The language a passage is written in, which is not the UI locale: a norsk
# passage stays Norwegian on the English site.
TextLanguage = Literal["nb", "nn", "en"]

MAX_DIFFICULTY = 3
# Shorter than this and a reading is over before the timing means anything.
MIN_WORDS = 20

_WORD = re.compile(r"[^\W\d_]+(?:['’-][^\W\d_]+)*", re.UNICODE)


def words(text: str) -> list[str]:
    """The comparable words in a passage or a transcript.

    Case, punctuation and digits are dropped: a child who reads "17" as
    "sytten" read it correctly, and the recogniser will not agree with the page
    about which spelling to emit. Hyphens and apostrophes stay inside a word so
    "kunne-vaere" and "don't" count once.
    """
    return [match.group(0).casefold() for match in _WORD.finditer(text)]


class Token(BaseModel):
    """A run of the passage as it is printed.

    `index` is the word's position in `word_list` -- the number the scorer and
    the replay both use -- or None for the punctuation and spacing between
    words, which is printed but never scored.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    index: int | None = None


class ReadingText(BaseModel):
    """One passage a pupil reads aloud."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    # The competence goal this exercises. Same contract as a quiz item: a
    # curriculum revision that renumbers goals must orphan this loudly.
    goal: str = Field(min_length=1)
    language: TextLanguage
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    # Relative to the checkpoint, exactly as quiz item difficulty is.
    difficulty: int = Field(ge=1, le=MAX_DIFFICULTY)
    # Where the passage came from. "pensum" means we wrote it; anything else
    # must name a source that may lawfully be reproduced.
    source: str = Field(min_length=1)
    # The released build serves only reviewed passages, as with quiz items.
    reviewed: bool = False
    reviewed_by: str | None = None

    @property
    def word_list(self) -> list[str]:
        return words(self.body)

    @property
    def word_count(self) -> int:
        return len(self.word_list)

    @property
    def lines(self) -> list[str]:
        """The passage split for display, one paragraph per entry."""
        return [line.strip() for line in self.body.strip().split("\n") if line.strip()]

    @property
    def paragraphs(self) -> list[list[Token]]:
        """The passage as tokens, numbered so a word can be lit up individually.

        The word numbering must agree exactly with `word_list`, because that is
        what the scorer aligns against and what the replay indexes into. Doing
        the split once, here, is what keeps the page and the score talking about
        the same word 43.
        """
        numbered: list[list[Token]] = []
        index = 0
        for line in self.lines:
            tokens: list[Token] = []
            at = 0
            for match in _WORD.finditer(line):
                if match.start() > at:
                    tokens.append(Token(text=line[at : match.start()], index=None))
                tokens.append(Token(text=match.group(0), index=index))
                index += 1
                at = match.end()
            if at < len(line):
                tokens.append(Token(text=line[at:], index=None))
            numbered.append(tokens)
        return numbered

    @model_validator(mode="after")
    def _check_long_enough(self) -> ReadingText:
        if self.word_count < MIN_WORDS:
            raise ValueError(f"{self.id}: a reading text needs at least {MIN_WORDS} words")
        return self


class ReadingSet(BaseModel):
    """Every passage authored for one checkpoint. One file per goal set."""

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    goal_set: str = Field(min_length=1)
    texts: tuple[ReadingText, ...] = ()

    @model_validator(mode="after")
    def _check_no_duplicate_ids(self) -> ReadingSet:
        ids = [text.id for text in self.texts]
        if len(set(ids)) != len(ids):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(f"{self.goal_set}: duplicate reading text ids {duplicates}")
        return self


class ReadingNorm(BaseModel):
    """The words-per-minute band typical for one subject at one checkpoint.

    A band, never a threshold. Reading speed varies enormously between children
    who all read perfectly well, and the pupil-facing wording says so; the data
    shape enforces the rest by making a band with no `source` impossible to
    load.
    """

    model_config = ConfigDict(frozen=True)

    subject: str = Field(min_length=1)
    after_year: int = Field(ge=1, le=10)
    # Correct words per minute, not words per minute: reading fast by skipping
    # is not reading. See `fluency.measure`.
    low: int = Field(gt=0)
    high: int = Field(gt=0)
    # Free text naming where the band came from. Required, and checked by a test
    # against the sources declared in the same file.
    source: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_band_ordered(self) -> ReadingNorm:
        if self.low > self.high:
            raise ValueError(f"{self.subject} after {self.after_year}: low is above high")
        return self


class NormSource(BaseModel):
    """Where a band came from, and what it does not claim."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    # Stated in the UI next to the band. This is the field that keeps an
    # authored guideline from being read as a national standard.
    caveat: str = Field(min_length=1)
    url: str | None = None


class NormTable(BaseModel):
    """Every band, with its sources. One file."""

    model_config = ConfigDict(frozen=True)

    sources: tuple[NormSource, ...] = ()
    bands: tuple[ReadingNorm, ...] = ()

    @model_validator(mode="after")
    def _check_sources_resolve(self) -> NormTable:
        known = {source.id for source in self.sources}
        unknown = sorted({b.source for b in self.bands} - known)
        if unknown:
            raise ValueError(f"bands cite unknown sources: {unknown}")
        return self

    def band(self, subject: str, after_year: int) -> ReadingNorm | None:
        return next(
            (b for b in self.bands if b.subject == subject and b.after_year == after_year),
            None,
        )

    def source(self, source_id: str) -> NormSource | None:
        return next((s for s in self.sources if s.id == source_id), None)
