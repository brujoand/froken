"""The geometry behind a question's picture.

A figure is arithmetic with no I/O, so these assert the numbers rather than the
markup: that 2/4 shades half the bar, that a jump backwards points left, that a
3 by 4 array has twelve cells. A test that only checked the SVG parsed would
pass on a drawing that says the wrong thing.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from pensum.items.figures import (
    VIEW,
    ArrayFigure,
    CountersFigure,
    FractionFigure,
    NumberLineFigure,
    ShapeFigure,
    draw,
)
from pensum.items.text import AuthoredText

ALT = AuthoredText(nb="En figur", en="A figure")


def shape(**kwargs) -> ShapeFigure:
    return ShapeFigure(alt=ALT, **kwargs)


# --- shapes ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "corners"),
    [
        ("triangle", 3),
        ("right_triangle", 3),
        ("square", 4),
        ("rectangle", 4),
        ("parallelogram", 4),
        ("rhombus", 4),
        ("trapezoid", 4),
        ("pentagon", 5),
        ("hexagon", 6),
        ("octagon", 8),
    ],
)
def test_a_polygon_is_drawn_with_the_corners_its_name_promises(name: str, corners: int) -> None:
    """The one property a shape figure must never get wrong."""
    drawing = draw(shape(shape=name), "nb")
    assert drawing.paths[0].d.count("L") == corners - 1


def test_every_shape_fits_inside_its_box() -> None:
    """A vertex outside the viewBox is a corner the browser silently clips."""
    for name in ("triangle", "hexagon", "rhombus", "trapezoid", "octagon"):
        for token in _coordinates(draw(shape(shape=name), "nb").paths[0].d):
            assert 0 <= token <= VIEW


@pytest.mark.parametrize("name", ["square", "rhombus", "pentagon", "hexagon", "octagon"])
def test_a_shape_is_drawn_in_its_own_proportions(name: str) -> None:
    """A square drawn in a rectangle's box is a rectangle. This is the check
    that a shared drawing box cannot silently rename a figure.
    """
    points = _coordinates(draw(shape(shape=name), "nb").paths[0].d)
    xs, ys = points[0::2], points[1::2]
    aspect = (max(xs) - min(xs)) / (max(ys) - min(ys))
    if name in ("square", "rhombus"):
        assert aspect == pytest.approx(1.0)
    else:
        # A regular polygon on a flat edge: wider than tall, by its own geometry
        # and never by more than the circle it is inscribed in.
        assert 1.0 <= aspect < 1.3


def test_side_labels_follow_the_sides() -> None:
    """Three labels on a triangle land in three different places."""
    drawing = draw(shape(shape="triangle", sides=("a", "b", "c")), "nb")
    placed = {label.text: (round(label.x), round(label.y)) for label in drawing.labels}
    assert set(placed) == {"a", "b", "c"}
    assert len(set(placed.values())) == 3


def test_an_empty_side_label_draws_nothing() -> None:
    """So an author can name one side of four without inventing names for the rest."""
    drawing = draw(shape(shape="square", sides=("4 cm", "", "", "")), "nb")
    assert [label.text for label in drawing.labels] == ["4 cm"]


def test_the_altitude_lands_on_the_base() -> None:
    drawing = draw(shape(shape="triangle", height="h"), "nb")
    guide = next(path for path in drawing.paths if path.role == "guide")
    top_x, _, foot_x, foot_y = _coordinates(guide.d)
    assert top_x == foot_x
    # The base is the lowest edge, so the foot is at the figure's bottom.
    assert foot_y == pytest.approx(max(_coordinates(drawing.paths[0].d)[1::2]))


def test_a_right_angle_mark_is_drawn_inside_the_corner() -> None:
    drawing = draw(shape(shape="right_triangle", right_angles=(2,)), "nb")
    mark = next(path for path in drawing.paths if path.role == "guide")
    assert mark.d.count("L") == 2


def test_alt_text_follows_the_locale() -> None:
    figure = ShapeFigure(shape="square", alt=AuthoredText(nb="Et kvadrat", en="A square"))
    assert draw(figure, "nb").alt == "Et kvadrat"
    assert draw(figure, "en").alt == "A square"


def test_a_circle_refuses_sides_and_a_polygon_refuses_a_radius() -> None:
    """The two mistakes a copy-pasted figure block actually makes."""
    with pytest.raises(ValidationError):
        shape(shape="circle", sides=("a",))
    with pytest.raises(ValidationError):
        shape(shape="square", radius="r")


def test_a_label_count_that_does_not_match_the_shape_is_refused() -> None:
    """Four labels on a triangle means the author was thinking of another figure."""
    with pytest.raises(ValidationError):
        shape(shape="triangle", sides=("a", "b", "c", "d"))
    with pytest.raises(ValidationError):
        shape(shape="pentagon", vertices=("A", "B", "C"))


def test_only_shapes_with_one_base_can_carry_a_height() -> None:
    with pytest.raises(ValidationError):
        shape(shape="hexagon", height="h")
    with pytest.raises(ValidationError):
        shape(shape="right_triangle", height="h")


def test_a_shape_that_would_stop_being_itself_cannot_be_stretched() -> None:
    with pytest.raises(ValidationError):
        shape(shape="square", ratio=2.0)
    with pytest.raises(ValidationError):
        shape(shape="hexagon", ratio=2.0)


# --- counters ----------------------------------------------------------------


def test_counters_draw_one_dot_each() -> None:
    assert len(draw(CountersFigure(alt=ALT, count=7), "nb").dots) == 7


def test_counters_wrap_rather_than_run_off_the_page() -> None:
    drawing = draw(CountersFigure(alt=ALT, count=23, per_row=10), "nb")
    assert len(drawing.dots) == 23
    assert len({round(dot.cy) for dot in drawing.dots}) == 3


def test_groups_are_drawn_one_per_row() -> None:
    """Three groups of four is a claim about rows, not just about a total."""
    drawing = draw(CountersFigure(alt=ALT, count=12, groups=(4, 4, 4)), "nb")
    rows = {round(dot.cy) for dot in drawing.dots}
    assert len(rows) == 3


def test_shaded_counters_come_first() -> None:
    drawing = draw(CountersFigure(alt=ALT, count=5, shaded=2), "nb")
    assert [dot.role for dot in drawing.dots] == ["fill", "fill", "outline", "outline", "outline"]


def test_groups_that_do_not_add_up_are_refused() -> None:
    with pytest.raises(ValidationError):
        CountersFigure(alt=ALT, count=12, groups=(4, 4))


def test_more_shaded_than_counters_is_refused() -> None:
    with pytest.raises(ValidationError):
        CountersFigure(alt=ALT, count=3, shaded=4)


# --- arrays ------------------------------------------------------------------


def test_an_array_draws_a_cell_per_square() -> None:
    drawing = draw(ArrayFigure(alt=ALT, rows=3, columns=4), "nb")
    assert len(drawing.paths) == 12


def test_array_cells_are_square() -> None:
    """The whole argument for this figure is that the area can be counted."""
    drawing = draw(ArrayFigure(alt=ALT, rows=2, columns=5), "nb")
    xs, ys = _coordinates(drawing.paths[0].d)[0::2], _coordinates(drawing.paths[0].d)[1::2]
    assert max(xs) - min(xs) == pytest.approx(max(ys) - min(ys))


def test_array_shading_fills_in_reading_order() -> None:
    drawing = draw(ArrayFigure(alt=ALT, rows=2, columns=3, shaded=4), "nb")
    assert [path.role for path in drawing.paths] == [
        "fill",
        "fill",
        "fill",
        "fill",
        "outline",
        "outline",
    ]


def test_more_shaded_than_cells_is_refused() -> None:
    with pytest.raises(ValidationError):
        ArrayFigure(alt=ALT, rows=2, columns=2, shaded=5)


# --- fractions ---------------------------------------------------------------


def test_a_fraction_bar_has_one_part_per_denominator() -> None:
    drawing = draw(FractionFigure(alt=ALT, rows=({"parts": 8, "shaded": 3},)), "nb")
    assert len(drawing.paths) == 8
    assert sum(path.role == "fill" for path in drawing.paths) == 3


def test_equivalent_fractions_shade_the_same_width() -> None:
    """The reason a comparison is drawn at all: 1/2 and 2/4 must look equal."""
    drawing = draw(
        FractionFigure(
            alt=ALT,
            rows=({"parts": 2, "shaded": 1}, {"parts": 4, "shaded": 2}),
        ),
        "nb",
    )
    halves = [p for p in drawing.paths[:2] if p.role == "fill"]
    quarters = [p for p in drawing.paths[2:] if p.role == "fill"]
    assert _width(halves) == pytest.approx(_width(quarters))


def test_fraction_circles_close_the_whole() -> None:
    """Six sixths must be a circle, not five sixths and a gap."""
    drawing = draw(FractionFigure(alt=ALT, shape="circle", rows=({"parts": 6, "shaded": 1},)), "nb")
    assert len(drawing.paths) == 6
    assert all(path.d.endswith("Z") for path in drawing.paths)


def test_shading_more_than_the_whole_is_refused() -> None:
    with pytest.raises(ValidationError):
        FractionFigure(alt=ALT, rows=({"parts": 4, "shaded": 5},))


# --- number lines ------------------------------------------------------------


def test_a_number_line_ticks_every_step() -> None:
    drawing = draw(NumberLineFigure(alt=ALT, start=0, end=10, step=1), "nb")
    # One path for the line itself, then one tick per value.
    assert len(drawing.paths) == 1 + 11
    assert [label.text for label in drawing.labels] == [str(n) for n in range(11)]


def test_label_every_thins_the_numbers_without_thinning_the_ticks() -> None:
    drawing = draw(NumberLineFigure(alt=ALT, start=0, end=100, step=5, label_every=4), "nb")
    assert len(drawing.paths) == 1 + 21
    assert [label.text for label in drawing.labels] == ["0", "20", "40", "60", "80", "100"]


def test_an_unknown_mark_is_hollow_and_asks() -> None:
    drawing = draw(
        NumberLineFigure(alt=ALT, start=0, end=10, step=1, marks=({"at": 4, "unknown": True},)),
        "nb",
    )
    assert [dot.role for dot in drawing.dots] == ["outline"]
    assert "?" in [label.text for label in drawing.labels]


def test_a_backwards_jump_points_backwards() -> None:
    """Which way the count went is the thing being taught."""
    forwards = _head(
        draw(
            NumberLineFigure(alt=ALT, start=0, end=10, step=1, jumps=({"start": 2, "end": 6},)),
            "nb",
        )
    )
    backwards = _head(
        draw(
            NumberLineFigure(alt=ALT, start=0, end=10, step=1, jumps=({"start": 6, "end": 2},)),
            "nb",
        )
    )
    # The arrow's wings sit behind its tip, so they trail in opposite directions.
    assert (forwards[0] < forwards[2]) != (backwards[0] < backwards[2])


def test_a_step_that_does_not_divide_the_line_is_refused() -> None:
    with pytest.raises(ValidationError):
        NumberLineFigure(alt=ALT, start=0, end=10, step=3)


def test_a_mark_off_the_end_of_the_line_is_refused() -> None:
    with pytest.raises(ValidationError):
        NumberLineFigure(alt=ALT, start=0, end=10, step=1, marks=({"at": 11},))


def test_a_line_too_long_to_read_is_refused() -> None:
    with pytest.raises(ValidationError):
        NumberLineFigure(alt=ALT, start=0, end=1000, step=1)


def test_decimal_ticks_are_written_the_norwegian_way() -> None:
    drawing = draw(NumberLineFigure(alt=ALT, start=0, end=2, step=0.5), "nb")
    assert [label.text for label in drawing.labels] == ["0", "0,5", "1", "1,5", "2"]


# --- helpers -----------------------------------------------------------------


def _coordinates(d: str) -> list[float]:
    import re

    return [float(token) for token in re.findall(r"-?\d+\.?\d*", d)]


def _width(paths: list) -> float:
    xs = [x for path in paths for x in _coordinates(path.d)[0::2]]
    return max(xs) - min(xs)


def _head(drawing) -> list[float]:
    path = next(p for p in drawing.paths if p.role == "jump-head")
    return _coordinates(path.d)
