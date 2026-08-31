"""Reading-aloud routes.

Four things happen here and it is worth being explicit about which:

* the passage is shown, which needs nothing;
* the reading is timed, which needs only a clock in the page;
* the words light up as they are read, which needs the audio to arrive in
  pieces and a speech model to transcribe each piece; and
* the reading is checked against the passage, which needs the same model over
  the whole recording.

Only the last two are optional, and their absence is the default. A deployment
with no models still gets a working reading exercise -- it reports a pace rather
than an accuracy, replays the passage at an even pace rather than at the times
the words were actually read, and says so on the page.

The recording is decoded in memory and discarded with the reading. It is never
written to disk, never forwarded anywhere, and never associated with a pupil,
whether or not they are signed in.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from pensum.domain.grades import checkpoint_for
from pensum.reading import rewards
from pensum.reading.audio import MAX_BYTES, SAMPLE_RATE, SAMPLE_WIDTH, AudioError, decode
from pensum.reading.fluency import advance, even_replay, measure, replay, time_only, verdict
from pensum.reading.library import ReadingLibrary
from pensum.reading.schema import ReadingText
from pensum.reading.streams import ReadingStream, StreamLimit, StreamStore
from pensum.reading.transcribe import Transcriber
from pensum.web.deps import get_settings
from pensum.web.rendering import context, templates, validate_locale

router = APIRouter()

# A reading that claims to have taken longer than this was not a reading; the
# tab was left open. The clock is client-side, so it has to be sanity-checked.
MAX_SECONDS = 900.0

# How much recent audio the live pass looks at. Long enough to carry a phrase,
# short enough that the cost of a pass does not grow with the length of the
# reading.
LIVE_WINDOW_SECONDS = 8.0


def _library(request: Request) -> ReadingLibrary:
    return request.app.state.reading


def _transcriber(request: Request) -> Transcriber | None:
    return request.app.state.transcriber


def _streams(request: Request) -> StreamStore:
    return request.app.state.streams


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


def _can_check(request: Request, language: str) -> bool:
    transcriber = _transcriber(request)
    return transcriber is not None and transcriber.supports(language)


def _require_transcriber(request: Request, language: str) -> Transcriber:
    if not _can_check(request, language):
        # Not an error in the page's control: this deployment ships no model for
        # this language. 503 rather than 404 -- the route exists, the capability
        # does not.
        raise HTTPException(status_code=503, detail="speech checking is not available")
    return _transcriber(request)


def _seconds(raw: float) -> float:
    """Trust the page's clock only within bounds. Negative is a bug or a lie."""
    if raw <= 0 or raw > MAX_SECONDS:
        raise HTTPException(status_code=400, detail="implausible reading time")
    return raw


def _stream_or_404(request: Request, stream_id: str, text: ReadingText) -> ReadingStream:
    stream = _streams(request).get(stream_id, datetime.now(UTC))
    if stream is None or stream.text_id != text.id:
        # Expired, unknown, or belonging to another passage. All three are
        # ordinary -- a tab left open over lunch is the common one.
        raise HTTPException(status_code=404, detail="reading not found")
    return stream


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

    checked = _can_check(request, text.language)
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
            checked=checked,
            # Whether it can also light words up while the reading is happening.
            # A separate switch because it costs a transcription every couple of
            # seconds on top of the one that produces the score.
            live=checked and get_settings(request).speech_live,
        ),
    )


def _result(
    request: Request,
    locale: str,
    *,
    subject,
    checkpoint,
    text: ReadingText,
    fluency=None,
    timing=None,
) -> HTMLResponse:
    """Render one reading's result. The single place a score becomes a page."""
    band = _library(request).band(subject.code, checkpoint.goal_set.after_year)
    source = _library(request).norms.source(band.source) if band else None

    if fluency is not None:
        outcome = verdict(fluency, band.low, band.high) if band else None
        earned = rewards.earned(fluency, outcome)
        timeline = replay(fluency)
    else:
        outcome = None
        hit = None if (band is None or timing.wpm is None) else timing.wpm >= band.low
        earned = rewards.earned_timed(finished=True, band_hit=hit)
        timeline = even_replay(text, timing.seconds)

    return templates.TemplateResponse(
        request,
        "partials/reading_result.html",
        context(
            request,
            locale,
            subject=subject,
            text=text,
            fluency=fluency,
            timing=timing,
            checked=fluency is not None,
            band=band,
            source=source,
            verdict=outcome or "unmeasured",
            rewards=earned,
            replay=timeline,
            # A replay of a reading nobody listened to is an even pace, not a
            # recording of anything. The page has to say which it is showing.
            replay_is_real=fluency is not None and fluency.timed,
        ),
    )


@router.post(
    "/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}/opptak",
    response_class=HTMLResponse,
)
async def submit_recording(
    request: Request, locale: str, grade: int, subject_code: str, text_id: str
) -> HTMLResponse:
    """Check a whole reading in one request.

    What the page posts when the words are not being lit up live. The body is
    raw 16 kHz mono PCM in a WAV container, produced by the page, and it exists
    only for the duration of this function.
    """
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)
    transcriber = _require_transcriber(request, text.language)

    try:
        recording = decode(await request.body())
    except AudioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    heard = transcriber.transcribe(recording.pcm, text.language)
    # The recogniser's own word timings, when it has them, measure the reading
    # rather than the file: the seconds spent reaching for the stop button are
    # not reading time. `decode` gives the fallback.
    fluency = measure(text, heard.words, heard.seconds or recording.seconds)

    return _result(
        request, locale, subject=subject, checkpoint=checkpoint, text=text, fluency=fluency
    )


@router.post("/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}/strom")
async def start_stream(
    request: Request, locale: str, grade: int, subject_code: str, text_id: str
) -> dict[str, object]:
    """Open a reading that will arrive in pieces."""
    validate_locale(locale)
    _, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)
    _require_transcriber(request, text.language)

    try:
        stream = _streams(request).create(
            goal_set=checkpoint.goal_set.code,
            text_id=text.id,
            language=text.language,
            now=datetime.now(UTC),
        )
    except StreamLimit as exc:
        # Every in-flight reading holds its audio in memory, so this is a memory
        # ceiling rather than a rate limit. Saying so beats an opaque 500 under
        # load.
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {"stream": stream.id, "cursor": 0}


@router.post("/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}/strom/{stream_id}")
async def push_chunk(
    request: Request, locale: str, grade: int, subject_code: str, text_id: str, stream_id: str
) -> dict[str, object]:
    """Take the next slice of audio and say how far the highlight has got.

    Raw 16-bit PCM, no container: the page has already produced exactly the
    format the model wants, and wrapping each slice in a WAV header only to strip
    it again would be ceremony.

    The cursor this returns is approximate and forward-only. It never feeds the
    score -- that comes from one pass over the whole recording when the reading
    ends -- so a live pass that mishears costs a beat of highlighting and
    nothing else.
    """
    validate_locale(locale)
    _, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)
    transcriber = _require_transcriber(request, text.language)
    stream = _stream_or_404(request, stream_id, text)

    chunk = await request.body()
    if len(chunk) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="chunk too large")
    if len(chunk) % SAMPLE_WIDTH:
        raise HTTPException(status_code=400, detail="not 16-bit PCM")

    try:
        stream.append(chunk)
    except StreamLimit as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    window = stream.tail(LIVE_WINDOW_SECONDS)
    if len(window) >= SAMPLE_RATE * SAMPLE_WIDTH:  # at least a second to listen to
        heard = transcriber.transcribe(window, stream.language)
        stream.cursor = advance(text, stream.cursor, heard.words)

    return {"cursor": stream.cursor, "total": text.word_count}


@router.post(
    "/{locale}/klasse/{grade}/{subject_code}/lesing/{text_id}/strom/{stream_id}/ferdig",
    response_class=HTMLResponse,
)
async def finish_stream(
    request: Request, locale: str, grade: int, subject_code: str, text_id: str, stream_id: str
) -> HTMLResponse:
    """Score the whole reading, then forget the audio.

    The score never comes from the live passes. They saw eight-second windows
    with no idea what came before; this sees the reading.
    """
    validate_locale(locale)
    subject, checkpoint = _checkpoint(request, subject_code, grade)
    text = _text_or_404(request, checkpoint.goal_set.code, text_id)
    transcriber = _require_transcriber(request, text.language)
    stream = _stream_or_404(request, stream_id, text)

    audio = bytes(stream.audio)
    fallback = stream.seconds
    # Dropped before the transcription rather than after, so an exception on the
    # way out cannot leave a buffer of a child's voice in memory until the TTL.
    _streams(request).drop(stream_id)

    heard = transcriber.transcribe(audio, text.language)
    fluency = measure(text, heard.words, heard.seconds or fallback)

    return _result(
        request, locale, subject=subject, checkpoint=checkpoint, text=text, fluency=fluency
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
    return _result(
        request, locale, subject=subject, checkpoint=checkpoint, text=text, timing=timing
    )
