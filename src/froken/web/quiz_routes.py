"""Quiz routes.

HTMX drives the question loop: each answer swaps in feedback, and the next
question replaces it. No page reloads, no JavaScript we wrote, and the whole
flow still works if the fragments are fetched as ordinary pages.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from froken.domain.grades import checkpoint_for
from froken.items.loader import ItemBank
from froken.quiz.scoring import Result, score, select
from froken.quiz.session import QuizSession, SessionStore
from froken.scores.store import Attempt, GoalTally, attempt_key
from froken.web.deps import current_user, get_store
from froken.web.rendering import context, templates, validate_locale

router = APIRouter()


def _bank(request: Request) -> ItemBank:
    return request.app.state.items


def _store(request: Request) -> SessionStore:
    return request.app.state.sessions


def _session_or_404(request: Request, session_id: str):
    session = _store(request).get(session_id, datetime.now(UTC))
    if session is None:
        # Expired or unknown. Both are ordinary -- a pupil leaving a tab open
        # overnight is the common case, not an error worth alarming them about.
        raise HTTPException(status_code=404, detail="quiz session not found")
    return session


def _remember(request: Request, session: QuizSession, outcome: Result, now: datetime) -> None:
    """Record a finished attempt, if there is anywhere and anyone to record.

    Three conditions, all of which must hold, and none of which is the default:
    a store is configured, the pupil was signed in when they started, and they
    actually finished. An abandoned quiz is not a result and is not kept.
    """
    store = get_store(request)
    if store is None or not session.attributed or not session.finished:
        return

    store.record(
        Attempt(
            key=attempt_key(session.id),
            user_sub=str(session.user_sub),
            user_name=session.user_name or str(session.user_sub),
            subject=session.subject,
            goal_set=session.goal_set,
            grade=session.grade,
            correct=outcome.correct,
            total=outcome.total,
            by_goal=tuple(
                GoalTally(goal=g.goal, correct=g.correct, total=g.total) for g in outcome.by_goal
            ),
            finished_at=now,
        )
    )


@router.post("/{locale}/klasse/{grade}/{subject_code}/quiz")
async def start_quiz(
    request: Request, locale: str, grade: int, subject_code: str
) -> RedirectResponse:
    validate_locale(locale)
    subject = request.app.state.catalogue.subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail="unknown subject")

    checkpoint = checkpoint_for(subject, grade)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="no goals for this grade")

    items = select(_bank(request).for_goal_set(checkpoint.goal_set.code))
    if not items:
        raise HTTPException(status_code=404, detail="no quiz available for this checkpoint")

    session = _store(request).create(
        subject=subject.code,
        goal_set=checkpoint.goal_set.code,
        grade=grade,
        items=items,
        now=datetime.now(UTC),
        user=current_user(request),
    )
    return RedirectResponse(f"/{locale}/quiz/{session.id}", status_code=303)


@router.get("/{locale}/quiz/{session_id}", response_class=HTMLResponse)
async def quiz_page(request: Request, locale: str, session_id: str) -> HTMLResponse:
    validate_locale(locale)
    session = _session_or_404(request, session_id)
    subject = request.app.state.catalogue.subject(session.subject)

    return templates.TemplateResponse(
        request,
        "pages/quiz.html",
        context(request, locale, session=session, subject=subject, item=session.current()),
    )


@router.post("/{locale}/quiz/{session_id}/answer", response_class=HTMLResponse)
async def answer(
    request: Request,
    locale: str,
    session_id: str,
    item_id: str = Form(...),
    response: str = Form(""),
) -> HTMLResponse:
    validate_locale(locale)
    session = _session_or_404(request, session_id)

    item = session.answer(item_id, response)
    if item is None:
        raise HTTPException(status_code=409, detail="that question was already answered")

    return templates.TemplateResponse(
        request,
        "partials/feedback.html",
        context(
            request,
            locale,
            session=session,
            item=item,
            given=response,
            correct=item.is_correct(response),
        ),
    )


@router.get("/{locale}/quiz/{session_id}/question", response_class=HTMLResponse)
async def next_question(request: Request, locale: str, session_id: str) -> HTMLResponse:
    validate_locale(locale)
    session = _session_or_404(request, session_id)

    return templates.TemplateResponse(
        request,
        "partials/question.html",
        context(request, locale, session=session, item=session.current()),
    )


@router.get("/{locale}/quiz/{session_id}/result", response_class=HTMLResponse)
async def result(request: Request, locale: str, session_id: str) -> HTMLResponse:
    validate_locale(locale)
    session = _session_or_404(request, session_id)
    subject = request.app.state.catalogue.subject(session.subject)
    goal_set = subject.goal_set(session.goal_set)

    outcome = score(session)
    _remember(request, session, outcome, datetime.now(UTC))

    # The per-goal breakdown is the useful half of the result, and it only reads
    # as useful if it shows the goal text rather than a KM code.
    goals = {goal.code: goal for goal in goal_set.goals}

    return templates.TemplateResponse(
        request,
        "pages/result.html",
        context(
            request,
            locale,
            session=session,
            subject=subject,
            result=outcome,
            goals=goals,
            # So a score reads against what the quiz actually reached, not the
            # whole checkpoint.
            coverage=_bank(request).coverage(goal_set),
        ),
    )
