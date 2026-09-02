"""Listening routes.

Two: show the round, mark the round. The speaking happens in the browser, so
there is no audio endpoint and nothing to stream -- which is the mirror image of
the reading exercise, where only the server can hear.

The words are in the page. They have to be: the browser is what says them, and
it cannot say a word it has not been given. So a pupil who opens the developer
tools can read the answer to a dictation, and that is accepted rather than
worked around. This is a study aid with no marks, no history and nothing to
gain by cheating -- the alternative is synthesising audio on the server, which
would mean shipping a voice for every language and would still be defeated by
turning the volume up and listening.

Nothing is stored, as with reading and writing.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from pensum.domain.grades import checkpoint_for
from pensum.listening.exercise import Round
from pensum.listening.library import ListeningLibrary
from pensum.listening.marking import MAX_STARS, Answers, mark
from pensum.web.deps import sees_unreviewed
from pensum.web.rendering import context, templates, validate_locale

router = APIRouter()


def _library(request: Request) -> ListeningLibrary:
    return request.app.state.listening


def _checkpoint(request: Request, subject_code: str, grade: int):
    subject = request.app.state.catalogue.subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail="unknown subject")
    checkpoint = checkpoint_for(subject, grade)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="no goals for this grade")
    return subject, checkpoint


def _round_or_404(request: Request, checkpoint) -> Round:
    built = _library(request).round_for(
        checkpoint.goal_set.code,
        checkpoint.goal_set.after_year,
        unreviewed=sees_unreviewed(request),
    )
    if built is None:
        raise HTTPException(status_code=404, detail="no listening exercise for this checkpoint")
    return built


@router.get("/{locale}/klasse/{grade}/{subject_code}/lytting", response_class=HTMLResponse)
async def listening_page(
    request: Request, locale: str, grade: int, subject_code: str
) -> HTMLResponse:
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    built = _round_or_404(request, checkpoint)

    return templates.TemplateResponse(
        request,
        "pages/listening.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            round=built,
        ),
    )


@router.post("/{locale}/klasse/{grade}/{subject_code}/lytting/svar", response_class=HTMLResponse)
async def submit_answers(
    request: Request,
    locale: str,
    grade: int,
    subject_code: str,
    answers: Answers,
) -> HTMLResponse:
    """Mark a finished round.

    The round is rebuilt here rather than posted back. It is derived
    deterministically from the checkpoint, so rebuilding it is cheap and cached
    -- and it means the questions being marked are the questions the server set,
    not the ones the request claims it was asked.
    """
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    built = _round_or_404(request, checkpoint)

    return templates.TemplateResponse(
        request,
        "partials/listening_result.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            round=built,
            result=mark(built, answers),
            max_stars=MAX_STARS,
        ),
    )
