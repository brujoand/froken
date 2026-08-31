"""Readings recognised by the pupil's own device.

Every browser worth the name now ships a speech recogniser, and on a phone it is
usually better at children than anything Pensum can afford to run: it is tuned
for the device's microphone, it is instant, and on iOS and recent Chrome it can
run without the audio leaving the handset at all.

That last part is the whole reason this exists, and the whole reason it is
conditional. `SpeechRecognition` in a browser is two completely different
products wearing one API:

* on-device recognition, where the audio never leaves the machine -- strictly
  more private than posting it to Pensum, which is what the server path does;
* cloud recognition, where the browser vendor receives a child reading aloud.

The page uses the first without asking and the second only on an explicit
opt-in that says whose servers are involved. Pensum cannot detect which one is
happening from here, so that judgement is made in the browser, where the API
that answers the question lives.

What arrives here is a transcript, not audio. Nothing is recorded, uploaded or
kept -- and a transcript is also, unavoidably, whatever the page chose to send.
See `DeviceReading` for what that means for trust.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from pensum.reading.fluency import HeardWord

# Passage language -> the BCP-47 tag a browser recogniser wants. Nynorsk is
# offered as its own tag; browsers that do not have it fall back to bokmål on
# their own, which is the right degradation for a reader of either.
SPEECH_LOCALE = {"nb": "nb-NO", "nn": "nn-NO", "en": "en-GB"}

# A transcript longer than this is not a reading of a few hundred words, it is a
# stuck recogniser or someone poking the endpoint.
MAX_WORDS = 2000
MAX_WORD_LENGTH = 64


class DeviceWord(BaseModel):
    """One word the device reported, and when the page first saw it."""

    model_config = ConfigDict(frozen=True)

    t: str = Field(min_length=1, max_length=MAX_WORD_LENGTH)
    # Seconds from the start of the reading. Approximate by construction: it is
    # when the word appeared in an interim result, not when it was spoken.
    at: float | None = Field(default=None, ge=0)


class DeviceReading(BaseModel):
    """A reading the device recognised, as posted by the page.

    Every number in here is the page's word. A pupil with the developer tools
    open can claim a flawless reading in four seconds, and nothing here can tell.
    That is accepted rather than defended against: Pensum stores no score unless
    someone signed in, the result is shown to the pupil who produced it, and a
    practice tool that treats its user as an adversary is a worse practice tool.
    The server-side path exists for anyone who wants the stricter answer.
    """

    model_config = ConfigDict(frozen=True)

    seconds: float = Field(gt=0)
    words: tuple[DeviceWord, ...] = Field(default=(), max_length=MAX_WORDS)

    def heard(self) -> tuple[HeardWord, ...]:
        return tuple(HeardWord(text=word.t.casefold(), at=word.at) for word in self.words)
