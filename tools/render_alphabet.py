"""Look at the letterforms.

`data/writing/alphabet.yaml` is a list of path strings, and a path string is not
something anyone can review by reading it. This prints each glyph, either as
ASCII in a terminal or as an SVG sheet, with every stroke numbered and its
direction marked -- which is what a reviewer actually has to check: not that the
curve is pretty, but that the `a` starts at the top and goes round to the left.

    uv run python tools/render_alphabet.py            # every glyph, as ASCII
    uv run python tools/render_alphabet.py a b 7      # only these
    uv run python tools/render_alphabet.py --svg > /tmp/sheet.svg

Digits `1`-`9` mark where each stroke starts; `.` is the ink; `-` is a writing
line.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pensum.writing.library import load_alphabet  # noqa: E402
from pensum.writing.paths import sample  # noqa: E402

COLUMNS = 34
ROWS = 24


def ascii_glyph(strokes: list[str], metrics) -> list[str]:
    grid = [[" "] * COLUMNS for _ in range(ROWS)]

    def put(x: float, y: float, mark: str, *, over: bool = False) -> None:
        col = int(round(x / metrics.width * (COLUMNS - 1)))
        row = int(round(y / metrics.height * (ROWS - 1)))
        if 0 <= col < COLUMNS and 0 <= row < ROWS and (over or grid[row][col] == " "):
            grid[row][col] = mark

    for line in (metrics.ascender, metrics.x_height, metrics.baseline, metrics.descender):
        row = int(round(line / metrics.height * (ROWS - 1)))
        if 0 <= row < ROWS:
            grid[row] = ["-"] * COLUMNS

    for number, stroke in enumerate(strokes, start=1):
        points = sample(stroke, spacing=1.0)
        for x, y in points:
            put(x, y, ".", over=True)
        put(points[0][0], points[0][1], str(number), over=True)
        # The last point gets a lowercase letter so a reviewer can see which way
        # the stroke ran, not only where it began.
        put(points[-1][0], points[-1][1], "abcdefgh"[number - 1], over=True)

    return ["".join(row) for row in grid]


def svg_sheet(alphabet) -> str:
    metrics = alphabet.metrics
    per_row = 10
    cell_w, cell_h = metrics.width + 20, metrics.height + 40
    rows = (len(alphabet.glyphs) + per_row - 1) // per_row
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {per_row * cell_w} {rows * cell_h}">',
        "<style>path{fill:none;stroke:#222;stroke-width:6;stroke-linecap:round;"
        "stroke-linejoin:round}"
        ".rule{stroke:#cfe;stroke-width:1}text{font:10px sans-serif;fill:#c33}</style>",
    ]
    for index, glyph in enumerate(alphabet.glyphs):
        x = (index % per_row) * cell_w + 10
        y = (index // per_row) * cell_h + 10
        out.append(f'<g transform="translate({x} {y})">')
        for line in (metrics.ascender, metrics.x_height, metrics.baseline, metrics.descender):
            out.append(f'<line class="rule" x1="0" y1="{line}" x2="{metrics.width}" y2="{line}"/>')
        for number, stroke in enumerate(glyph.strokes, start=1):
            out.append(f'<path d="{stroke}"/>')
            start = sample(stroke)[0]
            out.append(f'<text x="{start[0] + 3}" y="{start[1]}">{number}</text>')
        out.append(f'<text x="0" y="{metrics.height + 20}">{glyph.char}</text>')
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chars", nargs="*", help="glyphs to render; default is all of them")
    parser.add_argument("--svg", action="store_true", help="write an SVG sheet to stdout")
    args = parser.parse_args()

    alphabet = load_alphabet()
    if args.svg:
        print(svg_sheet(alphabet))
        return 0

    wanted = args.chars or [glyph.char for glyph in alphabet.glyphs]
    for char in wanted:
        glyph = alphabet.glyph(char)
        if glyph is None:
            print(f"{char!r}: no such glyph", file=sys.stderr)
            return 1
        print(f"=== {glyph.char}  ({len(glyph.strokes)} stroke(s)) ===")
        for line in ascii_glyph(list(glyph.strokes), alphabet.metrics):
            print(line)
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
