"""Look at the figures.

A figure is authored as a handful of numbers, and a handful of numbers is not
something anyone can review against the sentence it illustrates. This prints
every committed figure as an SVG contact sheet, each one under the prompt it
belongs to, so the question a reviewer has to answer -- does the picture say the
same thing as the words -- can be answered by looking.

    uv run python tools/render_figures.py > /tmp/figures.html   # every figure
    uv run python tools/render_figures.py MAT01-06 > /tmp/f.html
    uv run python tools/render_figures.py --gallery > /tmp/g.html

`--gallery` ignores the committed data and draws one of each kind with made-up
parameters. That is the sheet to look at when changing the geometry itself: it
covers the shapes and options no item happens to use yet, which is exactly where
a regression hides.

Unreviewed items are included and labelled. A draft figure is the main thing
anyone would want to look at here.
"""

from __future__ import annotations

import argparse
import html
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pensum.items.figures import (  # noqa: E402
    ArrayFigure,
    CountersFigure,
    Drawing,
    FractionFigure,
    NumberLineFigure,
    ShapeFigure,
    draw,
)
from pensum.items.loader import ItemBank  # noqa: E402
from pensum.items.text import AuthoredText  # noqa: E402

ALT = AuthoredText(nb="Eksempelfigur", en="Example figure")


def gallery() -> list[tuple[str, object]]:
    """One of each kind, plus the options no committed item exercises yet."""
    shapes = [
        ShapeFigure(alt=ALT, shape=name)
        for name in (
            "triangle",
            "right_triangle",
            "square",
            "rectangle",
            "parallelogram",
            "rhombus",
            "trapezoid",
            "pentagon",
            "hexagon",
            "octagon",
            "circle",
        )
    ]
    return [(f"shape: {f.shape}", f) for f in shapes] + [
        (
            "shape: sides and vertices labelled",
            ShapeFigure(
                alt=ALT,
                shape="triangle",
                sides=("5 cm", "4 cm", "3 cm"),
                vertices=("A", "B", "C"),
            ),
        ),
        (
            "shape: right angle marked",
            ShapeFigure(alt=ALT, shape="right_triangle", right_angles=(2,), sides=("", "", "g")),
        ),
        (
            "shape: altitude",
            ShapeFigure(alt=ALT, shape="triangle", height="h", sides=("", "g", "")),
        ),
        ("shape: stretched rectangle", ShapeFigure(alt=ALT, shape="rectangle", ratio=2.5)),
        ("shape: shaded", ShapeFigure(alt=ALT, shape="hexagon", shaded=True)),
        ("shape: circle with radius", ShapeFigure(alt=ALT, shape="circle", radius="r")),
        ("shape: circle with diameter", ShapeFigure(alt=ALT, shape="circle", diameter="d")),
        ("counters: seven", CountersFigure(alt=ALT, count=7)),
        ("counters: wrapped", CountersFigure(alt=ALT, count=23)),
        ("counters: three groups of four", CountersFigure(alt=ALT, count=12, groups=(4, 4, 4))),
        ("counters: shaded", CountersFigure(alt=ALT, count=10, shaded=3)),
        ("array: 3 by 4", ArrayFigure(alt=ALT, rows=3, columns=4, row_label="3", column_label="4")),
        ("array: shaded", ArrayFigure(alt=ALT, rows=4, columns=5, shaded=7)),
        (
            "fraction: bar",
            FractionFigure(alt=ALT, rows=({"parts": 4, "shaded": 1, "label": "1/4"},)),
        ),
        (
            "fraction: compared",
            FractionFigure(
                alt=ALT,
                rows=(
                    {"parts": 2, "shaded": 1, "label": "1/2"},
                    {"parts": 4, "shaded": 2, "label": "2/4"},
                ),
            ),
        ),
        (
            "fraction: circles",
            FractionFigure(
                alt=ALT,
                shape="circle",
                rows=({"parts": 8, "shaded": 3, "label": "3/8"}, {"parts": 3, "shaded": 1}),
            ),
        ),
        ("number_line: plain", NumberLineFigure(alt=ALT, start=0, end=10, step=1)),
        (
            "number_line: sparse labels and an unknown",
            NumberLineFigure(
                alt=ALT,
                start=0,
                end=100,
                step=5,
                label_every=4,
                marks=({"at": 35, "unknown": True},),
            ),
        ),
        (
            "number_line: jumps backwards",
            NumberLineFigure(
                alt=ALT,
                start=10,
                end=20,
                step=2,
                jumps=(
                    {"start": 20, "end": 18, "label": "-2"},
                    {"start": 18, "end": 16, "label": "-2"},
                ),
            ),
        ),
    ]


def committed(subjects: list[str]) -> list[tuple[str, object]]:
    bank = ItemBank.load(include_unreviewed=True)
    out = []
    for item_set in sorted(bank.item_sets, key=lambda s: (s.subject, s.goal_set)):
        if subjects and item_set.subject not in subjects:
            continue
        for item in item_set.items:
            if item.figure is None:
                continue
            draft = "" if item.reviewed else "  [DRAFT]"
            out.append(
                (
                    f"{item_set.subject} / {item_set.goal_set} / {item.id}{draft}\n"
                    f"{item.prompt.nb}",
                    item.figure,
                )
            )
    return out


def svg(drawing: Drawing) -> str:
    parts = [
        f'<svg class="fig" viewBox="0 0 {drawing.width:.0f} {drawing.height:.0f}" '
        f'role="img" aria-label="{html.escape(drawing.alt)}">'
    ]
    for path in drawing.paths:
        parts.append(f'<path class="fig-{path.role}" d="{path.d}" />')
    for dot in drawing.dots:
        parts.append(
            f'<circle class="fig-{dot.role}" cx="{dot.cx:.2f}" cy="{dot.cy:.2f}" r="{dot.r:.2f}" />'
        )
    for label in drawing.labels:
        parts.append(
            f'<text class="fig-{label.role}" x="{label.x:.2f}" y="{label.y:.2f}" '
            f'font-size="{label.size:.1f}" text-anchor="{label.anchor}" '
            f'dominant-baseline="{label.baseline}">{html.escape(label.text)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


STYLE = """
body { font: 15px/1.5 system-ui, sans-serif; margin: 2rem; background: #fbfaf7; color: #1c2024; }
.sheet { display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 1.5rem; }
figure { margin: 0; border: 1px solid #dfdcd4; border-radius: 10px; padding: 0.75rem; background: #fff; }
figcaption { font-size: 13px; color: #5b6570; white-space: pre-wrap; margin-top: 0.5rem; }
svg.fig { display: block; width: 100%; height: auto; }
.fig-outline { fill: none; stroke: #1c2024; stroke-width: 2; stroke-linejoin: round; }
.fig-fill { fill: #1f5c8b; fill-opacity: 0.28; stroke: #1c2024; stroke-width: 2; }
.fig-guide { fill: none; stroke: #5b6570; stroke-width: 1.6; stroke-dasharray: 5 4; }
.fig-axis { fill: none; stroke: #1c2024; stroke-width: 2; stroke-linecap: round; }
.fig-jump { fill: none; stroke: #1f5c8b; stroke-width: 2; }
.fig-jump-head { fill: none; stroke: #1f5c8b; stroke-width: 2; stroke-linejoin: round; }
circle.fig-outline { fill: #fff; }
circle.fig-fill { fill: #1f5c8b; fill-opacity: 1; }
circle.fig-mark { fill: #1c2024; stroke: none; }
.fig-label { fill: #1c2024; }
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("subjects", nargs="*", help="only these subject codes")
    parser.add_argument(
        "--gallery", action="store_true", help="draw one of each kind instead of the committed data"
    )
    args = parser.parse_args()

    entries = gallery() if args.gallery else committed(args.subjects)
    if not entries:
        print("No figures found.", file=sys.stderr)
        return 1

    cards = "\n".join(
        f"<figure>{svg(draw(figure, 'nb'))}<figcaption>{html.escape(caption)}</figcaption></figure>"
        for caption, figure in entries
    )
    print(
        f"<!doctype html><meta charset=utf-8><title>Pensum figures</title>"
        f"<style>{STYLE}</style>"
        f"<h1>{len(entries)} figures</h1><div class=sheet>{cards}</div>"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
