"""The image has to carry the data the app loads at startup.

This exists because it did not, once. `data/writing/` was added with its loader,
its routes, its templates and its tests, and the Dockerfile was not told about
it -- so every test passed, the merge went in, and the published container
raised `FileNotFoundError` from its lifespan and never bound a port.

The image job in CI does catch it: it builds the container and asks it to serve.
But that job takes minutes, runs only after the fast ones, and its failure reads
as "the image is broken" rather than "you added a data directory and forgot the
Dockerfile". These checks run in seconds and say the second thing.

They compare declarations, not the filesystem: what the loaders say they read
against what the Dockerfile says it copies. A directory that exists but nothing
loads is not a bug, and a loader pointed at a directory nobody ships is.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from pensum.catalogue.loader import DEFAULT_DATA_DIR
from pensum.items.loader import DEFAULT_ITEMS_DIR
from pensum.reading.library import DEFAULT_READING_DIR
from pensum.writing.library import DEFAULT_WRITING_DIR

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"

# Every directory a loader reads when the app starts with no configuration.
# Adding a loader means adding it here, which is the point: the list is short
# enough to keep by hand and the failure it prevents is a crash loop.
LOADED_AT_STARTUP = {
    "curriculum": DEFAULT_DATA_DIR,
    "items": DEFAULT_ITEMS_DIR,
    "reading": DEFAULT_READING_DIR,
    "writing": DEFAULT_WRITING_DIR,
}

_COPY = re.compile(r"^COPY\s+(data/\S+?)/?\s", re.MULTILINE)


@pytest.fixture(scope="module")
def copied() -> set[str]:
    """The data paths the Dockerfile copies, relative to the repository."""
    return set(_COPY.findall(DOCKERFILE.read_text(encoding="utf-8")))


@pytest.mark.parametrize("name", sorted(LOADED_AT_STARTUP))
def test_every_data_directory_the_app_loads_is_copied_into_the_image(
    name: str, copied: set[str]
) -> None:
    """The check that was missing.

    A loader whose directory never reaches the image does not degrade -- it
    raises while the app is starting, which in a cluster is a container that
    never becomes ready and a rollout that never completes.
    """
    wanted = LOADED_AT_STARTUP[name].relative_to(ROOT).as_posix()
    assert wanted in copied, (
        f"{wanted} is read at startup but the Dockerfile never copies it; "
        "the published image would fail to launch"
    )


@pytest.mark.parametrize("name", sorted(LOADED_AT_STARTUP))
def test_every_data_directory_the_app_loads_actually_exists(name: str) -> None:
    """The other half. A COPY of a directory that is not there fails the build
    rather than the launch, but this says which loader is pointed at nothing."""
    assert LOADED_AT_STARTUP[name].is_dir()


def test_nothing_the_image_needs_is_excluded_by_dockerignore() -> None:
    """A COPY and an ignore rule can disagree, and the ignore wins silently:
    the file is simply absent from the layer and the build still succeeds."""
    ignored = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    for name, directory in LOADED_AT_STARTUP.items():
        wanted = directory.relative_to(ROOT).as_posix()
        for rule in ignored:
            bare = rule.rstrip("/")
            assert bare != wanted, f"{wanted} is copied for {name} and ignored by {rule!r}"


def test_the_image_job_still_starts_the_container() -> None:
    """These checks are the fast gate, not the real one.

    They compare two files and cannot know whether the container runs. If the
    smoke test in CI is ever dropped, the assertions above become the only thing
    standing between a missing file and a crash loop -- which is roughly how the
    missing file got out in the first place.
    """
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "docker run" in workflow
    assert "/healthz" in workflow
