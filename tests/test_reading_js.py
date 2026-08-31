"""The browser half of the reading exercise.

`reading.js` moves the live highlight on its own, without the server, whenever
the browser does the recognising -- which is the only path the published image
offers, since it ships without speech models. Two bugs got as far as a child's
iPad through that gap, so it is covered here.

The checks live in `tests/js/reading_matcher.test.js` because they have to run
the real JavaScript. This only shells out to node and reports what it said.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

HARNESS = Path(__file__).parent / "js" / "reading_matcher.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_live_highlight_matcher() -> None:
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
