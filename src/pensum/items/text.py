"""A string we wrote, in both UI locales.

Its own module, and not part of the item schema, for a dull structural reason:
figures need it too, and the schema needs figures. Curriculum text does not come
through here at all -- that arrives from Udir in whichever maalform it was
published in, and is never authored, translated or edited by us.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Our own text is authored in the two UI locales, unlike curriculum text, which
# arrives from Udir in every maalform it was published in.
BOKMAAL = "nb"
ENGLISH = "en"


class AuthoredText(BaseModel):
    """A string we wrote, in both UI locales. Bokmål is required."""

    model_config = ConfigDict(frozen=True)

    nb: str = Field(min_length=1)
    en: str = Field(min_length=1)

    def get(self, locale: str) -> str:
        return self.en if locale == ENGLISH else self.nb
