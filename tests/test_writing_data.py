"""Assertions about the committed letterforms and prompts.

These run against `data/writing/`, so they fail when a hand-edit or a curriculum
revision breaks the authored content -- not merely when the code changes.

The letterforms get more attention than the prompts, because they are the part
nobody can review by reading the file: a path string is not a letter until
something draws it. `tools/render_alphabet.py` is how a human looks at them;
these are the checks a human should not have to do by eye.
"""

from __future__ import annotations

import math

import pytest

from pensum.catalogue.loader import Catalogue
from pensum.web.writing_routes import LABEL_CLEARANCE, label_positions
from pensum.writing.library import WritingLibrary, load_alphabet
from pensum.writing.paths import flatten, length, sample
from pensum.writing.schema import Alphabet
from pensum.writing.validate import validate

# The Norwegian alphabet, which is the one this is for. A missing letter is not
# a smaller feature -- it is a child who cannot practise their own name.
NORWEGIAN = "abcdefghijklmnopqrstuvwxyzæøå"
DIGITS = "0123456789"

# Shorter than this and a "stroke" is a dot rather than a movement. The dot on
# an `i` is the exception and is deliberately just above it.
MIN_STROKE_LENGTH = 4.0


@pytest.fixture(scope="module")
def alphabet() -> Alphabet:
    return load_alphabet()


@pytest.fixture(scope="module")
def library() -> WritingLibrary:
    return WritingLibrary.load()


def test_committed_writing_data_validates_against_the_catalogue() -> None:
    """The same gate the pre-commit hook applies: orphaned goals, and prompts
    asking for characters the alphabet cannot draw. Both are silent at runtime."""
    assert validate() == []


def test_every_norwegian_letter_can_be_drawn(alphabet: Alphabet) -> None:
    missing = [char for char in NORWEGIAN if alphabet.glyph(char) is None]
    assert missing == []


def test_every_capital_can_be_drawn(alphabet: Alphabet) -> None:
    missing = [char for char in NORWEGIAN.upper() if alphabet.glyph(char) is None]
    assert missing == []


def test_every_digit_can_be_drawn(alphabet: Alphabet) -> None:
    missing = [char for char in DIGITS if alphabet.glyph(char) is None]
    assert missing == []


def test_every_stroke_is_a_movement_rather_than_a_dot(alphabet: Alphabet) -> None:
    """A zero-length stroke would be unscoreable: there is no direction to get
    right and no length to cover."""
    short = [
        (glyph.char, index)
        for glyph in alphabet.glyphs
        for index, stroke in enumerate(glyph.strokes)
        if length(flatten(stroke)) < MIN_STROKE_LENGTH
    ]
    assert short == []


def test_every_letter_sits_on_the_baseline(alphabet: Alphabet) -> None:
    """A letter floating above the line, or sunk below it, is drawn correctly and
    written wrongly -- and the page rules the paper from these same numbers."""
    metrics = alphabet.metrics
    floating = []
    for glyph in alphabet.glyphs:
        lowest = max(y for stroke in glyph.strokes for _, y in flatten(stroke))
        # Every glyph here either sits on the baseline or descends through it.
        # Nothing should stop short of it by more than a hair.
        if lowest < metrics.baseline - 2:
            floating.append((glyph.char, lowest))
    assert floating == []


def test_lower_case_letters_without_ascenders_stay_below_the_x_height(
    alphabet: Alphabet,
) -> None:
    """The x-height line is drawn on the page. An `o` poking through it would
    make the ruled paper look wrong rather than the letter."""
    metrics = alphabet.metrics
    for char in "acemnorsuvwxz":
        glyph = alphabet.glyph(char)
        highest = min(y for stroke in glyph.strokes for _, y in flatten(stroke))
        assert highest >= metrics.x_height - 2, f"{char} rises above the x-height"


def test_capitals_reach_the_cap_line_without_leaving_the_box(alphabet: Alphabet) -> None:
    metrics = alphabet.metrics
    for char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        glyph = alphabet.glyph(char)
        ys = [y for stroke in glyph.strokes for _, y in flatten(stroke)]
        assert min(ys) <= metrics.ascender + 6, f"{char} does not reach the cap line"
        assert max(ys) <= metrics.height


def test_no_two_stroke_numbers_are_printed_on_top_of_each_other(alphabet: Alphabet) -> None:
    """Strokes may legitimately begin in the same place -- a `B` starts its bowl
    where its stem starts -- so the page moves the *numbers* apart rather than
    the dots. Two numbers in one spot are the one thing on this page a child has
    to read, and unreadable."""
    for glyph in alphabet.glyphs:
        starts = [sample(stroke)[0] for stroke in glyph.strokes]
        labels = label_positions(starts, alphabet.metrics)
        for first in range(len(labels)):
            for second in range(first + 1, len(labels)):
                gap = math.dist(labels[first], labels[second])
                assert gap >= LABEL_CLEARANCE, (
                    f"{glyph.char}: numbers {first + 1} and {second + 1} overlap"
                )


def test_a_stroke_number_is_never_pushed_outside_the_letter_box(
    alphabet: Alphabet,
) -> None:
    metrics = alphabet.metrics
    for glyph in alphabet.glyphs:
        starts = [sample(stroke)[0] for stroke in glyph.strokes]
        for x, y in label_positions(starts, metrics):
            assert 0 <= x <= metrics.width
            assert 0 <= y <= metrics.height


def test_every_prompt_names_a_goal_that_exists(library: WritingLibrary) -> None:
    """A curriculum revision renumbers goal codes. This is what makes an orphaned
    prompt a red build rather than a silently mislabelled exercise."""
    catalogue = Catalogue.load()
    known = {
        goal.code
        for subject in catalogue.subjects
        for goal_set in subject.goal_sets
        for goal in goal_set.goals
    }
    assert library.goal_codes <= known, sorted(library.goal_codes - known)


def test_the_committed_prompts_exercise_every_character(library: WritingLibrary) -> None:
    """A letter with a glyph but no prompt cannot be practised, which is the same
    outcome as not having drawn it at all."""
    asked = "".join(
        prompt.text for writing_set in library.writing_sets for prompt in writing_set.prompts
    )
    missing = [char for char in NORWEGIAN + NORWEGIAN.upper() + DIGITS if char not in asked]
    assert missing == []


def test_every_committed_prompt_has_been_reviewed(library: WritingLibrary) -> None:
    """A released build serves only reviewed content. This is the check that a
    merge alone never puts an unread exercise in front of a child."""
    unreviewed = [
        prompt.id
        for writing_set in library.writing_sets
        for prompt in writing_set.prompts
        if not prompt.reviewed
    ]
    assert unreviewed == []


def test_every_committed_prompt_is_servable(library: WritingLibrary) -> None:
    """`for_goal_set` drops a prompt the alphabet cannot draw. Silently, and
    correctly -- so the loudness has to live here."""
    for writing_set in library.writing_sets:
        served = {prompt.id for prompt in library.for_goal_set(writing_set.goal_set)}
        authored = {prompt.id for prompt in writing_set.prompts}
        assert served == authored, sorted(authored - served)
