"""Hear a word, then either pick it or write it.

Two modes of one exercise, and the difference is only what the pupil does with
what they heard:

* **Pick** -- the word and one plausible mis-hearing, side by side. What 1. og
  2. trinn can do, because recognising a spelling comes a long way before
  producing one.
* **Write** -- a box, and no letters to copy from. Dictation, from 3. trinn.

Which one a checkpoint gets is decided from the checkpoint, not from the pupil:
a nine-year-old still working towards 2. trinn gets the same exercise as
everybody else working towards it. That is the same rule the rest of Pensum
follows, and the same reason -- the goal set is the thing being practised.

The words come from the passages authored for that checkpoint, so they are
already at its level and already reviewed. Nothing here is authored twice: a
listening exercise is a view of text that exists rather than a new pile of
content to keep in step with the curriculum.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from pensum.listening.confusable import distractor
from pensum.listening.lexicon import Lexicon
from pensum.reading.schema import ReadingText

Mode = Literal["pick", "write"]

# Where the exercise stops being recognition and becomes production. Reading
# goals are set after 2. trinn and again after 4., and the year between is where
# writing a heard word stops being unreasonable.
WRITE_FROM_YEAR = 3

# How many words one round asks. Short enough to finish before a six-year-old is
# done with it, long enough that guessing twice is not a strategy.
ROUND_LENGTH = 8


@dataclass(frozen=True)
class Question:
    """One word, spoken."""

    word: str
    language: str
    # Both spellings in the order they are shown, for `pick`; empty for `write`,
    # where showing anything to choose between would be showing the answer.
    options: tuple[str, ...] = ()

    @property
    def answer_index(self) -> int | None:
        return self.options.index(self.word) if self.options else None


@dataclass(frozen=True)
class Round:
    """The questions one sitting asks."""

    mode: Mode
    language: str
    questions: tuple[Question, ...]


def mode_for(after_year: int) -> Mode:
    return "write" if after_year >= WRITE_FROM_YEAR else "pick"


def _seed(word: str, salt: str) -> int:
    """A stable number from a word.

    Used to decide which side a distractor goes on. `random` would put the
    answer in a different place on every reload, which sounds harmless until a
    test cannot assert anything and a pupil reloads until the answer moves.
    Hashing the word is stable, unguessable by a child, and reproducible in a
    failing test.
    """
    return int.from_bytes(hashlib.sha256(f"{salt}:{word}".encode()).digest()[:4], "big")


def question_for(word: str, language: str, mode: Mode, lexicon: Lexicon) -> Question | None:
    """One question, or None when the word cannot carry one.

    A word with nothing to confuse it with is dropped rather than paired with
    something arbitrary: "which of these two completely different words did you
    hear" is a hearing test, and this is a spelling exercise.
    """
    if mode == "write":
        return Question(word=word, language=language)

    other = distractor(word, language, lexicon)
    if other is None:
        return None

    left_first = _seed(word, "side") % 2 == 0
    options = (word, other) if left_first else (other, word)
    return Question(word=word, language=language, options=options)


def build_round(
    texts: list[ReadingText],
    *,
    after_year: int,
    language: str,
    lexicon: Lexicon,
    length: int = ROUND_LENGTH,
) -> Round:
    """The words this checkpoint asks about, in a stable order.

    Drawn from every passage at the checkpoint and then ordered by a hash rather
    than by the alphabet: sorting alphabetically would ask about `and`, `at` and
    `av` every single time, which is both dull and a much easier exercise than
    the passages contain.
    """
    mode = mode_for(after_year)
    pool: set[str] = set()
    for text in texts:
        pool |= set(text.word_list)

    candidates = lexicon.askable(pool)
    candidates.sort(key=lambda word: _seed(word, "order"))

    questions = []
    for word in candidates:
        question = question_for(word, language, mode, lexicon)
        if question is not None:
            questions.append(question)
        if len(questions) == length:
            break

    return Round(mode=mode, language=language, questions=tuple(questions))


def normalise(answer: str) -> str:
    """What a typed answer is compared as.

    Case and surrounding space are forgiven, because neither is what is being
    practised and a tablet keyboard capitalises on its own. Everything else is
    not: `å` is not `a`, and a dictation that accepted it would be teaching the
    opposite of the lesson.
    """
    return answer.strip().casefold()


def is_correct(question: Question, answer: str) -> bool:
    return normalise(answer) == normalise(question.word)
