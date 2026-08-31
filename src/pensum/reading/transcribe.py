"""Speech to text, offline and in-process.

Whisper, via CTranslate2 (`faster-whisper`), running inside the container
against audio that never leaves it. That is the whole reason for the choice: the
browser's own `SpeechRecognition` would have been free and would have shipped a
child's voice to a third party, which the rest of this application spends
considerable effort not doing.

Whisper rather than the lighter Vosk for one disqualifying reason: Vosk
publishes no Norwegian model. It covers some three dozen languages and Swedish
is the only Nordic one, so it could have served engelsk and nothing else --
and norsk is the half that matters most here.

The models are not in the repository. They are hundreds of megabytes of binary
and they are not ours to redistribute, so this degrades rather than fails: with
no model directory configured there is no transcriber, and the reading page
times the reading instead of checking it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from pensum.reading.fluency import HeardWord
from pensum.reading.schema import words

# UI/text language -> the language code Whisper knows it by. Whisper has both
# `no` and `nn`, so a nynorsk passage is transcribed as nynorsk rather than
# being quietly treated as bokmål.
WHISPER_LANGUAGE = {"nb": "no", "nn": "nn", "en": "en"}

# What a CTranslate2 model directory always contains. Used to tell "this
# directory is a model" from "this directory contains models".
MODEL_MARKER = "model.bin"

# Greedy decoding. A beam search is a little more accurate and several times
# slower, and a child is sitting in front of the page waiting for the answer;
# the alignment in `fluency` is forgiving enough to absorb the difference.
BEAM_SIZE = 1

# int8 on CPU. The container has no GPU and is not expected to get one.
COMPUTE_TYPE = "int8"


@dataclass(frozen=True)
class Transcription:
    """What was heard, when each word was heard, and over how long.

    The per-word times are what let the passage replay afterwards with the words
    lighting up as they were actually read. They are relative to the first word,
    not to the file, for the same reason `seconds` is.
    """

    words: tuple[HeardWord, ...]
    # The span from the first word to the last, when the recogniser reports word
    # timings. That is what should be divided by, not the length of the file:
    # the seconds a child spends reaching for the stop button are not reading
    # time.
    seconds: float

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)


class Transcriber(Protocol):
    """What a reading route needs. Implemented by Whisper, and by fakes in tests."""

    def transcribe(self, pcm: bytes, language: str) -> Transcription: ...

    def supports(self, language: str) -> bool: ...


def _is_model(path: Path) -> bool:
    return (path / MODEL_MARKER).is_file()


class WhisperTranscriber:
    """CTranslate2 Whisper models loaded from a directory.

    Two layouts are accepted, because the sensible deployments want different
    things:

    * `<dir>/nb` and `<dir>/en` -- a separate model per language, which is what
      you want if norsk is served by a Norwegian fine-tune such as NB-Whisper
      and engelsk by a stock Whisper.
    * `<dir>` as a model in its own right -- one multilingual Whisper for both,
      which is smaller and simpler and rather worse at Norwegian.

    Models load on first use and are then kept: loading costs seconds and
    several hundred megabytes of resident memory, so per-request loading would
    make the feature unusable and startup loading would charge every deployment
    for a feature it may never use.
    """

    def __init__(self, model_dir: Path) -> None:
        self._dir = model_dir
        self._models: dict[str, object] = {}

    def _path_for(self, language: str) -> Path | None:
        if language not in WHISPER_LANGUAGE:
            return None
        specific = self._dir / language
        if _is_model(specific):
            return specific
        return self._dir if _is_model(self._dir) else None

    def supports(self, language: str) -> bool:
        return self._path_for(language) is not None

    def _model(self, path: Path):
        key = str(path)
        if key not in self._models:
            from faster_whisper import WhisperModel  # imported late: an extra

            self._models[key] = WhisperModel(key, device="cpu", compute_type=COMPUTE_TYPE)
        return self._models[key]

    def transcribe(self, pcm: bytes, language: str) -> Transcription:
        import numpy as np

        from pensum.reading.audio import SAMPLE_RATE, SAMPLE_WIDTH

        path = self._path_for(language)
        if path is None:
            raise LookupError(f"no speech model for {language}")

        # Signed 16-bit PCM to the float32 in [-1, 1] the model expects. Done
        # here rather than handing over the WAV bytes so no audio decoder --
        # and no temporary file -- is involved.
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0

        segments, _info = self._model(path).transcribe(
            audio,
            language=WHISPER_LANGUAGE[language],
            word_timestamps=True,
            # Whisper invents fluent text over silence, and a hallucinated
            # sentence would be scored as words the child never said. The VAD
            # filter and dropping the previous-text conditioning are the two
            # standard mitigations.
            vad_filter=True,
            condition_on_previous_text=False,
            beam_size=BEAM_SIZE,
        )

        # Whisper hands back its own idea of a word -- "trappa." with the full
        # stop attached, or occasionally two words at once. Normalising each one
        # through the same tokeniser the passage went through is what keeps the
        # two sides of the alignment comparable; a token inherits the time of
        # the whisper word it came out of.
        heard: list[HeardWord] = []
        starts: list[float] = []
        ends: list[float] = []
        for segment in segments:
            for word in segment.words or ():
                start, end = float(word.start), float(word.end)
                starts.append(start)
                ends.append(end)
                heard.extend(HeardWord(text=token, at=start) for token in words(word.word))
            if not (segment.words or ()):
                # Word timings are requested but not guaranteed. Losing the
                # replay is acceptable; losing the transcript is not.
                heard.extend(HeardWord(text=token) for token in words(segment.text))

        if starts:
            origin = min(starts)
            seconds = max(ends) - origin
            heard = [
                HeardWord(text=w.text, at=None if w.at is None else round(w.at - origin, 3))
                for w in heard
            ]
        else:
            seconds = len(pcm) / (SAMPLE_RATE * SAMPLE_WIDTH)
        return Transcription(words=tuple(heard), seconds=seconds)


def load_transcriber(model_dir: Path | None) -> Transcriber | None:
    """Build a transcriber, or None when speech checking is not available.

    None is an ordinary configuration, not a failure: the published image ships
    without models, and the reading page still shows the passage and times it.
    """
    if model_dir is None or not model_dir.is_dir():
        return None
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return None

    transcriber = WhisperTranscriber(model_dir)
    return transcriber if any(transcriber.supports(lang) for lang in WHISPER_LANGUAGE) else None
