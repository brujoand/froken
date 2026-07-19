"""Page routes.

Server-rendered throughout. HTMX handles the quiz interactions in a later
milestone; browsing the catalogue needs no JavaScript at all, which keeps the
site usable on a school laptop with anything blocked.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from froken.catalogue.loader import Catalogue
from froken.domain.grades import FIRST_GRADE, LAST_GRADE, checkpoint_for, subjects_for_grade
from froken.domain.models import NYNORSK
from froken.i18n import DEFAULT_LOCALE, SUPPORTED_LOCALES, curriculum_language, translate

TEMPLATES_DIR = Path(__file__).parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)
router = APIRouter()

# The subjects quiz content is authored for. Everything else is browsable but
# shows no quiz call to action -- promising a quiz we cannot deliver is worse
# than saying nothing.
QUIZZABLE = frozenset({"MAT01-06", "NOR01-08", "ENG01-06", "NAT01-05", "SAF01-05", "RLE01-04"})


def _catalogue(request: Request) -> Catalogue:
    return request.app.state.catalogue


def _context(request: Request, locale: str, **extra: object) -> dict[str, object]:
    """Shared template context.

    `t` and `lang` are threaded into every template so no page can accidentally
    render one locale's chrome around another's content.
    """
    return {
        "request": request,
        "locale": locale,
        "lang": curriculum_language(locale),
        "t": lambda key, **kwargs: translate(locale, key, **kwargs),
        "locales": SUPPORTED_LOCALES,
        **extra,
    }


def _validate_locale(locale: str) -> None:
    if locale not in SUPPORTED_LOCALES:
        raise HTTPException(status_code=404, detail="unknown locale")


@router.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Norwegian is the default; an unprefixed path is not a neutral one."""
    return RedirectResponse(f"/{DEFAULT_LOCALE}/", status_code=307)


@router.get("/{locale}/", response_class=HTMLResponse)
async def home(request: Request, locale: str) -> HTMLResponse:
    _validate_locale(locale)
    return templates.TemplateResponse(
        request,
        "pages/home.html",
        _context(request, locale, grades=range(FIRST_GRADE, LAST_GRADE + 1)),
    )


@router.get("/{locale}/klasse/{grade}", response_class=HTMLResponse)
async def grade_page(request: Request, locale: str, grade: int) -> HTMLResponse:
    _validate_locale(locale)
    if not FIRST_GRADE <= grade <= LAST_GRADE:
        raise HTTPException(status_code=404, detail="grade outside grunnskole")

    catalogue = _catalogue(request)
    subjects = [s for s in subjects_for_grade(catalogue.subjects, grade) if s.code in QUIZZABLE]

    entries = []
    for subject in subjects:
        checkpoint = checkpoint_for(subject, grade)
        entries.append(
            {
                "subject": subject,
                "checkpoint": checkpoint,
                "goal_count": len(checkpoint.goal_set.goals),
            }
        )

    return templates.TemplateResponse(
        request,
        "pages/grade.html",
        _context(request, locale, grade=grade, entries=entries),
    )


@router.get("/{locale}/klasse/{grade}/{subject_code}", response_class=HTMLResponse)
async def subject_page(
    request: Request, locale: str, grade: int, subject_code: str
) -> HTMLResponse:
    _validate_locale(locale)
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
        _context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            shows_nynorsk=shows_nynorsk,
            quizzable=subject.code in QUIZZABLE,
        ),
    )
