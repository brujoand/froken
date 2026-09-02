"""Handwriting: the path geometry, the scorer, the review gate, the routes.

The scoring tests matter most, and the ones that matter most among those are the
negative ones. Everything a pupil is told about their handwriting comes out of
`mark`, and the ways it can be wrong are all ways of telling a child something
untrue: that a scribble is a letter, that a letter drawn the wrong way round is
perfect, or that a letter they wrote carefully is not one.
"""

from __future__ import annotations

import math

import pytest
from fastapi.testclient import TestClient

from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.web.app import create_app
from pensum.writing.attempt import Attempt, InkStroke, TracedGlyph
from pensum.writing.library import WritingLibrary, load_alphabet
from pensum.writing.paths import (
    PathError,
    distance_to,
    flatten,
    length,
    nearest_index,
    resample,
    sample,
)
from pensum.writing.rewards import MAX_STARS, earned, stars_for
from pensum.writing.schema import Alphabet, Glyph, Metrics, WritingPrompt, WritingSet
from pensum.writing.tracing import (
    BACKWARDS_KEEPS,
    MAX_INK_POINTS,
    ORDER_COST,
    Mark,
    mark,
    mark_glyph,
    prepare_ink,
)

# A two-stroke letter with nothing subtle about it: a stem, then a bar. Every
# scoring assertion below is easier to read against a cross than against an `a`.
CROSS = Glyph(char="+", strokes=("M 50 20 L 50 120", "M 20 70 L 80 70"))

METRICS = Metrics(width=100, height=140, ascender=10, x_height=60, baseline=110, descender=135)


def alphabet(*glyphs: Glyph) -> Alphabet:
    return Alphabet(metrics=METRICS, glyphs=glyphs or (CROSS,))


def prompt(**overrides) -> WritingPrompt:
    fields = {
        "id": "p1",
        "goal": "KM14147",
        "language": "nb",
        "title": "Test",
        "kind": "letters",
        "text": "+",
        "difficulty": 1,
        "source": "pensum",
        "reviewed": True,
    }
    fields.update(overrides)
    return WritingPrompt(**fields)


def traced(glyph: Glyph, *, reverse=False, skip=(), order=None, jitter=0.0, spacing=3.0):
    """A synthetic tracing of `glyph`, as the page would post it."""
    strokes = list(enumerate(glyph.strokes))
    if order is not None:
        strokes = [strokes[index] for index in order]
    out = []
    for index, path in strokes:
        if index in skip:
            continue
        points = list(sample(path, spacing))
        if reverse:
            points.reverse()
        out.append(tuple((x + jitter, y - jitter) for x, y in points))
    return out


# --- paths ---------------------------------------------------------------


def test_a_stroke_is_sampled_evenly_along_its_length() -> None:
    points = sample("M 0 0 L 100 0", spacing=2.0)
    gaps = [math.dist(a, b) for a, b in zip(points, points[1:], strict=False)]
    assert max(gaps) == pytest.approx(2.0, abs=0.001)
    assert points[0] == (0.0, 0.0)
    assert points[-1] == (100.0, 0.0)


def test_a_curve_is_flattened_finely_enough_to_measure() -> None:
    """A quarter circle of radius 50 is 78.5 long; a coarse flattening would cut
    the corner and report a chord."""
    quarter = flatten("M 50 0 C 22.4 0 0 22.4 0 50")
    assert length(quarter) == pytest.approx(math.pi * 50 / 2, rel=0.001)


def test_sampling_keeps_the_direction_the_stroke_was_written_in() -> None:
    down = sample("M 50 20 L 50 120")
    up = sample("M 50 120 L 50 20")
    assert down[0] == up[-1]
    assert down[-1] == up[0]


@pytest.mark.parametrize(
    "path",
    [
        "L 10 10",  # does not start with a move
        "M 10 10 A 5 5 0 0 1 20 20",  # arcs are not supported
        "m 10 10 l 20 20",  # relative commands are refused, not guessed at
        "M 10 10 L 20",  # a coordinate short
        "M 10 10 M 20 20",  # two movements in one stroke
        "M 10 10",  # goes nowhere
    ],
)
def test_a_stroke_that_is_not_a_stroke_is_refused(path: str) -> None:
    with pytest.raises(PathError):
        flatten(path)


def test_a_glyph_carrying_an_unparseable_stroke_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported command"):
        Glyph(char="x", strokes=("M 0 0 A 1 1 0 0 1 2 2",))


def test_distance_measures_to_the_segment_not_to_the_sampled_points() -> None:
    """The midpoint of a long segment is on the line even where no sample sits."""
    assert distance_to((50.0, 0.0), ((0.0, 0.0), (100.0, 0.0))) == pytest.approx(0.0)
    assert distance_to((50.0, 7.0), ((0.0, 0.0), (100.0, 0.0))) == pytest.approx(7.0)


def test_nearest_index_says_where_along_a_stroke_a_point_sits() -> None:
    guide = sample("M 0 0 L 100 0", spacing=2.0)
    assert nearest_index((0.0, 1.0), guide) == 0
    assert nearest_index((100.0, 1.0), guide) == len(guide) - 1


def test_resampling_refuses_a_spacing_of_nothing() -> None:
    with pytest.raises(ValueError):
        resample([(0.0, 0.0), (1.0, 0.0)], spacing=0)


# --- marking one letter --------------------------------------------------


def test_tracing_a_letter_exactly_scores_full_marks() -> None:
    assert mark_glyph(CROSS, traced(CROSS)).score == pytest.approx(1.0)


def test_a_slightly_wobbly_tracing_still_scores_full_marks() -> None:
    """The tolerance is a fingertip. A child who is a few units off the line has
    written the letter, and a tool that says otherwise is measuring the screen."""
    assert mark_glyph(CROSS, traced(CROSS, jitter=5)).score == pytest.approx(1.0)


def test_a_scribble_over_the_letter_scores_almost_nothing() -> None:
    """The test this whole module exists for.

    A scrub back and forth across the letter covers every point of the guide and
    never leaves it, so coverage and neatness both say it is perfect. Only flow
    can tell that nothing was written.
    """
    scrub = []
    for _ in range(20):
        scrub.extend([(50.0, 20.0), (50.0, 120.0)])
    marked = mark_glyph(CROSS, [tuple(scrub)])
    assert marked.score < 0.2


def test_a_letter_traced_backwards_keeps_its_shape_and_loses_its_movement() -> None:
    marked = mark_glyph(CROSS, traced(CROSS, reverse=True))
    assert not marked.strokes[0].forward
    assert marked.score == pytest.approx(BACKWARDS_KEEPS, abs=0.02)


def test_a_circle_traced_the_wrong_way_is_caught() -> None:
    """The letter where endpoints cannot tell: an `o` starts and ends in the same
    place whichever way round it was drawn."""
    o = load_alphabet().glyph("o")
    assert mark_glyph(o, traced(o)).strokes[0].forward
    assert not mark_glyph(o, traced(o, reverse=True)).strokes[0].forward


def test_a_missing_stroke_is_named_rather_than_averaged_away() -> None:
    marked = mark_glyph(CROSS, traced(CROSS, skip={1}))
    assert marked.missed == 1
    assert marked.strokes[1].ink_index is None
    assert marked.score == pytest.approx(0.5, abs=0.02)


def test_writing_the_strokes_in_the_wrong_order_costs_only_a_little() -> None:
    """Deliberate: a six-year-old who draws the bar of a `t` first has written a
    `t`. Rejecting it would teach them they cannot write."""
    marked = mark_glyph(CROSS, traced(CROSS, order=[1, 0]))
    assert not marked.in_order
    assert marked.score == pytest.approx(1.0 - ORDER_COST, abs=0.02)


def test_the_matching_is_by_fit_rather_than_by_position() -> None:
    """Drawing the bar first must mark both strokes as drawn -- pairing by
    position would call each of them the wrong shape instead."""
    marked = mark_glyph(CROSS, traced(CROSS, order=[1, 0]))
    assert marked.missed == 0
    assert [stroke.ink_index for stroke in marked.strokes] == [1, 0]


def test_ink_that_belongs_to_no_stroke_leaves_the_letter_unattempted() -> None:
    away = [tuple((float(x), 5.0) for x in range(0, 100, 3))]
    marked = mark_glyph(CROSS, away)
    assert not marked.attempted
    assert marked.score == 0.0


def test_going_over_a_letter_twice_to_be_sure_costs_nothing() -> None:
    o = load_alphabet().glyph("o")
    once = list(sample(o.strokes[0], 3.0))
    assert mark_glyph(o, [tuple(once + once)]).score == pytest.approx(1.0, abs=0.05)


def test_drawing_far_more_ink_than_the_letter_needs_does_cost() -> None:
    o = load_alphabet().glyph("o")
    once = list(sample(o.strokes[0], 3.0))
    assert mark_glyph(o, [tuple(once * 6)]).score < 0.6


def test_resampling_stops_at_the_limit_it_was_given() -> None:
    """The output grows with the polyline's length, not with how many points went
    in, so a caller taking points off the wire has to be able to bound it."""
    far = [(0.0, 0.0), (10000.0, 0.0)]
    assert len(resample(far, spacing=1.0, limit=50)) == 50
    assert len(resample(far, spacing=1.0)) > 50


def test_ink_is_bounded_before_it_is_measured_against_anything() -> None:
    """The denial-of-service shape, and the reason `prepare_ink` exists.

    Marking costs guide points times ink points. A body of a few hundred points
    that zig-zag across the page is a polyline hundreds of thousands of units
    long, and resampling it evenly would produce points in proportion to *that*
    -- turning a small request into minutes of arithmetic.
    """
    zigzag = tuple((400.0 if index % 2 else -200.0, float(index)) for index in range(500))
    prepared = prepare_ink([zigzag] * 8)

    assert sum(len(points) for points, _ in prepared) <= MAX_INK_POINTS + 16


def test_trimming_ink_does_not_flatter_it() -> None:
    """The length `economy` judges is measured on the points as posted. Measuring
    it after the trim would report a short, tidy stroke -- so the cheapest way to
    score well would be to send something enormous."""
    zigzag = tuple((400.0 if index % 2 else -200.0, float(index)) for index in range(500))
    ((_, drawn),) = prepare_ink([zigzag])

    assert drawn > 100_000
    marked = mark_glyph(CROSS, [zigzag])
    assert marked.score < 0.05


# --- marking a whole prompt ----------------------------------------------


def attempt_for(glyph: Glyph, count: int, *, seconds: float = 10.0, **kwargs) -> Attempt:
    return Attempt(
        seconds=seconds,
        glyphs=tuple(
            TracedGlyph(
                index=index,
                strokes=tuple(InkStroke(points=points) for points in traced(glyph, **kwargs)),
            )
            for index in range(count)
        ),
    )


def test_a_prompt_is_the_mean_over_every_character_it_asked_for() -> None:
    """Over every character, not over the ones attempted: skipping the hard
    letter must not raise the score."""
    letters = alphabet()
    written = prompt(text="++")
    half = Attempt(
        seconds=5.0,
        glyphs=(
            TracedGlyph(
                index=0,
                strokes=tuple(InkStroke(points=points) for points in traced(CROSS)),
            ),
        ),
    )
    marked = mark(written, letters, half)
    assert marked.attempted == 1
    assert not marked.finished
    assert marked.score == pytest.approx(0.5, abs=0.02)


def test_a_finished_prompt_says_so() -> None:
    marked = mark(prompt(text="++"), alphabet(), attempt_for(CROSS, 2))
    assert marked.finished
    assert marked.score == pytest.approx(1.0)


def test_an_empty_attempt_scores_nothing_rather_than_erroring() -> None:
    marked = mark(prompt(text="++"), alphabet(), Attempt(seconds=1.0))
    assert marked.score == 0.0
    assert not marked.finished


# --- rewards --------------------------------------------------------------


def test_an_ordinary_tracing_earns_all_three_stars() -> None:
    """The thresholds are forgiving on purpose. A fingertip is blunter than a
    pencil, and thresholds tuned for a pencil would fail a child who was doing
    it right."""
    marked = mark(prompt(text="++"), alphabet(), attempt_for(CROSS, 2, jitter=5))
    badges = earned(marked)
    assert badges.stars == MAX_STARS
    assert badges.finished
    assert badges.even


def test_finishing_is_awarded_however_wobbly_the_letters_were() -> None:
    marked = Mark(
        glyphs=mark(prompt(text="++"), alphabet(), attempt_for(CROSS, 2)).glyphs, seconds=1
    )
    assert earned(marked).finished


def test_the_even_badge_needs_every_letter_and_not_the_average() -> None:
    letters = alphabet()
    good = TracedGlyph(index=0, strokes=tuple(InkStroke(points=points) for points in traced(CROSS)))
    poor = TracedGlyph(
        index=1, strokes=tuple(InkStroke(points=points) for points in traced(CROSS, skip={1}))
    )
    marked = mark(prompt(text="++"), letters, Attempt(seconds=8.0, glyphs=(good, poor)))
    assert marked.score > 0.7
    assert not earned(marked).even


@pytest.mark.parametrize(
    ("score", "stars"), [(1.0, 3), (0.9, 3), (0.7, 2), (0.5, 1), (0.2, 0), (0.0, 0)]
)
def test_stars_rise_with_the_score(score: float, stars: int) -> None:
    assert stars_for(score) == stars


# --- the library and its review gate --------------------------------------


def library(*prompts: WritingPrompt, **kwargs) -> WritingLibrary:
    return WritingLibrary(
        [WritingSet(subject="NOR01-08", goal_set="KV1107", prompts=prompts or (prompt(),))],
        alphabet(),
        **kwargs,
    )


def test_an_unreviewed_prompt_is_withheld_by_default() -> None:
    held = library(prompt(reviewed=False))
    assert held.for_goal_set("KV1107") == []
    assert not held.has_writing("KV1107")


def test_an_unreviewed_prompt_is_served_to_someone_allowed_to_see_drafts() -> None:
    held = library(prompt(reviewed=False))
    assert len(held.for_goal_set("KV1107", unreviewed=True)) == 1


def test_the_deployment_wide_switch_widens_it_too() -> None:
    held = library(prompt(reviewed=False), include_unreviewed=True)
    assert len(held.for_goal_set("KV1107")) == 1


def test_a_prompt_the_alphabet_cannot_draw_is_withheld_rather_than_shown_blank() -> None:
    held = library(prompt(text="ø"))
    assert held.for_goal_set("KV1107") == []


def test_an_unknown_goal_set_is_empty_rather_than_an_error() -> None:
    assert library().for_goal_set("KV9999") == []


# --- the schema's own refusals --------------------------------------------


def test_a_prompt_with_a_space_in_it_is_refused() -> None:
    with pytest.raises(ValueError, match="without spaces"):
        prompt(text="a b")


def test_a_digits_prompt_holding_letters_is_refused() -> None:
    with pytest.raises(ValueError, match="only digits"):
        prompt(kind="digits", text="1a")


def test_a_word_prompt_holding_digits_is_refused() -> None:
    with pytest.raises(ValueError, match="only letters"):
        prompt(kind="word", text="s7")


def test_a_letterform_that_leaves_the_box_is_refused() -> None:
    """It would be clipped on the page, and the scorer would then mark a pupil
    down for not tracing something they could not see."""
    with pytest.raises(ValueError, match="leaves the box"):
        Alphabet(metrics=METRICS, glyphs=(Glyph(char="x", strokes=("M 10 10 L 400 10",)),))


def test_an_alphabet_defining_a_character_twice_is_refused() -> None:
    with pytest.raises(ValueError, match="more than once"):
        Alphabet(metrics=METRICS, glyphs=(CROSS, CROSS))


def test_writing_lines_out_of_order_are_refused() -> None:
    with pytest.raises(ValueError, match="top to bottom"):
        Metrics(width=100, height=140, ascender=90, x_height=60, baseline=110, descender=135)


# --- what the page may post -----------------------------------------------


def test_a_tracing_that_took_no_time_is_refused() -> None:
    with pytest.raises(ValueError):
        Attempt(seconds=0)


def test_a_tracing_that_claims_to_have_taken_all_afternoon_is_refused() -> None:
    with pytest.raises(ValueError):
        Attempt(seconds=100000)


def test_a_stroke_of_one_point_is_refused() -> None:
    with pytest.raises(ValueError):
        InkStroke(points=((1.0, 1.0),))


def test_a_tapped_dot_marks_as_a_written_dot() -> None:
    """The dot on an `i` is a 4-unit stroke, and a tap has no length.

    The page turns a tap into a mark that size rather than throwing it away, and
    this is the marking end of that: what it sends has to score like a dot, or
    the fix would only move the problem from "the dot never registers" to "the
    dot never counts". The old behaviour -- the tap discarded, the stem alone --
    is the second half of the assertion.
    """
    dotted = Glyph(char="i", strokes=("M 50 60 L 50 110", "M 50 38 L 50 42"))
    stem = tuple((50.0, 60.0 + step * 3) for step in range(17))
    tap = ((50.0, 38.0), (50.0, 42.0))

    with_dot = Attempt(
        seconds=4.0,
        glyphs=(TracedGlyph(index=0, strokes=(InkStroke(points=stem), InkStroke(points=tap))),),
    )
    without = Attempt(
        seconds=4.0,
        glyphs=(TracedGlyph(index=0, strokes=(InkStroke(points=stem),)),),
    )

    letters = alphabet(dotted)
    assert mark(prompt(text="i"), letters, with_dot).score > 0.95
    assert mark(prompt(text="i"), letters, without).score < 0.6


def test_points_far_outside_the_box_are_clamped_rather_than_refused() -> None:
    """Overshooting is information, not an attack: it is what "outside the lines"
    means. Only the absurd is cut off."""
    stroke = InkStroke(points=((0.0, 0.0), (1e9, -1e9)))
    x, y = stroke.clamped()[1]
    assert x == 400.0
    assert y == -200.0


# --- the routes -----------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Catalogue.load(), settings=Settings()))


def real_attempt(client: TestClient, url: str, characters: str, **kwargs) -> dict:
    letters = load_alphabet()
    glyphs = []
    for index, char in enumerate(characters):
        strokes = traced(letters.glyph(char), **kwargs)
        glyphs.append(
            {"index": index, "strokes": [{"points": [list(p) for p in s]} for s in strokes]}
        )
    return {"seconds": 12.0, "glyphs": glyphs}


def test_the_index_lists_the_prompts_for_the_checkpoint(client: TestClient) -> None:
    response = client.get("/nb/klasse/1/NOR01-08/skriving")
    assert response.status_code == 200
    assert "nor-kv1107-sma-streker" in response.text


def test_a_checkpoint_with_no_prompts_is_a_404(client: TestClient) -> None:
    assert client.get("/nb/klasse/7/NAT01-05/skriving").status_code == 404


def test_the_page_draws_the_guide_and_the_starting_dots(client: TestClient) -> None:
    """Without these the exercise is a blank box: the shape is obvious and the
    starting point is not."""
    response = client.get("/nb/klasse/1/NOR01-08/skriving/nor-kv1107-sma-streker")
    assert response.status_code == 200
    # i, l, t and j: two strokes, one, two, two.
    assert response.text.count('<path d="') == 7
    assert response.text.count("<circle") == 7


def test_the_page_works_without_javascript(client: TestClient) -> None:
    """Every letter is rendered server-side, so a browser with no script shows a
    worksheet rather than an empty frame."""
    body = client.get("/nb/klasse/1/NOR01-08/skriving/nor-kv1107-sma-streker").text
    assert body.count('class="writing-card"') == 4
    assert "noscript" in body


def test_a_tidy_tracing_is_marked_well(client: TestClient) -> None:
    url = "/nb/klasse/1/NOR01-08/skriving/nor-kv1107-sma-streker/spor"
    response = client.post(url, json=real_attempt(client, url, "iltj"))
    assert response.status_code == 200
    assert response.text.count("star--on") == MAX_STARS
    assert "100%" in response.text


def test_a_prompt_only_half_written_says_which_letters_are_missing(client: TestClient) -> None:
    url = "/nb/klasse/1/NOR01-08/skriving/nor-kv1107-sma-streker/spor"
    body = real_attempt(client, url, "iltj")
    body["glyphs"] = body["glyphs"][:2]
    response = client.post(url, json=body)
    assert response.status_code == 200
    assert "ikke skrevet" in response.text


def test_an_unknown_prompt_is_a_404_rather_than_a_crash(client: TestClient) -> None:
    response = client.post(
        "/nb/klasse/1/NOR01-08/skriving/nope/spor", json={"seconds": 1.0, "glyphs": []}
    )
    assert response.status_code == 404


def test_an_implausible_tracing_is_refused_by_the_schema(client: TestClient) -> None:
    response = client.post(
        "/nb/klasse/1/NOR01-08/skriving/nor-kv1107-sma-streker/spor",
        json={"seconds": -3.0, "glyphs": []},
    )
    assert response.status_code == 422


def test_an_unknown_locale_is_a_404(client: TestClient) -> None:
    assert client.get("/de/klasse/1/NOR01-08/skriving").status_code == 404


def test_the_subject_page_links_to_the_exercise_where_prompts_exist(client: TestClient) -> None:
    assert "/nb/klasse/1/NOR01-08/skriving" in client.get("/nb/klasse/1/NOR01-08").text


def test_the_subject_page_stays_quiet_where_none_do(client: TestClient) -> None:
    assert "/skriving" not in client.get("/nb/klasse/7/NAT01-05").text
