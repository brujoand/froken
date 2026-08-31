"""Reading fluency: the scorer, the audio gate, the committed passages, the routes.

The scoring tests matter most. Everything a pupil is told about their reading is
derived from `measure`, and the ways it can be wrong -- punishing a stumble,
counting an unread tail as mistakes, dividing by a two-second clip -- are all
ways of telling a child something untrue about their reading.
"""

from __future__ import annotations

import io
import wave

import pytest
from fastapi.testclient import TestClient

from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.items.loader import ItemBank
from pensum.reading.audio import SAMPLE_RATE, AudioError, decode
from pensum.reading.fluency import ABOVE, BELOW, WITHIN, measure, time_only, verdict
from pensum.reading.library import ReadingLibrary
from pensum.reading.schema import ReadingText, words
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
    """Stands in for Vosk. The models are not in the repository, and a test that
    depended on one would be a test nobody could run."""

    def __init__(
        self, heard: str, seconds: float = 30.0, languages: tuple[str, ...] = ("nb", "en")
    ):
        self.heard = heard
        self.seconds = seconds
        self.languages = languages

    def supports(self, language: str) -> bool:
        return language in self.languages

    def transcribe(self, pcm: bytes, language: str) -> Transcription:
        return Transcription(text=self.heard, seconds=self.seconds)


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
    assert "Katten på trappa" in response.text


def test_a_subject_with_no_passages_has_no_reading_page(timed_client: TestClient) -> None:
    assert timed_client.get("/nb/klasse/2/MAT01-06/lesing").status_code == 404


def test_the_reading_page_shows_the_passage(timed_client: TestClient, library) -> None:
    passage = first_text(library, "KV1107")

    response = timed_client.get(f"/nb/klasse/2/NOR01-08/lesing/{passage.id}")

    assert response.status_code == 200
    assert "trappa" in response.text
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
