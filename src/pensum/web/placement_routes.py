"""Nivåtest routes -- the adaptive test that looks for a pupil's ceiling.

Deliberately a separate router from `quiz_routes`, because the two answer
different questions and should be able to diverge. What they must *not* diverge
on is the question loop itself, so both render `partials/question.html` and
`partials/feedback.html` through `rendering.flow`.

One asymmetry is visible in the URLs and is intentional: a trinntest is started
from a grade and a subject, because the pupil already knows which checkpoint
they mean. A nivåtest is started from a subject alone -- picking the checkpoint
is the thing it is for -- and the klasse it asks for is a hint about where to
begin, never a claim about where they will end up.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from pensum.domain.grades import FIRST_GRADE, LAST_GRADE
from pensum.domain.ladder import Ladder
from pensum.i18n import translate
from pensum.items.loader import ItemBank
from pensum.items.schema import QuizItem
from pensum.quiz.run import PlacementRun
from pensum.quiz.scoring import select
from pensum.quiz.session import SessionStore
from pensum.web.deps import current_user
from pensum.web.rendering import context, flow, templates, validate_locale

router = APIRouter()

# At or below this many questions on a goal, the gap list is a hint rather than
# a finding, and says so.
THIN_EVIDENCE = 2


def _bank(request: Request) -> ItemBank:
    return request.app.state.items


def _store(request: Request) -> SessionStore:
    return request.app.state.sessions


def _ladder(request: Request, subject_code: str) -> tuple[object, Ladder]:
    subject = request.app.state.catalogue.subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail="unknown subject")
    bank = _bank(request)
    servable = {gs.code for gs in subject.goal_sets if bank.has_quiz(gs.code)}
    ladder = Ladder.build(subject, servable)
    if len(ladder) < 2:
        # One rung is not a ladder: there is nothing to place between. The
        # subject page still offers its trinntest, which is the honest option.
        raise HTTPException(status_code=404, detail="subject has too few checkpoints to place on")
    return subject, ladder


def _drawer(request: Request):
    """Draw items for a rung, never re-serving one this run already asked."""
    bank = _bank(request)

    def draw(goal_set: str, count: int, exclude: set[str]) -> list[QuizItem]:
        pool = [item for item in bank.for_goal_set(goal_set) if item.id not in exclude]
        return select(pool, count)

    return draw


def _run_or_404(request: Request, run_id: str) -> PlacementRun:
    run = _store(request).get(run_id, datetime.now(UTC))
    if not isinstance(run, PlacementRun):
        # Expired, unknown, or a trinntest id on a nivåtest path. All ordinary --
        # a tab left open overnight is the common case.
        raise HTTPException(status_code=404, detail="nivåtest not found")
    return run


def _flow(request: Request, locale: str, run: PlacementRun) -> dict[str, object]:
    """A nivåtest cannot say "3 of 10" -- it has not decided how long it is.

    Showing a total would mean inventing one, and the number would move as the
    search changed its mind. `asked` is what is actually known.
    """
    return flow(
        locale,
        "nivatest",
        run.id,
        progress=translate(locale, "placement.progress", number=run.asked + 1),
        finished=run.finished,
    )


@router.get("/{locale}/nivatest/{subject_code}", response_class=HTMLResponse)
async def start_page(request: Request, locale: str, subject_code: str) -> HTMLResponse:
    validate_locale(locale)
    subject, ladder = _ladder(request, subject_code)
    return templates.TemplateResponse(
        request,
        "pages/placement_start.html",
        context(
            request,
            locale,
            subject=subject,
            ladder=ladder,
            grades=range(FIRST_GRADE, LAST_GRADE + 1),
        ),
    )


@router.post("/{locale}/nivatest/{subject_code}")
async def start(
    request: Request, locale: str, subject_code: str, grade: str = Form("")
) -> RedirectResponse:
    validate_locale(locale)
    subject, ladder = _ladder(request, subject_code)

    # "Jeg vet ikke" posts an empty string, and that is a real answer: it starts
    # the climb at the bottom rather than guessing on the pupil's behalf.
    chosen: int | None = None
    if grade.strip():
        try:
            value = int(grade)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="grade must be a number") from exc
        if not FIRST_GRADE <= value <= LAST_GRADE:
            raise HTTPException(status_code=400, detail="grade outside grunnskole")
        chosen = value

    now = datetime.now(UTC)
    run = PlacementRun.begin(
        subject=subject.code,
        ladder=ladder,
        grade=chosen,
        draw=_drawer(request),
        now=now,
        user=current_user(request),
    )
    if not run.blocks:
        raise HTTPException(status_code=404, detail="no items available for this subject")

    _store(request).put(run, now)
    return RedirectResponse(f"/{locale}/nivatest/run/{run.id}", status_code=303)


@router.get("/{locale}/nivatest/run/{run_id}", response_class=HTMLResponse)
async def run_page(request: Request, locale: str, run_id: str) -> HTMLResponse:
    validate_locale(locale)
    run = _run_or_404(request, run_id)
    subject = request.app.state.catalogue.subject(run.subject)
    return templates.TemplateResponse(
        request,
        "pages/placement.html",
        context(
            request,
            locale,
            run=run,
            subject=subject,
            item=run.current(),
            **_flow(request, locale, run),
        ),
    )


@router.post("/{locale}/nivatest/run/{run_id}/answer", response_class=HTMLResponse)
async def answer(
    request: Request,
    locale: str,
    run_id: str,
    item_id: str = Form(...),
    response: str = Form(""),
) -> HTMLResponse:
    validate_locale(locale)
    run = _run_or_404(request, run_id)

    item = run.answer(item_id, response, _drawer(request))
    if item is None:
        raise HTTPException(status_code=409, detail="that question was already answered")

    return templates.TemplateResponse(
        request,
        "partials/feedback.html",
        context(
            request,
            locale,
            run=run,
            item=item,
            given=response,
            correct=item.is_correct(response),
            **_flow(request, locale, run),
        ),
    )


@router.get("/{locale}/nivatest/run/{run_id}/question", response_class=HTMLResponse)
async def next_question(request: Request, locale: str, run_id: str) -> HTMLResponse:
    validate_locale(locale)
    run = _run_or_404(request, run_id)
    return templates.TemplateResponse(
        request,
        "partials/question.html",
        context(
            request,
            locale,
            run=run,
            item=run.current(),
            **_flow(request, locale, run),
        ),
    )


@router.get("/{locale}/nivatest/run/{run_id}/result", response_class=HTMLResponse)
async def result(request: Request, locale: str, run_id: str) -> HTMLResponse:
    validate_locale(locale)
    run = _run_or_404(request, run_id)
    subject = request.app.state.catalogue.subject(run.subject)
    outcome = run.outcome()
    bank = _bank(request)

    # The gap list is the actionable half, and it only reads as actionable with
    # the goal text rather than a KM code. Goals are looked up across every rung
    # the run touched, not just the frontier.
    goals = {
        goal.code: goal
        for rung in run.ladder.rungs
        for goal in rung.goal_set.goals
    }
    tally = run.tally()
    gap_codes = [code for code in run.gaps() if code in goals]

    # A nivåtest spreads a handful of questions over many goals, so a listed gap
    # often rests on a single answer. That is enough to point at something worth
    # looking again at, and nowhere near enough to conclude the pupil cannot do
    # it -- and eight goals each shown as "0 av 1" overstates badly by sheer
    # volume unless the page says which of the two it means.
    thin_evidence = bool(gap_codes) and all(tally[code][1] <= THIN_EVIDENCE for code in gap_codes)

    return templates.TemplateResponse(
        request,
        "pages/placement_result.html",
        context(
            request,
            locale,
            run=run,
            subject=subject,
            outcome=outcome,
            goals=goals,
            tally=tally,
            gaps=[goals[code] for code in gap_codes],
            thin_evidence=thin_evidence,
            # So a ceiling is read against what its quiz actually reaches. In
            # norsk that is around a third of the checkpoint, and a page that
            # said "mestrer 7. trinn" without it would overstate badly.
            coverage=bank.coverage(outcome.ceiling.goal_set) if outcome.ceiling else None,
            frontier_coverage=(
                bank.coverage(outcome.frontier.goal_set) if outcome.frontier else None
            ),
        ),
    )
