"""Turning what was heard into a reading measurement.

The metric is **correct words per minute**: words the recogniser heard in the
order the page printed them, divided by the time spent reading. Plain words per
minute rewards a child who reads fast by skipping, which is the opposite of what
this is for.

Two things are computed here beyond that number, both for the screen rather than
the score:

* `ReadWord.at` -- when each word was heard, so the passage can be replayed
  afterwards with the words lighting up at the times they were actually read;
* `advance`, which moves a cursor forward through the passage while the reading
  is still going, so words can light up live.

The live cursor is approximate by construction and never touches the result. The
score always comes from one pass over the whole recording at the end.

Everything here is deterministic and offline: the same input always gives the
same numbers, and none of it involves a model.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from pensum.reading.schema import ReadingText, words

SECONDS_PER_MINUTE = 60.0

# Below these, a measurement is noise rather than a result: a two-second clip
# divides by almost nothing, and ten words is inside the margin of the aligner.
MIN_SECONDS = 5.0
MIN_WORDS_READ = 10

# How far ahead of the live cursor to look when matching a fresh chunk of audio.
# Wide enough to survive a skipped line, narrow enough that a word repeated
# later in the passage cannot yank the cursor forwards.
LOOKAHEAD = 40

# Where a reading sits relative to the band for its checkpoint. Deliberately not
# an enum of grades: these are positions, and none of them is a pass or a fail.
Verdict = str

BELOW = "below"
WITHIN = "within"
ABOVE = "above"
UNMEASURED = "unmeasured"


@dataclass(frozen=True)
class HeardWord:
    """One word the recogniser reported, already normalised for comparison.

    `at` is seconds from the start of the reading, and is None when the
    recogniser gave no timings. Everything that depends on it degrades to an
    even pace rather than to nothing.
    """

    text: str
    at: float | None = None


@dataclass(frozen=True)
class ReadWord:
    """One word of the printed passage, and how it was read."""

    text: str
    correct: bool
    # False for every word after the point the pupil stopped. Distinguished from
    # `correct=False` because not reaching a word is not the same as misreading
    # it, and the result page must not mark an unread tail as mistakes.
    reached: bool
    # When it was heard, for the replay. None for a word that was not heard, and
    # for every word when the recogniser reported no timings.
    at: float | None = None


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

    @property
    def timed(self) -> bool:
        """Whether the replay can use real timings rather than an even pace."""
        return any(w.at is not None for w in self.words)


def heard_from_text(transcript: str) -> tuple[HeardWord, ...]:
    """Spoken words with no timings. What a transcript alone can give."""
    return tuple(HeardWord(text=word) for word in words(transcript))


def _matched_pairs(reference: list[str], spoken: Sequence[HeardWord]) -> dict[int, int]:
    """Map each matched reference index to the spoken index that matched it.

    Alignment is a plain longest-common-subsequence over case-folded words, so a
    repeated word, a self-correction or a stumble costs one word rather than
    derailing everything after it. That is deliberate: children re-read a word
    they trip on constantly, and a scorer that punishes it would report a fluent
    reader as a failing one.
    """
    heard = [word.text for word in spoken]
    pairs: dict[int, int] = {}
    for block in SequenceMatcher(None, reference, heard, autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            pairs[block.a + offset] = block.b + offset
    return pairs


def measure(text: ReadingText, spoken: Sequence[HeardWord] | str, seconds: float) -> Fluency:
    """Align what was heard against `text` and score the reading.

    A bare string is accepted for the case where no timings exist; the replay
    then falls back to an even pace.
    """
    if isinstance(spoken, str):
        spoken = heard_from_text(spoken)

    reference = text.word_list
    total = len(reference)
    pairs = _matched_pairs(reference, spoken)

    # Where they stopped: one past the last word actually heard. Everything
    # after it is unread, not wrong -- a child who runs out of time on a long
    # passage has not misread its final paragraph.
    attempted = max(pairs) + 1 if pairs else 0

    read_words = tuple(
        ReadWord(
            text=word,
            correct=index in pairs,
            reached=index < attempted,
            at=spoken[pairs[index]].at if index in pairs else None,
        )
        for index, word in enumerate(reference)
    )
    correct = len(pairs)

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


def advance(text: ReadingText, cursor: int, spoken: Sequence[HeardWord] | str) -> int:
    """Move the live cursor forward through the passage. Never backwards.

    Called while the pupil is still reading, against a short window of recent
    audio, so it sees a handful of words with no idea where in the passage they
    belong. Matching only the next `LOOKAHEAD` words is what stops a common word
    -- "og", "the" -- from teleporting the highlight to the end of the page.

    Forward-only for the same reason a progress bar is: a highlight that jumps
    backwards mid-sentence is worse than one that lags.
    """
    if isinstance(spoken, str):
        spoken = heard_from_text(spoken)

    window = text.word_list[cursor : cursor + LOOKAHEAD]
    if not window or not spoken:
        return cursor

    pairs = _matched_pairs(window, spoken)
    return cursor + max(pairs) + 1 if pairs else cursor


def replay(fluency: Fluency) -> tuple[dict[str, object], ...]:
    """Per-word instructions for the replay, in passage order.

    `at` is filled in for every reached word, so the animation never stalls on a
    word the recogniser skipped: an unheard word inherits the time of the last
    word that was heard. When there are no timings at all -- a timed reading, or
    a recogniser that reported none -- the whole passage is paced evenly and the
    page says so rather than implying it is playing back what happened.
    """
    reached = [(index, word) for index, word in enumerate(fluency.words) if word.reached]
    if not reached:
        return ()

    if fluency.timed:
        last = 0.0
        times: list[float] = []
        for _, word in reached:
            last = word.at if word.at is not None else last
            times.append(last)
    else:
        step = fluency.seconds / len(reached)
        times = [position * step for position in range(len(reached))]

    return tuple(
        {"i": index, "at": round(times[position], 3), "ok": word.correct}
        for position, (index, word) in enumerate(reached)
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


def even_replay(text: ReadingText, seconds: float) -> tuple[dict[str, object], ...]:
    """A replay for a reading nobody listened to: every word, evenly paced.

    Honest only because the page that shows it says outright that this is the
    clock spread across the passage, not a recording of how it was read.
    """
    total = text.word_count
    if not total or seconds <= 0:
        return ()
    step = seconds / total
    return tuple({"i": index, "at": round(index * step, 3), "ok": True} for index in range(total))
