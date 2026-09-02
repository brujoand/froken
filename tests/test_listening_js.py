"""The browser half of the listening exercise.

`listening.js` chooses the voice, and that choice is the one thing on the page
that can be wrong without looking wrong: an English voice reading "kjøleskap"
says a different word, and the child is then marked on spelling something they
were never told.

The checks live in `tests/js/listening_voice.test.js` because they have to run
the real JavaScript. This shells out to node and reports what it said -- and
adds the checks that cannot live on either side alone: that the page and the
server agree about how many answers a round has and how long one may be.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from pensum.listening.exercise import ROUND_LENGTH
from pensum.listening.marking import MAX_ANSWERS

HARNESS = Path(__file__).parent / "js" / "listening_voice.test.js"
SOURCE = Path(__file__).resolve().parents[1] / "src" / "pensum" / "web" / "static" / "listening.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_voice_picker() -> None:
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


def test_the_page_never_speaks_a_language_it_was_not_asked_to() -> None:
    """The refusal is the feature, so it is asserted against the shipped file
    rather than only inside the node harness: `pickVoice` returning null has to
    stop the exercise, not fall through to whatever voice is first."""
    source = SOURCE.read_text(encoding="utf-8")
    assert "labelNovoice" in source
    assert re.search(r"if\s*\(!voice\)", source), "listening.js no longer refuses a missing voice"


def test_a_full_round_is_within_what_the_server_will_accept() -> None:
    """The page posts one answer per question, so a round longer than the schema
    allows would be refused with a 422 at the moment the child presses the last
    button -- the one moment the page has nothing useful to say."""
    assert ROUND_LENGTH <= MAX_ANSWERS


def test_the_page_reads_its_length_from_the_round_rather_than_a_constant() -> None:
    """The script has no idea how many words a round has; it counts the question
    elements the server rendered. A second copy of the number in JavaScript is a
    second thing to keep in step, and this exercise already has enough of those.
    """
    source = SOURCE.read_text(encoding="utf-8")
    assert "questions.length" in source
    assert not re.search(r"var ROUND_LENGTH", source)
