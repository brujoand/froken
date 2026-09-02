"""What the release workflow promises about the image it publishes.

Nothing here runs the workflow. What is worth pinning is where its facts come
from: a value that drifts from the project without anyone editing the project is
how the published image spent a week describing itself by the repository's
previous name.

And *when* it publishes, which is the other way this has gone wrong. Release
used to trigger on the same push CI did, so the two ran side by side and a
commit whose container had already failed to start was tagged, pushed and
dispatched to the deployment repo anyway. That was v1.12.0. The assertions
below are what stop the trigger quietly going back to `push`.
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


# --- when it publishes ----------------------------------------------------


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text())


@pytest.fixture(scope="module")
def triggers(workflow: dict) -> dict:
    # PyYAML reads a bare `on:` key as the boolean True, this being YAML.
    return workflow[True] if True in workflow else workflow["on"]


def test_a_release_waits_for_ci_rather_than_racing_it(triggers: dict) -> None:
    """The whole point. On `push` the two workflows start together and the
    release cannot know what CI is about to conclude."""
    assert "push" not in triggers
    assert triggers["workflow_run"]["workflows"] == ["CI"]
    assert triggers["workflow_run"]["branches"] == ["main"]


def test_the_ci_workflow_is_named_what_the_trigger_says_it_is(triggers: dict) -> None:
    """`workflow_run` matches on the workflow's `name`, and a rename would not
    error -- the release would simply never trigger again."""
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())

    assert ci["name"] in triggers["workflow_run"]["workflows"]


def test_ci_actually_runs_on_pushes_to_main(triggers: dict) -> None:
    """`workflow_run` fires off CI's own run. If CI stopped running on main
    there would be nothing to fire off, and releases would stop silently."""
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text())
    ci_on = ci[True] if True in ci else ci["on"]

    assert "main" in ci_on["push"]["branches"]


def test_nothing_is_tagged_unless_ci_passed(workflow: dict) -> None:
    """`version` is where the tag and the GitHub release are created, so it is
    the job that has to be gated. Gating `publish` instead would leave a release
    with no image behind it."""
    guard = " ".join(workflow["jobs"]["version"]["if"].split())

    assert "github.event.workflow_run.conclusion == 'success'" in guard
    # The one exemption, and it is a human asking on purpose.
    assert "github.event_name == 'workflow_dispatch'" in guard


def test_the_publish_job_still_hangs_off_the_version_job(workflow: dict) -> None:
    """Gating `version` only gates `publish` because `publish` needs it."""
    publish = workflow["jobs"]["publish"]

    assert publish["needs"] == "version" or "version" in publish["needs"]
    assert "needs.version.outputs.released == 'true'" in publish["if"]


def test_every_checkout_pins_the_commit_ci_verified(workflow: dict) -> None:
    """A `workflow_run` job checks out the default branch by default, not the
    commit that triggered it. Left alone, a push landing mid-release would be
    built into an image tagged with the previous commit's version."""
    verified = workflow["env"]["VERIFIED_SHA"]
    assert "github.event.workflow_run.head_sha" in verified

    for name in ("version", "publish"):
        checkout = next(
            step
            for step in workflow["jobs"][name]["steps"]
            if str(step.get("uses", "")).startswith("actions/checkout")
        )
        assert checkout["with"]["ref"] == "${{ env.VERIFIED_SHA }}", name


def test_a_blocked_release_says_so_out_loud(workflow: dict) -> None:
    """A workflow run full of skipped jobs looks exactly like one that had
    nothing to release, and those are very different facts about main."""
    blocked = workflow["jobs"]["blocked"]
    guard = " ".join(blocked["if"].split())

    assert "conclusion != 'success'" in guard
    run = " ".join(step.get("run", "") for step in blocked["steps"])
    assert "::warning::" in run
    assert "GITHUB_STEP_SUMMARY" in run


def test_the_deploy_dispatch_does_not_depend_on_the_event_shape(
    publish_steps: list[dict],
) -> None:
    """The payload of a `workflow_run` is not the payload of a `push`. A name
    read from the event would come out empty here, and an empty app name pins
    nothing -- quietly, since the dispatch itself would still succeed."""
    dispatch = next(s for s in publish_steps if "workflow run" in s.get("run", ""))

    assert "github.event.repository" not in str(dispatch.get("env", {}))
    assert "GITHUB_REPOSITORY" in dispatch["run"] or "REPOSITORY" in dispatch["run"]
