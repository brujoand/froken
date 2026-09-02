"""The browser half of the writing exercise.

`writing.js` fills the meter and throws the sparks on its own, without asking
the server anything until the pupil is done. That arithmetic is the only thing
a child sees while they are actually writing, so it is covered here.

The checks live in `tests/js/writing_trace.test.js` because they have to run the
real JavaScript. This shells out to node and reports what it said -- and adds
the one check that cannot live on either side alone: that the page and the
server agree about how far off the line a fingertip may be.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pensum.writing.attempt import MAX_POINTS, MAX_STROKES
from pensum.writing.tracing import TOLERANCE

HARNESS = Path(__file__).parent / "js" / "writing_trace.test.js"
SOURCE = Path(__file__).resolve().parents[1] / "src" / "pensum" / "web" / "static" / "writing.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_meter_and_the_spark_matcher() -> None:
    result = subprocess.run(  # noqa: S603
        [shutil.which("node") or "node", str(HARNESS)],
        capture_output=True,
        text=True,
        check=False,
        cwd=HARNESS.parents[2],
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_the_harness_is_reachable() -> None:
    """Node is not a dependency of this project, so the check above skips where
    it is missing -- including, silently, if someone deletes the harness. This
    one does not skip."""
    assert HARNESS.is_file()


def test_the_page_and_the_server_agree_on_how_close_is_close_enough() -> None:
    """The one number that has to be the same in two languages.

    The page sparkles and fills its meter at this distance from the line; the
    server marks at it. A page that was kinder than the server would sparkle its
    way to a disappointing result, which is the worst version of this exercise:
    encouraging while writing, discouraging afterwards.
    """
    match = re.search(r"var TOLERANCE = ([\d.]+);", SOURCE.read_text(encoding="utf-8"))
    assert match, "writing.js no longer defines TOLERANCE"
    assert float(match.group(1)) == TOLERANCE


def test_the_page_trims_a_tracing_to_what_the_server_will_accept() -> None:
    """The page stops sampling at the same ceilings the schema enforces. If it
    stopped later, a long tracing would be refused with a 422 after the child had
    finished writing -- the one moment the page has nothing useful to say."""
    source = SOURCE.read_text(encoding="utf-8")
    caps = {
        name: float(re.search(rf"var {name} = ([\d.]+);", source).group(1))
        for name in ("MAX_POINTS", "MAX_STROKES")
    }
    assert caps["MAX_POINTS"] == MAX_POINTS
    assert caps["MAX_STROKES"] == MAX_STROKES
