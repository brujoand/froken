"""Every word Pensum has written, and the shapes those words take.

No word list is vendored and none is fetched. The lexicon is built at startup
from the text this project already authored -- the reading passages, and both
language halves of every quiz item -- which comes to a few thousand words per
language.

That is a choice rather than a shortcut, and it has one real advantage and one
real cost.

The advantage is that it is unambiguously ours. A word list is somebody's work
and carries somebody's licence; this repository is public and ships an image,
and "we compiled it from our own sentences" needs no attribution file and no
lawyer.

The cost is density. Roughly two in five words of the size a child reads have a
real-word neighbour in a lexicon this size, where a full dictionary would find
one for nearly all of them. `confusable.distractor` invents a misspelling for
the rest, which is a good distractor rather than a great one. If a properly
licensed word list is ever vendored, this module is the only thing that has to
change: it answers "is this a word" and "does this look like one", and nothing
above it cares how.

The second question is the more interesting one. An invented misspelling is only
useful if it is a mistake somebody could actually make -- *ffølge* and *sdrategi*
are not, and a child spots them without hearing anything. Rather than encode
Norwegian phonotactics by hand, the lexicon measures which letter pairs its own
words start with, contain and end with, and refuses anything outside that. The
data already knows what the language looks like.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pensum.items.loader import ItemBank
from pensum.reading.library import ReadingLibrary

# Letters only. A lexicon exists to answer whether something is a word, so
# numerals and punctuation have no place in it, and a hyphenated compound is two
# words for this purpose.
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)

# Below three letters there is nothing to confuse; above nine, a word is not
# what a primary-school dictation asks for. The bounds apply to what is *asked*,
# never to what the lexicon holds -- a long word is still a fine thing to
# recognise as real when looking for a distractor.
MIN_ASKED = 3
MAX_ASKED = 9

# A letter pair has to be seen this often before it counts as a shape of the
# language. One sighting is as likely to be a typo or a loanword as a rule, and
# a shape table that admits every accident admits every misspelling too.
SHAPE_FLOOR = 3


def words_in(text: str) -> set[str]:
    return {match.group(0).casefold() for match in _WORD.finditer(text)}


def _pairs(word: str) -> list[str]:
    return [word[index : index + 2] for index in range(len(word) - 1)]


def _frequent(counts: dict[str, int], floor: int) -> frozenset[str]:
    return frozenset(pair for pair, count in counts.items() if count >= floor)


@dataclass(frozen=True)
class Lexicon:
    """One language's words, and the letter pairs they are made of.

    `initial` and `final` are separate from `inner` because position is most of
    the rule: `ff` is ordinary inside a Norwegian word and impossible at the
    start of one, and a table that only knew "ff occurs" would happily invent
    *ffølge*.
    """

    words: frozenset[str]
    initial: frozenset[str]
    inner: frozenset[str]
    final: frozenset[str]

    def __contains__(self, word: str) -> bool:
        return word in self.words

    def plausible(self, word: str) -> bool:
        """Could this be a word of the language, whether or not it is one?

        Short words are waved through: at two letters there is no pair to judge,
        and the shape table has nothing to say.
        """
        pairs = _pairs(word)
        if not pairs:
            return True
        if pairs[0] not in self.initial:
            return False
        if pairs[-1] not in self.final:
            return False
        return all(pair in self.inner for pair in pairs)

    def askable(self, words: set[str]) -> list[str]:
        """Those words worth speaking aloud, in a stable order.

        Sorted rather than shuffled: which words a checkpoint offers must not
        depend on set iteration order, or the same data gives the same pupil a
        different exercise on a different day for no reason anybody can explain.
        """
        return sorted(
            word
            for word in words
            if MIN_ASKED <= len(word) <= MAX_ASKED and word.isalpha() and word in self.words
        )

    @classmethod
    def of(cls, words: set[str], *, floor: int = SHAPE_FLOOR) -> Lexicon:
        """Count the shapes of a set of words.

        `floor` is how many sightings make a pair a rule rather than an
        accident. It is a parameter only so a test can build a believable
        language out of a handful of words; nothing in the app passes it.
        """
        initial: dict[str, int] = {}
        inner: dict[str, int] = {}
        final: dict[str, int] = {}
        for word in words:
            pairs = _pairs(word)
            if not pairs:
                continue
            initial[pairs[0]] = initial.get(pairs[0], 0) + 1
            final[pairs[-1]] = final.get(pairs[-1], 0) + 1
            for pair in pairs:
                inner[pair] = inner.get(pair, 0) + 1
        return cls(
            words=frozenset(words),
            initial=_frequent(initial, floor),
            inner=_frequent(inner, floor),
            final=_frequent(final, floor),
        )


def build(items: ItemBank, reading: ReadingLibrary) -> dict[str, Lexicon]:
    """The lexicon, per language.

    Unreviewed content counts, and this is the one place in Pensum where that is
    right: none of it is shown to anybody. A draft passage's words are still
    Norwegian words, and excluding them would make the lexicon depend on review
    state -- so a distractor could change on the day somebody ticked a box.
    """
    pools: dict[str, set[str]] = {"nb": set(), "nn": set(), "en": set()}

    for reading_set in reading.reading_sets:
        for text in reading_set.texts:
            pools.setdefault(text.language, set()).update(text.word_list)

    # Every item carries both languages whatever subject it belongs to, so each
    # half feeds its own pool and no item is skipped: "fotosyntese" is a
    # Norwegian word even when it is teaching science.
    for item_set in items.item_sets:
        for item in item_set.items:
            for authored in (item.prompt, item.explanation):
                pools["nb"] |= words_in(authored.nb)
                pools["en"] |= words_in(authored.en)
            for choice in item.choices:
                pools["nb"] |= words_in(choice.text.nb)
                pools["en"] |= words_in(choice.text.en)

    # Nynorsk has no authored text of its own yet. Falling back to the bokmål
    # pool is wrong in detail and right in effect -- it is the same language --
    # where an empty lexicon would silently turn every distractor into an
    # invention.
    if not pools["nn"]:
        pools["nn"] = set(pools["nb"])

    return {language: Lexicon.of(words) for language, words in pools.items()}
