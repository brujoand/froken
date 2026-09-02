"""Handwriting routes.

Three of them, and only the last does any work: show the prompts, show one
prompt, mark what was traced. There is no live endpoint and no session -- the
page draws its own ink and fills its own meter, and the server hears about it
once, when the pupil is done.

That is a deliberate difference from the reading exercise, which streams audio
because only the server can transcribe it. Here the browser already knows where
the finger went, so asking it to post every point as it moves would spend a
request per stroke to learn nothing the page could not already draw.

Nothing is stored. The points arrive in a request body, are marked in memory,
and are gone when the response is sent -- not written to disk, not attached to a
pupil, whether or not anyone is signed in.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from pensum.domain.grades import checkpoint_for
from pensum.web.deps import sees_unreviewed
from pensum.web.rendering import context, templates, validate_locale
from pensum.writing import rewards
from pensum.writing.attempt import Attempt
from pensum.writing.library import WritingLibrary
from pensum.writing.paths import sample
from pensum.writing.schema import WritingPrompt
from pensum.writing.tracing import mark

router = APIRouter()


def _library(request: Request) -> WritingLibrary:
    return request.app.state.writing


def _checkpoint(request: Request, subject_code: str, grade: int):
    subject = request.app.state.catalogue.subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail="unknown subject")
    checkpoint = checkpoint_for(subject, grade)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="no goals for this grade")
    return subject, checkpoint


def _prompt_or_404(request: Request, goal_set: str, prompt_id: str) -> WritingPrompt:
    prompt = _library(request).prompt(goal_set, prompt_id, unreviewed=sees_unreviewed(request))
    if prompt is None:
        raise HTTPException(status_code=404, detail="unknown writing prompt")
    return prompt


# How far a stroke number moves when it would otherwise be printed on top of
# another, and how close two of them may be before that happens.
LABEL_STEP = 12.0
LABEL_CLEARANCE = 9.0

# Tried in order. Sideways first: a number beside the dot reads as belonging to
# it, where one above or below can look like it belongs to the line instead.
_LABEL_OFFSETS = (
    (0.0, 0.0),
    (LABEL_STEP, 0.0),
    (-LABEL_STEP, 0.0),
    (0.0, LABEL_STEP),
    (0.0, -LABEL_STEP),
    (LABEL_STEP, LABEL_STEP),
    (-LABEL_STEP, -LABEL_STEP),
)


def label_positions(starts: list[tuple[float, float]], metrics) -> list[tuple[float, float]]:
    """Where to print each stroke's number.

    Strokes legitimately begin in the same place -- the bowl of a `B` starts
    where its stem starts, and that is how a `B` is written -- so the dots are
    left where they belong and only the numbers move. Printed on top of each
    other they would read as one illegible mark, which is the one thing on this
    page a child actually has to read.
    """
    placed: list[tuple[float, float]] = []
    for x, y in starts:
        for dx, dy in _LABEL_OFFSETS:
            candidate = (x + dx, y + dy)
            if not (0 <= candidate[0] <= metrics.width and 0 <= candidate[1] <= metrics.height):
                continue
            if all(
                (candidate[0] - other[0]) ** 2 + (candidate[1] - other[1]) ** 2
                >= LABEL_CLEARANCE**2
                for other in placed
            ):
                placed.append(candidate)
                break
        else:
            # Nowhere clear inside the box. Better overlapping than outside it.
            placed.append((x, y))
    return placed


def _glyphs(request: Request, prompt: WritingPrompt) -> list[dict[str, object]]:
    """The letterforms for a prompt, in the order they are written.

    Each stroke carries where it starts as well as its path, because the page
    prints a numbered dot there: "begin here, and go this way" is the whole
    lesson, and it has to be visible before a finger touches the screen.

    The library refuses to serve a prompt whose characters it cannot draw, so
    every character has a glyph by the time this runs.
    """
    alphabet = _library(request).alphabet
    out: list[dict[str, object]] = []
    for char in prompt.characters:
        glyph = alphabet.glyph(char)
        starts = [sample(path)[0] for path in glyph.strokes]
        labels = label_positions(starts, alphabet.metrics)
        strokes = [
            {
                "d": path,
                "x": start[0],
                "y": start[1],
                "label_x": label[0],
                "label_y": label[1],
            }
            for path, start, label in zip(glyph.strokes, starts, labels, strict=True)
        ]
        out.append({"char": char, "strokes": strokes})
    return out


@router.get("/{locale}/klasse/{grade}/{subject_code}/skriving", response_class=HTMLResponse)
async def writing_index(
    request: Request, locale: str, grade: int, subject_code: str
) -> HTMLResponse:
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)

    prompts = _library(request).for_goal_set(
        checkpoint.goal_set.code, unreviewed=sees_unreviewed(request)
    )
    if not prompts:
        raise HTTPException(status_code=404, detail="no writing prompts for this checkpoint")

    return templates.TemplateResponse(
        request,
        "pages/writing_index.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            prompts=prompts,
        ),
    )


@router.get(
    "/{locale}/klasse/{grade}/{subject_code}/skriving/{prompt_id}", response_class=HTMLResponse
)
async def writing_page(
    request: Request, locale: str, grade: int, subject_code: str, prompt_id: str
) -> HTMLResponse:
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    prompt = _prompt_or_404(request, checkpoint.goal_set.code, prompt_id)

    return templates.TemplateResponse(
        request,
        "pages/writing.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            prompt=prompt,
            metrics=_library(request).alphabet.metrics,
            # The letterforms themselves, so the page can draw the guide and
            # animate the demonstration without a second request. A prompt is at
            # most a dozen characters, so this is a few kilobytes of paths.
            glyphs=_glyphs(request, prompt),
        ),
    )


@router.post(
    "/{locale}/klasse/{grade}/{subject_code}/skriving/{prompt_id}/spor",
    response_class=HTMLResponse,
)
def submit_tracing(
    request: Request,
    locale: str,
    grade: int,
    subject_code: str,
    prompt_id: str,
    attempt: Attempt,
) -> HTMLResponse:
    """Mark a finished prompt.

    The whole prompt at once rather than a letter at a time: a child who gives
    up halfway has not failed the letters they did write, and marking per letter
    would either have to invent a score for the rest or leave a half-finished
    result on the screen.

    Deliberately not `async`, unlike every other route here. Marking is
    arithmetic rather than waiting, it costs milliseconds for a real tracing and
    seconds for the nastiest body the schema will accept, and inside an async
    handler those seconds would stop *every* request this process is serving --
    a quiz on another tab included. Declared sync, Starlette runs it in a
    worker thread, so a hostile body costs one thread rather than the site.
    """
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    prompt = _prompt_or_404(request, checkpoint.goal_set.code, prompt_id)

    marked = mark(prompt, _library(request).alphabet, attempt)

    return templates.TemplateResponse(
        request,
        "partials/writing_result.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            prompt=prompt,
            mark=marked,
            rewards=rewards.earned(marked),
            max_stars=rewards.MAX_STARS,
        ),
    )
