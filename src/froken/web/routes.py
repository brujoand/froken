"""Page routes.

Server-rendered throughout. HTMX handles the quiz interactions in a later
milestone; browsing the catalogue needs no JavaScript at all, which keeps the
site usable on a school laptop with anything blocked.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from froken.catalogue.loader import Catalogue
from froken.domain.grades import FIRST_GRADE, LAST_GRADE, checkpoint_for, subjects_for_grade
from froken.domain.models import NYNORSK
from froken.i18n import DEFAULT_LOCALE, curriculum_language
from froken.items.loader import ItemBank
from froken.web.rendering import context, templates, validate_locale

router = APIRouter()

# Which subjects Frøken presents. An editorial choice, not a derived fact: the
# ingest vendors all 50 grunnskole curricula, including valgfag and the samisk
# parallels, and listing every one of them would bury the core subjects a pupil
# actually wants.
#
# These codes carry a curriculum revision, so they go stale the moment Udir
# publishes a new one -- and a stale code here empties the grade pages silently.
# `test_core_subjects_all_exist` turns that into a CI failure instead.
CORE_SUBJECTS = ("MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04")


def _catalogue(request: Request) -> Catalogue:
    return request.app.state.catalogue


def _items(request: Request) -> ItemBank:
    return request.app.state.items


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Norwegian is the default; an unprefixed path is not a neutral one."""
    return RedirectResponse(f"/{DEFAULT_LOCALE}/", status_code=307)


@router.get("/{locale}/", response_class=HTMLResponse)
async def home(request: Request, locale: str) -> HTMLResponse:
    validate_locale(locale)
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        context(request, locale, grades=range(FIRST_GRADE, LAST_GRADE + 1)),
    )


@router.get("/{locale}/klasse/{grade}", response_class=HTMLResponse)
async def grade_page(request: Request, locale: str, grade: int) -> HTMLResponse:
    validate_locale(locale)
    if not FIRST_GRADE <= grade <= LAST_GRADE:
        raise HTTPException(status_code=404, detail="grade outside grunnskole")

    catalogue = _catalogue(request)
    subjects = [s for s in subjects_for_grade(catalogue.subjects, grade) if s.code in CORE_SUBJECTS]

    entries = []
    for subject in subjects:
        checkpoint = checkpoint_for(subject, grade)
        entries.append(
            {
                "subject": subject,
                "checkpoint": checkpoint,
                "goal_count": len(checkpoint.goal_set.goals),
                # Derived, never declared: a subject offers a quiz exactly when
                # reviewed items exist for that checkpoint.
                "has_quiz": _items(request).has_quiz(checkpoint.goal_set.code),
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/grade.html",
        context(request, locale, grade=grade, entries=entries),
    )


@router.get("/{locale}/klasse/{grade}/{subject_code}", response_class=HTMLResponse)
async def subject_page(
    request: Request, locale: str, grade: int, subject_code: str
) -> HTMLResponse:
    validate_locale(locale)
    subject = _catalogue(request).subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail="unknown subject")

    checkpoint = checkpoint_for(subject, grade)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="subject has no goals for this grade")

    # A handful of curricula are established in nynorsk with no bokmål
    # translation. Showing them unlabelled would read as a typo rather than as
    # the official wording it is.
    language = curriculum_language(locale)
    shows_nynorsk = language != NYNORSK and all(
        not goal.text.has(language) and goal.text.has(NYNORSK) for goal in checkpoint.goal_set.goals
    )

    return templates.TemplateResponse(
        request,
        "pages/subject.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            shows_nynorsk=shows_nynorsk,
            has_quiz=_items(request).has_quiz(checkpoint.goal_set.code),
            question_count=len(_items(request).for_goal_set(checkpoint.goal_set.code)),
        ),
    )
