"""What the app holds for the listening exercise.

Unlike items, reading and writing, this library loads no files of its own. Every
listening exercise is derived from passages that already exist, so what is kept
here is the derivation: the per-language lexicon, and the rounds built from it.

Rounds are cached because they are pure. The same checkpoint, the same review
setting and the same data always produce the same eight words, so building them
once per process costs a few milliseconds and building them per request costs
that on every subject page -- which asks whether a listening exercise exists at
all, and can only find out by trying.
"""

from __future__ import annotations

from pensum.items.loader import ItemBank
from pensum.listening.exercise import Round, build_round
from pensum.listening.lexicon import Lexicon, build
from pensum.reading.library import ReadingLibrary

# Fewer than this and the exercise is not worth opening: four words is already a
# short sitting, and one or two reads as a bug rather than as a lesson.
MIN_ROUND = 4


class ListeningLibrary:
    """The lexicons, and the rounds derived from the reading passages."""

    def __init__(self, reading: ReadingLibrary, lexicons: dict[str, Lexicon]) -> None:
        self._reading = reading
        self._lexicons = lexicons
        self._rounds: dict[tuple[str, int, bool], Round | None] = {}

    @classmethod
    def of(cls, items: ItemBank, reading: ReadingLibrary) -> ListeningLibrary:
        return cls(reading, build(items, reading))

    @property
    def languages(self) -> dict[str, Lexicon]:
        return dict(self._lexicons)

    def lexicon(self, language: str) -> Lexicon:
        return self._lexicons.get(language, self._lexicons["nb"])

    def round_for(
        self, goal_set: str, after_year: int, *, unreviewed: bool = False
    ) -> Round | None:
        """The round for a checkpoint, or None when there is not enough to ask.

        `after_year` decides the mode, so it is part of the key: the same
        passages give a choosing exercise to one checkpoint and a dictation to
        the next, and both may be wanted in one process.
        """
        key = (goal_set, after_year, unreviewed)
        if key not in self._rounds:
            self._rounds[key] = self._build(goal_set, after_year, unreviewed)
        return self._rounds[key]

    def _build(self, goal_set: str, after_year: int, unreviewed: bool) -> Round | None:
        texts = self._reading.for_goal_set(goal_set, unreviewed=unreviewed)
        if not texts:
            return None

        # A goal set's passages are all in one language -- it is the language of
        # the subject -- but taking the first rather than assuming it means a
        # mixed set degrades to "the exercise is in the first passage's
        # language" instead of speaking Norwegian words with an English voice.
        language = texts[0].language
        built = build_round(
            texts,
            after_year=after_year,
            language=language,
            lexicon=self.lexicon(language),
        )
        return built if len(built.questions) >= MIN_ROUND else None

    def has_listening(self, goal_set: str, after_year: int, *, unreviewed: bool = False) -> bool:
        return self.round_for(goal_set, after_year, unreviewed=unreviewed) is not None
