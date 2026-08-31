"""Reading-aloud routes.

Three things happen here and it is worth being explicit about which:

* the passage is shown, which needs nothing;
* the reading is timed, which needs only a clock in the page; and
* the reading is checked against the passage, which needs a speech model in the
  container.

Only the third is optional, and its absence is the default. A deployment with no
models still gets a working reading exercise -- it just reports a pace rather
than an accuracy, and says so.

The recording itself is posted as raw bytes, decoded in memory and discarded
with the request. It is never written to disk, never forwarded anywhere, and
never associated with a pupil, whether or not they are signed in.
"""

from __future__ import annotations

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from pensum.domain.grades import checkpoint_for
from pensum.reading.audio import AudioError, decode
from pensum.reading.fluency import measure, time_only, verdict
from pensum.reading.library import ReadingLibrary
from pensum.reading.schema import ReadingText
from pensum.reading.transcribe import Transcriber
from pensum.web.rendering import context, templates, validate_locale

router = APIRouter()

# A reading that claims to have taken longer than this was not a reading; the
# tab was left open. The clock is client-side, so it has to be sanity-checked.
MAX_SECONDS = 900.0


def _library(request: Request) -> ReadingLibrary:
    return request.app.state.reading


def _transcriber(request: Request) -> Transcriber | None:
    return request.app.state.transcriber


def _checkpoint(request: Request, subject_code: str, grade: int):
    subject = request.app.state.catalogue.subject(subject_code)
    if subject is None:
        raise HTTPException(status_code=404, detail="unknown subject")
    checkpoint = checkpoint_for(subject, grade)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="no goals for this grade")
    return subject, checkpoint


def _text_or_404(request: Request, goal_set: str, text_id: str) -> ReadingText:
    text = _library(request).text(goal_set, text_id)
    if text is None:
        raise HTTPException(status_code=404, detail="unknown reading text")
    return text


def _seconds(raw: float) -> float:
    """Trust the page's clock only within bounds. Negative is a bug or a lie."""
    if raw <= 0 or raw > MAX_SECONDS:
        raise HTTPException(status_code=400, detail="implausible reading time")
    return raw


@router.get("/{locale}/klasse/{grade}/{subject_code}/lesing", response_class=HTMLResponse)
async def reading_index(
    request: Request, locale: str, grade: int, subject_code: str
) -> HTMLResponse:
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)

    texts = _library(request).for_goal_set(checkpoint.goal_set.code)
    if not texts:
        raise HTTPException(status_code=404, detail="no reading texts for this checkpoint")

    return templates.TemplateResponse(
        request,
        "pages/reading_index.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            texts=texts,
            band=_library(request).band(subject.code, checkpoint.goal_set.after_year),
        ),
    )


@router.get("/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}", response_class=HTMLResponse)
async def reading_page(
    request: Request, locale: str, grade: int, subject_code: str, text_id: str
) -> HTMLResponse:
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)

    transcriber = _transcriber(request)
    return templates.TemplateResponse(
        request,
        "pages/reading.html",
        context(
            request,
            locale,
            grade=grade,
            subject=subject,
            checkpoint=checkpoint,
            text=text,
            # Whether this deployment can check the reading, not merely time it.
            # Drives which endpoint the page posts to, and which promises the
            # page is allowed to make.
            checked=transcriber is not None and transcriber.supports(text.language),
        ),
    )


@router.post(
    "/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}/opptak",
    response_class=HTMLResponse,
)
async def submit_recording(
    request: Request, locale: str, grade: int, subject_code: str, text_id: str
) -> HTMLResponse:
    """Check a reading against the passage.

    The body is raw 16 kHz mono PCM in a WAV container, produced by the page.
    It exists only for the duration of this function.
    """
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)

    transcriber = _transcriber(request)
    if transcriber is None or not transcriber.supports(text.language):
        # Not an error in the page's control: this deployment ships no model for
        # this language. 503 rather than 404 -- the route exists, the capability
        # does not.
        raise HTTPException(status_code=503, detail="speech checking is not available")

    try:
        recording = decode(await request.body())
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    heard = transcriber.transcribe(recording.pcm, text.language)
    # The recogniser's own word timings, when it has them, measure the reading
    # rather than the file: the seconds spent reaching for the stop button are
    # not reading time. `decode` gives the fallback.
    fluency = measure(text, heard.text, heard.seconds or recording.seconds)

    band = _library(request).band(subject.code, checkpoint.goal_set.after_year)
    return templates.TemplateResponse(
        request,
        "partials/reading_result.html",
        context(
            request,
            locale,
            subject=subject,
            text=text,
            fluency=fluency,
            checked=True,
            band=band,
            source=_library(request).norms.source(band.source) if band else None,
            verdict=verdict(fluency, band.low, band.high) if band else "unmeasured",
        ),
    )


@router.post(
    "/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}/tid",
    response_class=HTMLResponse,
)
async def submit_time(
    request: Request,
    locale: str,
    grade: int,
    subject_code: str,
    text_id: str,
    seconds: float = Form(...),
) -> HTMLResponse:
    """Time a reading without listening to it.

    What a deployment with no speech models gets. The number is only as good as
    the pupil's honesty about having read the whole passage, and the result page
    says so rather than implying a check that did not happen.
    """
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)

    timing = time_only(text, _seconds(seconds))
    band = _library(request).band(subject.code, checkpoint.goal_set.after_year)
    return templates.TemplateResponse(
        request,
        "partials/reading_result.html",
        context(
            request,
            locale,
            subject=subject,
            text=text,
            timing=timing,
            checked=False,
            band=band,
            source=_library(request).norms.source(band.source) if band else None,
        ),
    )
