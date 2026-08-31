"""Turning a transcript and a duration into a reading measurement.

The metric is **correct words per minute**: words the recogniser heard in the
order the page printed them, divided by the time spent reading. Plain words per
minute rewards a child who reads fast by skipping, which is the opposite of what
this is for.

Everything here is deterministic and offline -- the same transcript and duration
always give the same numbers, and none of it involves a model.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher

from pensum.reading.schema import ReadingText, words

SECONDS_PER_MINUTE = 60.0

# Below these, a measurement is noise rather than a result: a two-second clip
# divides by almost nothing, and ten words is inside the margin of the aligner.
MIN_SECONDS = 5.0
MIN_WORDS_READ = 10

# Where a reading sits relative to the band for its checkpoint. Deliberately not
# an enum of grades: these are positions, and none of them is a pass or a fail.
Verdict = str

BELOW = "below"
WITHIN = "within"
ABOVE = "above"
UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class ReadWord:
    """One word of the printed passage, and how it was read."""

    text: str
    correct: bool
    # False for every word after the point the pupil stopped. Distinguished from
    # `correct=False` because not reaching a word is not the same as misreading
    # it, and the result page must not mark an unread tail as mistakes.
    reached: bool


@dataclass(frozen=True)
class Fluency:
    """One reading, measured."""

    seconds: float
    # Words of the passage the pupil got to, right or wrong.
    attempted: int
    # Of those, the ones heard correctly and in order.
    correct: int
    total: int
    words: tuple[ReadWord, ...]
    # None when the reading was too short to say anything about, which is a
    # state the result page renders rather than a zero it prints.
    wcpm: int | None
    accuracy: float | None

    @property
    def measurable(self) -> bool:
        return self.wcpm is not None

    @property
    def finished(self) -> bool:
        """Whether the pupil reached the end of the passage."""
        return self.attempted >= self.total

    @property
    def misread(self) -> tuple[str, ...]:
        """The words that were reached but not heard correctly."""
        return tuple(w.text for w in self.words if w.reached and not w.correct)


def measure(text: ReadingText, transcript: str, seconds: float) -> Fluency:
    """Align `transcript` against `text` and score the reading.

    Alignment is a plain longest-common-subsequence over case-folded words, so a
    repeated word, a self-correction or a stumble costs one word rather than
    derailing everything after it. That is deliberate: children re-read a word
    they trip on constantly, and a scorer that punishes it would report a fluent
    reader as a failing one.
    """
    reference = text.word_list
    spoken = words(transcript)
    total = len(reference)

    matched: set[int] = set()
    for block in SequenceMatcher(None, reference, spoken, autojunk=False).get_matching_blocks():
        matched.update(range(block.a, block.a + block.size))

    # Where they stopped: one past the last word actually heard. Everything
    # after it is unread, not wrong -- a child who runs out of time on a long
    # passage has not misread its final paragraph.
    attempted = max(matched) + 1 if matched else 0

    read_words = tuple(
        ReadWord(text=word, correct=index in matched, reached=index < attempted)
        for index, word in enumerate(reference)
    )
    correct = len(matched)

    if seconds < MIN_SECONDS or correct < MIN_WORDS_READ:
        return Fluency(
            seconds=seconds,
            attempted=attempted,
            correct=correct,
            total=total,
            words=read_words,
            wcpm=None,
            accuracy=None,
        )

    return Fluency(
        seconds=seconds,
        attempted=attempted,
        correct=correct,
        total=total,
        words=read_words,
        wcpm=round(correct * SECONDS_PER_MINUTE / seconds),
        # Against what they reached, not against the whole passage. Otherwise
        # stopping halfway through a text read flawlessly reports 50% accuracy.
        accuracy=correct / attempted if attempted else 0.0,
    )


def verdict(fluency: Fluency, low: int, high: int) -> Verdict:
    """Where a reading sits against a band. Three outcomes, none of them a mark.

    `above` is not praise and `below` is not failure; the wording the pupil sees
    says as much. This function only says which side of the band the number fell
    on.
    """
    if fluency.wcpm is None:
        return UNMEASURED
    if fluency.wcpm < low:
        return BELOW
    if fluency.wcpm > high:
        return ABOVE
    return WITHIN


@dataclass(frozen=True)
class TimedReading:
    """A reading that was timed but not listened to.

    What the default image produces: no speech model, so no transcript, so no
    accuracy and no misread words -- only a pace, and only on the pupil's own
    word that they read the whole passage. Kept as a separate type so a template
    cannot accidentally render it as a checked result.
    """

    seconds: float
    words: int
    wpm: int | None


def time_only(text: ReadingText, seconds: float) -> TimedReading:
    """Words per minute from the clock alone, assuming the passage was read."""
    total = text.word_count
    measurable = seconds >= MIN_SECONDS and total >= MIN_WORDS_READ
    return TimedReading(
        seconds=seconds,
        words=total,
        wpm=round(total * SECONDS_PER_MINUTE / seconds) if measurable else None,
    )
