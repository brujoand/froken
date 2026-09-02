"""Marking a finished round.

Nothing clever. A word was heard and a word was given; they match or they do
not, and the page says which. What matters here is the shape of what comes back,
because that is what the pupil reads: every question, the word that was said,
and what they wrote -- including the ones they got right, which is the half a
mark sheet usually throws away.

Nothing is stored. The answers arrive, are compared in memory, and are gone with
the response, exactly as with reading and writing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from pydantic import BaseModel, Field

from pensum.listening.exercise import Question, Round, is_correct

# One answer cannot be longer than this. A dictation word is nine letters at
# most, so anything past a short line is somebody testing what the box accepts
# rather than a child spelling.
MAX_ANSWER = 64
MAX_ANSWERS = 32

THREE_STARS = 1.0
TWO_STARS = 0.75
ONE_STAR = 0.5

MAX_STARS = 3


class Answers(BaseModel):
    """What the page posts back.

    Positional: `given[i]` answers `round.questions[i]`. The words themselves are
    not posted, because the round is rebuilt from the checkpoint on this side and
    a client that could name its own questions could name easy ones.
    """

    # Bounded per answer as well as in count. Unbounded strings here would let a
    # request of any size through the schema and into memory, and no child has
    # ever spelled a nine-letter word in more than a short line.
    given: tuple[Annotated[str, Field(max_length=MAX_ANSWER)], ...] = Field(
        default=(), max_length=MAX_ANSWERS
    )

    def at(self, index: int) -> str:
        return self.given[index] if index < len(self.given) else ""


@dataclass(frozen=True)
class Marked:
    """One question and what happened to it."""

    question: Question
    given: str
    correct: bool

    @property
    def answered(self) -> bool:
        return bool(self.given.strip())


@dataclass(frozen=True)
class Result:
    """A whole round, marked."""

    marks: tuple[Marked, ...]

    @property
    def total(self) -> int:
        return len(self.marks)

    @property
    def right(self) -> int:
        return sum(1 for mark in self.marks if mark.correct)

    @property
    def score(self) -> float:
        return self.right / self.total if self.total else 0.0

    @property
    def finished(self) -> bool:
        """Every question attempted, whether or not it was got right.

        The reward that cannot mislead, as on the other two exercise screens: a
        child who spelled all eight and missed three still finished all eight.
        """
        return all(mark.answered for mark in self.marks)

    @property
    def stars(self) -> int:
        if self.score >= THREE_STARS:
            return 3
        if self.score >= TWO_STARS:
            return 2
        if self.score >= ONE_STAR:
            return 1
        return 0


def mark(round_: Round, answers: Answers) -> Result:
    return Result(
        marks=tuple(
            Marked(
                question=question,
                given=answers.at(index).strip(),
                correct=is_correct(question, answers.at(index)),
            )
            for index, question in enumerate(round_.questions)
        )
    )
