"""Assertions about the committed catalogue, not about fixtures.

These are the tests that fail when Udir revises a curriculum and someone commits
the re-ingest without reading the diff. They run offline against `data/`.
"""

from __future__ import annotations

import json

import pytest

from froken.catalogue.loader import DEFAULT_DATA_DIR, Catalogue
from froken.domain.grades import checkpoint_for
from froken.domain.models import BOKMAAL, NYNORSK

# The subjects quiz content is authored for. Ingest covers every grunnskole
# subject; these six are the ones the app promises to test.
V1_SUBJECTS = ["MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04"]


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return Catalogue.load()


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((DEFAULT_DATA_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("code", V1_SUBJECTS)
def test_v1_subject_is_present(catalogue: Catalogue, code: str) -> None:
    assert catalogue.subject(code) is not None


@pytest.mark.parametrize("code", V1_SUBJECTS)
def test_v1_subject_covers_every_grunnskole_grade(catalogue: Catalogue, code: str) -> None:
    """A pupil in any klasse 1-10 must find something in every core subject."""
    subject = catalogue.subject(code)
    missing = [grade for grade in range(1, 11) if checkpoint_for(subject, grade) is None]
    assert not missing, f"{code} has no checkpoint for grades {missing}"


def test_no_subject_is_superseded_by_another_in_the_catalogue(catalogue: Catalogue) -> None:
    """Two generations of one subject would give a grade competing goal sets.

    Date filtering alone does not catch this: NOR01-07 carries no `gyldig-til`
    and so reads as in force forever, even though NOR01-08 replaces it.
    """
    present = {s.code for s in catalogue.subjects}
    overlapping = {
        s.code: [c for c in s.replaced_by if c in present]
        for s in catalogue.subjects
        if any(c in present for c in s.replaced_by)
    }
    assert not overlapping, f"superseded curricula still present: {overlapping}"


@pytest.mark.parametrize("code", V1_SUBJECTS)
def test_v1_goals_have_bokmaal_text(catalogue: Catalogue, code: str) -> None:
    """The subjects we author quizzes for must be readable in bokmål verbatim."""
    subject = catalogue.subject(code)
    missing = [
        goal.code
        for goal_set in subject.goal_sets
        for goal in goal_set.goals
        if not goal.text.has(BOKMAAL)
    ]
    assert not missing, f"{code}: {len(missing)} goals lack bokmål, e.g. {missing[:5]}"


def test_every_goal_has_some_norwegian_text(catalogue: Catalogue) -> None:
    """Beyond the v1 six, bokmål is not guaranteed -- but Norwegian is.

    Three valgfag (LKA01-02, RSL01-02, TPR01-02) are fastsatt in nynorsk with no
    bokmål translation, mirroring the maalform caveat that already applies to 1T
    and samfunnskunnskap in VGS. The fallback chain resolves them to nynorsk
    rather than to English, which is the correct behaviour, not a gap to paper
    over -- so this asserts what is actually true.
    """
    missing = [
        goal.code
        for subject in catalogue.subjects
        for goal_set in subject.goal_sets
        for goal in goal_set.goals
        if not (goal.text.has(BOKMAAL) or goal.text.has(NYNORSK))
    ]
    assert not missing, f"{len(missing)} goals have no Norwegian text, e.g. {missing[:5]}"


def test_goal_codes_are_globally_unique(catalogue: Catalogue) -> None:
    """Quiz items key off goal codes, so a collision would mis-attribute questions."""
    seen: dict[str, str] = {}
    for subject in catalogue.subjects:
        for goal_set in subject.goal_sets:
            for goal in goal_set.goals:
                assert goal.code not in seen, (
                    f"{goal.code} in both {seen.get(goal.code)} and {subject.code}"
                )
                seen[goal.code] = subject.code


def test_no_goal_set_is_empty(catalogue: Catalogue) -> None:
    empty = [
        f"{s.code}/{gs.code}" for s in catalogue.subjects for gs in s.goal_sets if not gs.goals
    ]
    assert not empty, f"empty goal sets: {empty}"


def test_manifest_matches_the_committed_subjects(catalogue: Catalogue, manifest: dict) -> None:
    assert set(manifest["subjects"]) == {s.code for s in catalogue.subjects}


def test_manifest_records_a_validity_start_for_every_subject(manifest: dict) -> None:
    """A missing `valid_from` means the ingest silently lost the date field."""
    undated = [code for code, meta in manifest["subjects"].items() if not meta["valid_from"]]
    assert not undated, f"subjects with no valid_from: {undated}"
