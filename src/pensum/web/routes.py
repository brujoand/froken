"""Page routes.

Server-rendered throughout. HTMX handles the quiz interactions in a later
milestone; browsing the catalogue needs no JavaScript at all, which keeps the
site usable on a school laptop with anything blocked.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pensum import __version__
from pensum.catalogue.loader import Catalogue
from pensum.domain.grades import FIRST_GRADE, LAST_GRADE, checkpoint_for, subjects_for_grade
from pensum.domain.models import NYNORSK
from pensum.i18n import DEFAULT_LOCALE, curriculum_language
from pensum.items.loader import ItemBank
from pensum.reading.library import ReadingLibrary
from pensum.web.deps import sees_unreviewed
from pensum.web.rendering import context, templates, validate_locale
from pensum.writing.library import WritingLibrary

router = APIRouter()

# Which subjects Pensum presents. An editorial choice, not a derived fact: the
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


def _reading(request: Request) -> ReadingLibrary:
    return request.app.state.reading


def _writing(request: Request) -> WritingLibrary:
    return request.app.state.writing


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    # The version comes back too, so what is deployed can be checked without
    # inspecting the image. "dev" means it was built outside the release
    # pipeline.
    return {"status": "ok", "version": __version__}


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


@router.get("/{locale}/rettigheter", response_class=HTMLResponse)
async def rights_page(request: Request, locale: str) -> HTMLResponse:
    """Who owns what here, and where to write if we have got it wrong.

    Linked from the footer of every page. Everything Pensum serves is either
    Udir's under NLOD or written for Pensum, so the address on this page should
    never receive anything -- which is the reason to publish it rather than a
    reason not to.
    """
    validate_locale(locale)
    return templates.TemplateResponse(request, "pages/rights.html", context(request, locale))


@router.get("/{locale}/klasse/{grade}", response_class=HTMLResponse)
async def grade_page(request: Request, locale: str, grade: int) -> HTMLResponse:
    validate_locale(locale)
    if not FIRST_GRADE <= grade <= LAST_GRADE:
        raise HTTPException(status_code=404, detail="grade outside grunnskole")

    catalogue = _catalogue(request)
    drafts = sees_unreviewed(request)
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
                "has_quiz": _items(request).has_quiz(checkpoint.goal_set.code, unreviewed=drafts),
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
    drafts = sees_unreviewed(request)
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
            has_quiz=_items(request).has_quiz(checkpoint.goal_set.code, unreviewed=drafts),
            # Derived the same way the quiz is: a subject offers reading aloud
            # exactly when reviewed passages exist for that checkpoint. Norsk and
            # engelsk have them; the other subjects simply have none, which needs
            # no list of which subjects are "reading subjects".
            has_reading=_reading(request).has_reading(checkpoint.goal_set.code, unreviewed=drafts),
            # And the same again for handwriting. Whether the device can
            # actually be written on is decided in the browser, so the link
            # appears wherever prompts exist and the page itself says what it
            # needs.
            has_writing=_writing(request).has_writing(checkpoint.goal_set.code, unreviewed=drafts),
            question_count=len(
                _items(request).for_goal_set(checkpoint.goal_set.code, unreviewed=drafts)
            ),
            coverage=_items(request).coverage(checkpoint.goal_set, unreviewed=drafts),
        ),
    )
