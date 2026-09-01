"""What the release workflow promises about the image it publishes.

Nothing here runs the workflow. What is worth pinning is where its facts come
from: a value that drifts from the project without anyone editing the project is
how the published image spent a week describing itself by the repository's
previous name.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
LABEL = "org.opencontainers.image.description"


@pytest.fixture(scope="module")
def publish_steps() -> list[dict]:
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]["publish"]["steps"]


@pytest.fixture(scope="module")
def described() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["description"]


def test_the_image_description_comes_from_pyproject(publish_steps: list[dict]) -> None:
    """Not from the GitHub repository description, which is the default and is a
    field no file in this repo controls, and not from a second copy written into
    the workflow, which is a copy that can be wrong on its own.
    """
    meta = next(s for s in publish_steps if s.get("id") == "meta")
    labels = meta["with"]["labels"]

    assert LABEL in labels
    assert "steps.describe.outputs.description" in labels

    reader = next(s for s in publish_steps if s.get("id") == "describe")
    assert "pyproject.toml" in reader["run"]


def test_the_description_reader_runs_before_the_labels_are_built(
    publish_steps: list[dict],
) -> None:
    """A step output referenced before the step runs is empty, not an error, so
    the image would be labelled with nothing at all."""
    ids = [s.get("id") for s in publish_steps]

    assert ids.index("describe") < ids.index("meta")


def test_the_description_is_one_line(described: str) -> None:
    """It travels through GITHUB_OUTPUT, where a newline ends the value. A
    two-line description would label the image with the first line and lose the
    rest silently."""
    assert "\n" not in described
    assert described.strip() == described
    assert described


def test_the_description_does_not_name_the_project_something_else(described: str) -> None:
    """The rename is the reason this file exists."""
    assert "røken" not in described.lower()
    assert "roken" not in described.lower()
