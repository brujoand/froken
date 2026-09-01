"""Reading fluency: the scorer, the audio gate, the committed passages, the routes.

The scoring tests matter most. Everything a pupil is told about their reading is
derived from `measure`, and the ways it can be wrong -- punishing a stumble,
counting an unread tail as mistakes, dividing by a two-second clip -- are all
ways of telling a child something untrue about their reading.
"""

from __future__ import annotations

import io
import wave
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.items.loader import ItemBank
from pensum.reading import rewards
from pensum.reading.audio import MAX_BYTES, SAMPLE_RATE, AudioError, decode
from pensum.reading.device import MAX_WORDS as MAX_DEVICE_WORDS
from pensum.reading.fluency import (
    ABOVE,
    BELOW,
    WITHIN,
    HeardWord,
    advance,
    close_enough,
    even_replay,
    heard_from_text,
    measure,
    replay,
    time_only,
    verdict,
)
from pensum.reading.library import ReadingLibrary
from pensum.reading.schema import ReadingText, words
from pensum.reading.streams import MAX_STREAMS, STREAM_TTL, StreamLimit, StreamStore
from pensum.reading.transcribe import WHISPER_LANGUAGE, Transcription, load_transcriber
from pensum.reading.validate import validate
from pensum.web.app import create_app

PASSAGE = (
    "Det sitter en katt på trappa vår. Den er svart, med hvite poter, "
    "og den ser rolig på meg mens jeg spiser frokosten min."
)


def text(body: str = PASSAGE) -> ReadingText:
    return ReadingText(
        id="t1",
        goal="KM14140",
        language="nb",
        title="Test",
        body=body,
        difficulty=1,
        source="pensum",
        reviewed=True,
    )


def wav(seconds: float = 1.0, rate: int = SAMPLE_RATE, channels: int = 1, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(b"\x00" * int(rate * seconds) * width * channels)
    return buffer.getvalue()


class FakeTranscriber:
    """Stands in for Whisper. The models are not in the repository, and a test
    that depended on one would be a test nobody could run."""

    def __init__(
        self,
        heard: str,
        seconds: float = 30.0,
        languages: tuple[str, ...] = ("nb", "en"),
        *,
        timed: bool = True,
    ):
        spoken = heard_from_text(heard)
        step = seconds / max(len(spoken), 1)
        self.words = (
            tuple(HeardWord(text=w.text, at=round(i * step, 3)) for i, w in enumerate(spoken))
            if timed
            else spoken
        )
        self.seconds = seconds
        self.languages = languages
        self.calls = 0

    def supports(self, language: str) -> bool:
        return language in self.languages

    def transcribe(self, pcm: bytes, language: str) -> Transcription:
        self.calls += 1
        return Transcription(words=self.words, seconds=self.seconds)


# --- Scoring ---------------------------------------------------------------


def test_a_perfect_reading_scores_every_word() -> None:
    fluency = measure(text(), PASSAGE, seconds=30.0)

    assert fluency.correct == fluency.total
    assert fluency.accuracy == 1.0
    assert fluency.finished
    assert fluency.misread == ()
    # The passage in 30 seconds is twice the passage in a minute.
    assert fluency.wcpm == fluency.total * 2


def test_a_stumble_costs_one_word_not_the_rest_of_the_line() -> None:
    """Children re-read a word they trip on. A scorer that derails on it would
    report a fluent reader as a failing one."""
    stumbled = PASSAGE.replace("trappa", "tra trappa")

    fluency = measure(text(), stumbled, seconds=30.0)

    assert fluency.correct == fluency.total
    assert fluency.misread == ()


def test_a_skipped_word_is_the_only_thing_marked() -> None:
    fluency = measure(text(), PASSAGE.replace("svart, ", ""), seconds=30.0)

    assert fluency.misread == ("svart",)
    assert fluency.correct == fluency.total - 1


def test_stopping_early_leaves_the_tail_unread_rather_than_wrong() -> None:
    """Not reaching a word is not misreading it, and the result page must not
    mark an unread paragraph as a page of mistakes."""
    half = " ".join(PASSAGE.split()[:12])

    fluency = measure(text(), half, seconds=30.0)

    assert not fluency.finished
    assert fluency.attempted == 12
    assert fluency.misread == ()
    # Accuracy is against what they reached, not the whole passage: a flawless
    # half is not 50% accurate.
    assert fluency.accuracy == 1.0


def test_case_and_punctuation_do_not_count_against_a_reader() -> None:
    assert words("Katten, sier han!") == ["katten", "sier", "han"]


def test_a_two_second_clip_is_reported_as_unmeasurable_not_as_a_number() -> None:
    fluency = measure(text(), PASSAGE, seconds=2.0)

    assert fluency.wcpm is None
    assert fluency.accuracy is None
    assert not fluency.measurable


def test_near_silence_is_unmeasurable_however_long_the_clip() -> None:
    fluency = measure(text(), "det sitter", seconds=60.0)

    assert fluency.wcpm is None


def test_verdict_places_a_reading_against_the_band() -> None:
    fast = measure(text(), PASSAGE, seconds=6.0)
    slow = measure(text(), PASSAGE, seconds=60.0)

    assert verdict(fast, 30, 60) == ABOVE
    assert verdict(slow, 30, 60) == BELOW
    assert verdict(measure(text(), PASSAGE, seconds=25.0), 30, 60) == WITHIN


def test_timing_alone_yields_a_pace_and_nothing_else() -> None:
    passage = text()
    timing = time_only(passage, seconds=30.0)

    assert timing.wpm == passage.word_count * 2
    assert timing.words == passage.word_count
    assert not hasattr(timing, "accuracy")


# --- Audio -----------------------------------------------------------------


def test_a_well_formed_recording_decodes_to_its_duration() -> None:
    recording = decode(wav(seconds=2.0))

    assert recording.seconds == pytest.approx(2.0)
    assert len(recording.pcm) == SAMPLE_RATE * 2 * 2


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"", "empty"),
        (b"not a wav at all", "junk"),
        (wav(rate=44_100), "wrong sample rate"),
        (wav(channels=2), "stereo"),
        (wav(width=1), "8-bit"),
    ],
)
def test_anything_but_the_one_accepted_format_is_refused(payload: bytes, reason: str) -> None:
    with pytest.raises(AudioError):
        decode(payload)


def test_an_oversized_upload_is_refused_before_it_is_parsed() -> None:
    with pytest.raises(AudioError):
        decode(b"\x00" * (SAMPLE_RATE * 2 * 400))


# --- Committed passages and bands ------------------------------------------


@pytest.fixture(scope="module")
def library() -> ReadingLibrary:
    return ReadingLibrary.load(include_unreviewed=True)


def test_committed_reading_data_validates_against_the_catalogue() -> None:
    """The same gate the pre-commit hook applies: orphaned goals and checkpoints
    with passages but no band, both of which are silent at runtime."""
    assert validate() == []


def test_every_passage_names_a_goal_that_exists(library: ReadingLibrary) -> None:
    """A curriculum revision renumbers goal codes. This is what makes an orphaned
    passage a red build rather than a silently mislabelled exercise."""
    catalogue = Catalogue.load()
    known = {
        goal.code
        for subject in catalogue.subjects
        for goal_set in subject.goal_sets
        for goal in goal_set.goals
    }

    assert library.goal_codes <= known, sorted(library.goal_codes - known)


def test_every_checkpoint_with_passages_has_a_band(library: ReadingLibrary) -> None:
    """A passage with no band would show a pace with nothing to read it against."""
    catalogue = Catalogue.load()
    missing = []
    for reading_set in library.reading_sets:
        subject = catalogue.subject(reading_set.subject)
        goal_set = subject.goal_set(reading_set.goal_set)
        if library.band(subject.code, goal_set.after_year) is None:
            missing.append(reading_set.goal_set)

    assert missing == []


def test_every_band_carries_a_source_with_a_caveat(library: ReadingLibrary) -> None:
    """The bands are authored, not official. A number shown without its caveat
    reads as a national standard, and there is no national standard."""
    for band in library.norms.bands:
        source = library.norms.source(band.source)
        assert source is not None
        assert len(source.caveat) > 40


def test_passages_are_long_enough_to_time(library: ReadingLibrary) -> None:
    for reading_set in library.reading_sets:
        for passage in reading_set.texts:
            assert passage.word_count >= 20, passage.id


def test_the_default_build_serves_only_reviewed_passages() -> None:
    """Same gate as quiz items: a merge alone never puts unread text in front of
    a child."""
    reviewed_only = ReadingLibrary.load()
    everything = ReadingLibrary.load(include_unreviewed=True)

    served = sum(len(reviewed_only.for_goal_set(s.goal_set)) for s in everything.reading_sets)
    authored = sum(len(s.texts) for s in everything.reading_sets)
    assert served <= authored


# --- Routes ----------------------------------------------------------------


@pytest.fixture(scope="module")
def catalogue() -> Catalogue:
    return Catalogue.load()


def app_for(catalogue: Catalogue, transcriber: object | None = None) -> TestClient:
    return TestClient(
        create_app(
            catalogue,
            ItemBank.load(),
            reading=ReadingLibrary.load(include_unreviewed=True),
            transcriber=transcriber,
        )
    )


@pytest.fixture(scope="module")
def timed_client(catalogue: Catalogue) -> TestClient:
    """The published image: passages and a clock, no speech model."""
    return app_for(catalogue)


def first_text(library: ReadingLibrary, goal_set: str) -> ReadingText:
    return library.for_goal_set(goal_set)[0]


def test_the_index_lists_the_passages_for_the_checkpoint(timed_client: TestClient) -> None:
    response = timed_client.get("/nb/klasse/2/NOR01-08/lesing")

    assert response.status_code == 200
    assert "Sokken som rømte" in response.text


def test_a_subject_with_no_passages_has_no_reading_page(timed_client: TestClient) -> None:
    assert timed_client.get("/nb/klasse/2/MAT01-06/lesing").status_code == 404


def test_the_reading_page_shows_the_passage(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert response.status_code == 200
    assert "sokk" in response.text
    # No model configured, so the page must post to the timing endpoint and
    # promise nothing about a recording.
    assert "/tid" in response.text
    assert "/opptak" not in response.text


def test_the_subject_page_links_to_reading_when_passages_exist(
    timed_client: TestClient,
) -> None:
    """Derived from the passages, not from a list of "reading subjects": norsk
    has them, matematikk does not, and nothing anywhere says so explicitly."""
    norsk = timed_client.get("/nb/klasse/2/NOR01-08")
    maths = timed_client.get("/nb/klasse/2/MAT01-06")

    assert "/NOR01-08/lesing" in norsk.text
    assert "/lesing" not in maths.text


def test_english_passages_are_served_in_their_own_language(
    timed_client: TestClient, library
) -> None:
    passage = first_text(library, "KV1030")

    response = timed_client.get(f"/nb/klasse/2/ENG01-06/lesing/{passage.id}")

    assert response.status_code == 200
    # The chrome stays Norwegian; the passage is marked as English so a screen
    # reader does not read it with Norwegian phonetics.
    assert 'lang="en"' in response.text


def test_an_unknown_passage_is_a_404(timed_client: TestClient) -> None:
    assert timed_client.get("/nb/klasse/2/NOR01-08/lesing/nope").status_code == 404


def test_timing_a_reading_reports_a_pace_and_says_it_was_not_checked(
    timed_client: TestClient, library
) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/tid", data={"seconds": "60"}
    )

    assert response.status_code == 200
    assert str(passage.word_count) in response.text
    assert "Ingen har sjekket" in response.text


@pytest.mark.parametrize("seconds", ["0", "-5", "100000"])
def test_an_implausible_clock_is_refused(timed_client: TestClient, library, seconds: str) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/tid", data={"seconds": seconds}
    )

    assert response.status_code == 400


def test_posting_audio_to_a_build_with_no_model_is_a_503(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/opptak",
        content=wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 503


def test_a_checked_reading_reports_accuracy(catalogue: Catalogue, library) -> None:
    passage = first_text(library, "KV1107")
    client = app_for(catalogue, FakeTranscriber(passage.body, seconds=30.0))

    response = client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/opptak",
        content=wav(seconds=30),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 200
    assert "100 %" in response.text
    # The caveat travels with the number.
    assert "ikke en offisiell norm" in response.text


def test_a_checked_page_posts_the_recording_and_says_what_happens_to_it(
    catalogue: Catalogue, library
) -> None:
    passage = first_text(library, "KV1107")
    client = app_for(catalogue, FakeTranscriber(passage.body))

    response = client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert "/opptak" in response.text
    assert "lagres ikke" in response.text


def test_a_malformed_recording_is_a_400_not_a_500(catalogue: Catalogue, library) -> None:
    passage = first_text(library, "KV1107")
    client = app_for(catalogue, FakeTranscriber(passage.body))

    response = client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/opptak",
        content=b"not audio",
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 400


def test_a_language_the_deployment_has_no_model_for_is_a_503(catalogue: Catalogue, library) -> None:
    """Norwegian models shipped, English not: engelsk must degrade rather than
    transcribe Norwegian phonetics against an English passage."""
    passage = first_text(library, "KV1030")
    client = app_for(catalogue, FakeTranscriber("whatever", languages=("nb",)))

    response = client.post(
        f"/nb/klasse/2/ENG01-06/lesing/{passage.id}/opptak",
        content=wav(),
        headers={"Content-Type": "audio/wav"},
    )

    assert response.status_code == 503


# --- Configuration ---------------------------------------------------------


def test_no_model_directory_means_no_transcriber() -> None:
    assert load_transcriber(None) is None


def test_a_missing_model_directory_degrades_rather_than_raising(tmp_path) -> None:
    assert load_transcriber(tmp_path / "absent") is None


def test_speech_is_off_by_default() -> None:
    assert Settings().speech_enabled is False


def test_every_passage_language_maps_to_a_whisper_language() -> None:
    """Including nynorsk, which Whisper knows as its own language rather than as
    a dialect of the bokmål one."""
    assert WHISPER_LANGUAGE == {"nb": "no", "nn": "nn", "en": "en"}


# --- Lighting the words up -------------------------------------------------


def test_the_printed_tokens_are_numbered_the_way_the_scorer_counts() -> None:
    """The page lights word 43; the scorer marks word 43. If these two ever
    disagree, every highlight on the screen is off by the difference."""
    passage = text()

    printed = [token for paragraph in passage.paragraphs for token in paragraph]
    numbered = [token for token in printed if token.index is not None]

    assert [token.index for token in numbered] == list(range(passage.word_count))
    assert [token.text.casefold() for token in numbered] == passage.word_list
    # Punctuation and spacing survive, or the passage is printed as a word list.
    assert "".join(token.text for token in printed[: len(printed)]).strip().startswith("Det")


def test_the_live_cursor_moves_forward_with_what_was_just_heard() -> None:
    passage = text()
    first = advance(passage, 0, "det sitter en katt")

    assert first == 4


def test_the_live_cursor_never_moves_backwards() -> None:
    """A highlight that jumps back mid-sentence is worse than one that lags."""
    passage = text()
    cursor = advance(passage, 0, "det sitter en katt på trappa vår")

    assert advance(passage, cursor, "noe helt annet") == cursor


def test_a_common_word_cannot_teleport_the_cursor_to_the_end() -> None:
    """ "på" appears twice in the passage. Matching only the next stretch is what
    keeps the second occurrence from dragging the highlight forward."""
    passage = text()

    assert advance(passage, 0, "på") <= 6


def test_the_replay_uses_the_times_the_words_were_actually_heard() -> None:
    passage = text()
    spoken = tuple(
        HeardWord(text=word, at=index * 0.5) for index, word in enumerate(passage.word_list)
    )

    timeline = replay(measure(passage, spoken, seconds=12.0))

    assert [entry["i"] for entry in timeline] == list(range(passage.word_count))
    assert timeline[0]["at"] == 0.0
    assert timeline[2]["at"] == 1.0
    assert all(entry["ok"] for entry in timeline)


def test_a_word_that_was_not_heard_still_gets_a_time_so_the_replay_runs_on() -> None:
    passage = text()
    timeline = replay(measure(passage, PASSAGE.replace("svart, ", ""), seconds=30.0))

    missed = [entry for entry in timeline if not entry["ok"]]
    assert missed
    # No timings from a bare transcript, so the whole thing is evenly paced --
    # and every reached word is still in the timeline.
    assert [entry["i"] for entry in timeline] == sorted(entry["i"] for entry in timeline)


def test_a_reading_nobody_listened_to_replays_at_an_even_pace() -> None:
    passage = text()
    timeline = even_replay(passage, seconds=30.0)

    assert len(timeline) == passage.word_count
    gaps = {round(timeline[i + 1]["at"] - timeline[i]["at"], 3) for i in range(len(timeline) - 1)}
    assert len(gaps) == 1


# --- Badges ----------------------------------------------------------------


def test_finishing_is_rewarded_regardless_of_pace() -> None:
    """The one badge that cannot mislead: a slow reader who reaches the last
    word earns exactly what a fast one earns."""
    slow = rewards.earned(measure(text(), PASSAGE, seconds=120.0), BELOW)
    fast = rewards.earned(measure(text(), PASSAGE, seconds=8.0), ABOVE)

    assert slow.finished and fast.finished


def test_the_band_badge_is_for_reaching_the_band_not_for_landing_in_it() -> None:
    """Rewarding only the middle would turn a guideline range into a target with
    a penalty on both sides."""
    fluency = measure(text(), PASSAGE, seconds=10.0)

    assert rewards.earned(fluency, WITHIN).band_hit
    assert rewards.earned(fluency, ABOVE).band_hit
    assert not rewards.earned(fluency, BELOW).band_hit


def test_a_reading_nobody_listened_to_earns_no_stars() -> None:
    """There is no accuracy, and inventing one from the clock would hand out
    three stars for reading fast and none for reading carefully."""
    assert rewards.earned_timed(finished=True, band_hit=True).stars is None


def test_stars_are_forgiving_because_the_recogniser_is_not() -> None:
    assert rewards.stars_for(1.0) == 3
    assert rewards.stars_for(0.90) == 3
    assert rewards.stars_for(0.80) == 2
    assert rewards.stars_for(0.60) == 1
    assert rewards.stars_for(0.20) == 0
    assert rewards.stars_for(None) is None


# --- Streaming a reading ---------------------------------------------------


def wpm_in(html: str) -> str:
    return html.split('data-wpm="')[1].split('"')[0]


def pcm(seconds: float = 2.0) -> bytes:
    return b"\x00" * (int(SAMPLE_RATE * seconds) * 2)


def test_a_streamed_reading_lights_words_up_then_scores_the_whole_thing(
    catalogue: Catalogue, library
) -> None:
    passage = first_text(library, "KV1107")
    transcriber = FakeTranscriber(passage.body)
    client = app_for(catalogue, transcriber)
    base = f"/nb/klasse/2/NOR01-08/lesing/{passage.id}"

    opened = client.post(f"{base}/strom")
    assert opened.status_code == 200
    stream_id = opened.json()["stream"]

    pushed = client.post(
        f"{base}/strom/{stream_id}",
        content=pcm(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert pushed.status_code == 200
    assert pushed.json()["cursor"] > 0

    finished = client.post(f"{base}/strom/{stream_id}/ferdig")
    assert finished.status_code == 200
    assert "100 %" in finished.text

    # The audio is dropped the moment the reading is scored, not on a timer.
    assert client.post(f"{base}/strom/{stream_id}/ferdig").status_code == 404


def test_the_live_cursor_never_feeds_the_score(catalogue: Catalogue, library) -> None:
    """The live passes see eight-second windows with no idea what came before.
    The score comes from one pass over the whole recording, and a live pass that
    misheard must not be able to change it."""
    passage = first_text(library, "KV1107")
    client = app_for(catalogue, FakeTranscriber(passage.body))
    base = f"/nb/klasse/2/NOR01-08/lesing/{passage.id}"

    stream_id = client.post(f"{base}/strom").json()["stream"]
    for _ in range(3):
        client.post(
            f"{base}/strom/{stream_id}",
            content=pcm(),
            headers={"Content-Type": "application/octet-stream"},
        )

    streamed = client.post(f"{base}/strom/{stream_id}/ferdig")
    one_shot = client.post(
        f"{base}/opptak", content=wav(seconds=30), headers={"Content-Type": "audio/wav"}
    )

    assert 'data-wpm="' in streamed.text
    assert wpm_in(streamed.text) == wpm_in(one_shot.text)


def test_a_stream_for_another_passage_is_not_accepted(catalogue: Catalogue, library) -> None:
    """The stream id is the only thing standing between one reading's audio and
    another request."""
    first = first_text(library, "KV1107")
    other = library.for_goal_set("KV1107")[1]
    client = app_for(catalogue, FakeTranscriber(first.body))

    stream_id = client.post(f"/nb/klasse/2/NOR01-08/lesing/{first.id}/strom").json()["stream"]
    response = client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{other.id}/strom/{stream_id}",
        content=pcm(),
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 404


def test_a_chunk_that_is_not_16_bit_pcm_is_refused(catalogue: Catalogue, library) -> None:
    passage = first_text(library, "KV1107")
    client = app_for(catalogue, FakeTranscriber(passage.body))
    base = f"/nb/klasse/2/NOR01-08/lesing/{passage.id}"

    stream_id = client.post(f"{base}/strom").json()["stream"]
    response = client.post(
        f"{base}/strom/{stream_id}",
        content=b"\x00" * 33,
        headers={"Content-Type": "application/octet-stream"},
    )

    assert response.status_code == 400


def test_a_build_with_no_model_cannot_open_a_stream(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")

    assert timed_client.post(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/strom").status_code == 503


def test_streams_expire_and_are_pruned() -> None:
    store = StreamStore()
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)
    stream = store.create(goal_set="KV1107", text_id="t1", language="nb", now=now)

    assert store.get(stream.id, now) is stream
    assert store.get(stream.id, now + STREAM_TTL + timedelta(seconds=1)) is None
    assert len(store) == 0


def test_a_stream_refuses_to_grow_without_limit() -> None:
    store = StreamStore()
    stream = store.create(
        goal_set="KV1107", text_id="t1", language="nb", now=datetime(2026, 8, 31, tzinfo=UTC)
    )

    with pytest.raises(StreamLimit):
        stream.append(b"\x00" * (MAX_BYTES + 2))


def test_too_many_readings_at_once_is_refused_rather_than_swallowing_memory() -> None:
    """Every in-flight reading holds its audio in memory, so this is a memory
    ceiling rather than a rate limit."""
    store = StreamStore()
    now = datetime(2026, 8, 31, tzinfo=UTC)
    for _ in range(MAX_STREAMS):
        store.create(goal_set="KV1107", text_id="t1", language="nb", now=now)

    with pytest.raises(StreamLimit):
        store.create(goal_set="KV1107", text_id="t1", language="nb", now=now)


# --- The screen ------------------------------------------------------------


def test_the_passage_is_rendered_one_span_per_word(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert response.text.count('class="w" data-i=') == passage.word_count


def test_live_highlighting_is_off_when_the_deployment_turns_it_off(
    catalogue: Catalogue, library
) -> None:
    """It costs a transcription every couple of seconds on top of the one that
    produces the score, which is the first thing to turn off under load."""
    passage = first_text(library, "KV1107")
    client = TestClient(
        create_app(
            catalogue,
            ItemBank.load(),
            settings=Settings(session_secret="test", speech_live=False),
            reading=ReadingLibrary.load(include_unreviewed=True),
            transcriber=FakeTranscriber(passage.body),
        )
    )

    response = client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert 'data-live="false"' in response.text
    # Still checked, just not live.
    assert 'data-checked="true"' in response.text


def test_the_result_carries_the_replay_and_the_caveats(catalogue: Catalogue, library) -> None:
    passage = first_text(library, "KV1107")
    client = app_for(catalogue, FakeTranscriber(passage.body))

    response = client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/opptak",
        content=wav(seconds=30),
        headers={"Content-Type": "audio/wav"},
    )

    assert 'id="reading-timeline"' in response.text
    # Stars never appear without the sentence explaining what they are worth.
    assert "stars" in response.text
    assert "hører dårlig" in response.text


# --- Rights and takedowns --------------------------------------------------


def test_every_page_carries_the_takedown_contact(timed_client: TestClient) -> None:
    """A contact address nobody can find is not a contact address."""
    for path in ("/nb/", "/nb/klasse/2", "/nb/klasse/2/NOR01-08"):
        assert "/nb/rettigheter" in timed_client.get(path).text, path


def test_the_rights_page_names_the_contact_and_who_owns_what(
    timed_client: TestClient,
) -> None:
    response = timed_client.get("/nb/rettigheter")

    assert response.status_code == 200
    assert "dmca@brujordet.no" in response.text
    # The three owners, kept apart: conflating them is how a takedown request
    # ends up aimed at the wrong one.
    assert "NLOD" in response.text
    assert "MIT" in response.text


def test_the_rights_page_exists_in_english_too(timed_client: TestClient) -> None:
    response = timed_client.get("/en/rettigheter")

    assert response.status_code == 200
    assert "Rights and takedowns" in response.text


def test_an_unknown_locale_has_no_rights_page(timed_client: TestClient) -> None:
    assert timed_client.get("/de/rettigheter").status_code == 404


def test_the_takedown_address_is_configurable(catalogue: Catalogue) -> None:
    """So a fork gets its own inbox rather than ours."""
    client = TestClient(
        create_app(
            catalogue,
            ItemBank.load(),
            settings=Settings(session_secret="test", dmca_email="rights@example.com"),
        )
    )

    response = client.get("/nb/rettigheter")

    assert "rights@example.com" in response.text
    assert "dmca@brujordet.no" not in response.text


def test_the_reading_index_links_to_the_rights_page(timed_client: TestClient) -> None:
    """Next to the claim that the passages are ours, not only in the footer."""
    response = timed_client.get("/nb/klasse/2/NOR01-08/lesing")

    assert response.text.count("/nb/rettigheter") >= 2


def test_no_passage_claims_a_source_we_may_not_reproduce(library: ReadingLibrary) -> None:
    """Every passage is written for Pensum. A passage sourced from anywhere else
    would need a licence recorded here, and none is."""
    for reading_set in library.reading_sets:
        for passage in reading_set.texts:
            assert passage.source == "pensum", passage.id


# --- Pronunciation versus the wrong word -----------------------------------


def test_a_different_ending_is_pronunciation_not_a_mistake() -> None:
    """boka/boken, trappa/trappen. Norwegian gives endless legitimate variation,
    and counting it as errors marks down the children who are reading fine."""
    assert close_enough("trappa", "trappen")
    assert close_enough("boka", "boken")
    assert close_enough("sitter", "sitte")


def test_a_different_word_is_a_mistake() -> None:
    assert not close_enough("hus", "hest")
    assert not close_enough("katt", "hund")
    # Short words are held to an exact match: that is where a real substitution
    # hides, and three letters carry no signal for a ratio test.
    assert not close_enough("og", "om")
    assert not close_enough("hus", "his")


def test_mispronouncing_a_word_does_not_cost_accuracy() -> None:
    passage = text()
    said = PASSAGE.replace("trappa", "trappen").replace("sitter", "sitte")

    fluency = measure(passage, said, seconds=30.0)

    assert fluency.accuracy == 1.0
    assert fluency.misread == ()
    # Tracked, so the page can say the dialect was not what cost the percentage.
    assert fluency.close_count == 2


def test_reading_the_wrong_word_still_counts_against_you() -> None:
    passage = text()

    fluency = measure(passage, PASSAGE.replace("katt", "hund"), seconds=30.0)

    assert "katt" in fluency.misread
    assert fluency.accuracy < 1.0


def test_a_hallucinating_recogniser_cannot_make_the_alignment_crawl() -> None:
    """Whisper invents fluent text over silence. Aligning against all of it is
    quadratic work for no gain."""
    passage = text()

    fluency = measure(passage, " ".join(["blah"] * 20_000), seconds=60.0)

    assert fluency.correct == 0


# --- The device's own recogniser -------------------------------------------


def test_a_device_reading_is_scored_without_any_model_on_the_server(
    timed_client: TestClient, library
) -> None:
    """The whole point: the published image ships no models, and a phone that
    recognises speech itself still gets a checked reading."""
    passage = first_text(library, "KV1107")
    words = [
        {"t": word, "at": round(index * 0.4, 2)} for index, word in enumerate(passage.word_list)
    ]

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/enhet",
        json={"seconds": 30.0, "words": words},
    )

    assert response.status_code == 200
    assert "100 %" in response.text
    # Word times came with the transcript, so the replay is the real thing.
    assert 'id="reading-timeline"' in response.text


def test_a_device_reading_with_no_timings_still_scores(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")
    words = [{"t": word} for word in passage.word_list]

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/enhet",
        json={"seconds": 45.0, "words": words},
    )

    assert response.status_code == 200


@pytest.mark.parametrize("seconds", [0, -3, 100_000])
def test_a_device_reading_with_an_implausible_clock_is_refused(
    timed_client: TestClient, library, seconds: float
) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/enhet",
        json={"seconds": seconds, "words": [{"t": "det"}]},
    )

    assert response.status_code in (400, 422)


def test_a_device_transcript_cannot_be_unbounded(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/enhet",
        json={"seconds": 30.0, "words": [{"t": "det"}] * (MAX_DEVICE_WORDS + 1)},
    )

    assert response.status_code == 422


def test_a_deployment_can_turn_the_device_recogniser_off(catalogue: Catalogue, library) -> None:
    passage = first_text(library, "KV1107")
    client = TestClient(
        create_app(
            catalogue,
            ItemBank.load(),
            settings=Settings(session_secret="test", device_speech=False),
            reading=ReadingLibrary.load(include_unreviewed=True),
        )
    )

    page = client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")
    posted = client.post(
        f"/nb/klasse/2/NOR01-08/lesing/{passage.id}/enhet",
        json={"seconds": 30.0, "words": [{"t": "det"}]},
    )

    assert 'data-device="false"' in page.text
    assert posted.status_code == 503


def test_the_page_carries_the_language_tag_the_browser_needs(
    timed_client: TestClient, library
) -> None:
    norsk = first_text(library, "KV1107")
    engelsk = first_text(library, "KV1030")

    assert (
        'data-speech-locale="nb-NO"'
        in timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{norsk.id}").text
    )
    assert (
        'data-speech-locale="en-GB"'
        in timed_client.get(f"/nb/klasse/2/ENG01-06/lesing/{engelsk.id}").text
    )


def test_the_page_offers_device_recognition_by_default(timed_client: TestClient, library) -> None:
    """Even on the published image, which has no models: the recognising happens
    in the browser."""
    passage = first_text(library, "KV1107")

    page = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert 'data-device="true"' in page.text
    # And the page must carry both statements, because only the browser knows
    # which one applies.
    assert "forlater ikke telefonen" in page.text
    assert "kan stemmen din bli sendt" in page.text


def test_the_prefix_rule_has_a_known_cost() -> None:
    """Recorded rather than hidden: two different words that share a stem are
    accepted as one. "store" and "storm" are the clearest case.

    The trade is deliberate. Norwegian inflection is almost entirely in the
    ending, so a rule that reads the beginning catches nearly all legitimate
    variation; the price is that a genuine substitution with the same stem slips
    through. Given a recogniser that mishears children constantly, wrongly
    forgiving is the better direction to be wrong in.
    """
    assert close_enough("store", "storm")
    # But it is a stem rule, not a "first letter" rule.
    assert not close_enough("stor", "smør")


def test_the_page_carries_what_to_say_when_the_recogniser_refuses(
    timed_client: TestClient, library
) -> None:
    """A refusal has to arrive as words, not as nothing happening.

    The recogniser reports `not-allowed` both for a denied permission prompt and
    for a device with dictation switched off, and the page cannot tell those
    apart -- so the one message has to cover the remedy for both.
    """
    passage = first_text(library, "KV1107")

    page = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert "data-label-speech-blocked=" in page.text
    assert "data-label-speech-unsupported=" in page.text
    # Naming the setting, because a child cannot be expected to guess it.
    assert "diktering" in page.text


def test_the_start_button_comes_before_the_passage(timed_client: TestClient, library) -> None:
    """A child reading aloud should not have to scroll past the text to find the
    button that stops the clock. Source order is what decides that, so it is
    what is asserted."""
    passage = first_text(library, "KV1107")

    body = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}").text

    assert body.index('id="reading-toggle"') < body.index('id="reading-passage"')


def test_checking_the_reading_is_not_optional(timed_client: TestClient, library) -> None:
    """There is no switch, and there was one for a day.

    Checking the reading is the exercise, so an off position is a way to get a
    result that looks like the others and measures something else. What stays is
    the statement of which recogniser is listening -- that differs by device
    rather than by anything the reader chose, and a statement is not a control.
    """
    passage = first_text(library, "KV1107")

    body = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}").text

    assert "reading-timed-box" not in body
    assert "reading-consent-box" not in body
    assert 'type="checkbox"' not in body
    # The notice itself is still there, filled in by the browser.
    assert 'id="reading-engine"' in body


def test_progress_is_drawn_under_the_words_not_in_a_bar(timed_client: TestClient, library) -> None:
    """A bar elsewhere on the screen asks a reader to look away from the text to
    learn something the text can say in place, and looking away is what this
    screen exists to prevent. The line under the read words says it instead.

    The progressbar element stays, hidden: a line under a word is nothing at all
    to someone not reading with their eyes, and dropping the visual bar must not
    quietly drop them too.
    """
    passage = first_text(library, "KV1107")

    body = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}").text

    assert 'id="reading-bar"' not in body
    assert 'role="progressbar"' in body
    assert "visually-hidden" in body


def test_the_gaps_between_words_can_carry_the_line(timed_client: TestClient, library) -> None:
    """Underlining only the words leaves a break at every space, which reads as
    a row of dashes rather than as one line showing how far someone has got. The
    spaces and punctuation are spans too, so they can be lit with the word they
    follow.

    Word numbering is untouched: only `.w` spans carry `data-i`, and the scorer
    and the page have to agree about which word is which.
    """
    passage = first_text(library, "KV1107")

    body = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}").text
    article = body[body.index('id="reading-passage"') : body.index("</article>")]

    assert '<span class="gap">' in article
    assert article.count('data-i="') == passage.word_count


def test_the_heat_indicator_is_in_the_hud_not_the_passage(
    timed_client: TestClient, library
) -> None:
    """Flames appearing between the words would move the words, which is the one
    thing this screen must never do to someone reading."""
    passage = first_text(library, "KV1107")

    body = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}").text
    hud = body[body.index('class="reading-hud"') : body.index('class="reading-controls"')]

    assert 'id="reading-heat"' in hud
    # Empty until a run earns it, so it takes up no room and announces nothing
    # on a reading that never gets one.
    assert 'id="reading-heat"' not in body[body.index("</article>") :]
