"""Words that look or sound like other words.

The wrong answer in a listening exercise is the whole exercise. "kjøleskap" next
to "banan" tests nothing -- a child who heard any of it picks the right one. What
tests something is the word they might actually have written: *kjøleskapp*, with
the doubled consonant, or *skjøre* for *kjøre*, because kj and skj are the sound
Norwegian children spend two years learning to spell apart.

So nothing here is authored per word. A distractor is *derived* from the target,
by the confusions a child makes:

* **A real word, when one exists.** `bok`/`bak`, `bruk`/`bråk`, `ber`/`bør`.
  These are the best kind: both spellings are correct Norwegian, so the only way
  through is to have heard which one was said.
* **A plausible misspelling, when no real word is near enough.** `kjøleskapp`.
  Not a word, and precisely the non-word the pupil was at risk of writing.

Roughly two in five words of the size a child reads have a real neighbour in
Pensum's own vocabulary, so both paths carry real weight and both are tested.

The rules are per language and are about *spelling*, not phonetics: this is a
dictation exercise, and what is being practised is which letters come out. They
are deliberately a short, readable table rather than a phonetic algorithm -- a
teacher should be able to read this file and disagree with it.

What the table cannot do is know where in a word a change is possible. Left to
itself it turns `følge` into *ffølge* and `strategi` into *sdrategi*, neither of
which any child has written; both are spotted without listening, so both make
the exercise easier rather than harder. That judgement is left to
`Lexicon.plausible`, which measures the shapes of a language from the language
rather than asserting them here.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from pensum.listening.lexicon import Lexicon

# Norwegian spelling confusions, each written once and applied both ways.
#
# The list is short on purpose. Every pair here is a mistake that appears in
# real 1.-7. trinn writing; the temptation is to add every possible letter swap,
# which produces distractors no child would ever have written.
NB_PAIRS: tuple[tuple[str, str], ...] = (
    # The famous one. Three spellings, one sound, years of school.
    ("kj", "skj"),
    ("kj", "sj"),
    ("sj", "skj"),
    # Silent letters.
    ("hj", "j"),
    ("hv", "v"),
    ("gj", "j"),
    # Vowels that sit next to each other in the mouth.
    ("o", "å"),
    ("o", "u"),
    ("e", "æ"),
    ("y", "i"),
    # Voiced against voiceless, which is what an unstressed ending sounds like.
    ("b", "p"),
    ("d", "t"),
    ("g", "k"),
    ("v", "f"),
)

# English is a different problem: its spelling is irregular rather than
# rule-governed, so the confusions are letter groups that spell one sound.
EN_PAIRS: tuple[tuple[str, str], ...] = (
    ("ei", "ie"),
    ("ea", "ee"),
    ("ou", "ow"),
    ("oo", "u"),
    ("c", "k"),
    ("c", "s"),
    ("ph", "f"),
    ("gh", ""),
    ("y", "i"),
    ("er", "or"),
    ("ai", "ay"),
)

PAIRS = {"nb": NB_PAIRS, "nn": NB_PAIRS, "en": EN_PAIRS}

# Silent d, which needs its own handling because it is the only confusion here
# that is not reversible. `kald`, `land`, `bord`: the d is written and not said,
# so a child leaves it out, and dropping it is a mistake anywhere the letters
# appear. Putting one *in* is only a mistake where Norwegian would have put one
# -- closing a syllable -- and `bldir` for `blir` is nobody's spelling error.
NB_SILENT: tuple[tuple[str, str], ...] = (("ld", "l"), ("nd", "n"), ("rd", "r"))
SILENT = {"nb": NB_SILENT, "nn": NB_SILENT, "en": ()}

VOWELS = {
    "nb": "aeiouyæøå",
    "nn": "aeiouyæøå",
    "en": "aeiouy",
}

# Consonants a child doubles, or fails to double. In Norwegian this is the
# single commonest spelling error there is: a short vowel wants two.
DOUBLE_NB = "bdfgklmnprstv"
DOUBLE_EN = "bdflmnprstz"
DOUBLES = {"nb": DOUBLE_NB, "nn": DOUBLE_NB, "en": DOUBLE_EN}

ALPHABET = {
    "nb": "abcdefghijklmnopqrstuvwxyzæøå",
    "nn": "abcdefghijklmnopqrstuvwxyzæøå",
    "en": "abcdefghijklmnopqrstuvwxyz",
}


def _positions(word: str, part: str) -> list[int]:
    out: list[int] = []
    start = word.find(part)
    while start >= 0:
        out.append(start)
        start = word.find(part, start + 1)
    return out


def _replacements(word: str, part: str, into: str) -> set[str]:
    return {word[:start] + into + word[start + len(part) :] for start in _positions(word, part)}


def _swaps(word: str, pairs: Iterable[tuple[str, str]]) -> set[str]:
    """Every one-substitution variant the confusion table allows."""
    out: set[str] = set()
    for left, right in pairs:
        for a, b in ((left, right), (right, left)):
            if a:
                out |= _replacements(word, a, b)
    return out


def _doubling(word: str, consonants: str, vowels: str) -> set[str]:
    """Double a single consonant, or single a doubled one.

    Only between a vowel and either a vowel or the end of the word, which is
    where doubling means anything: it marks the vowel in front of it as short.
    Elsewhere the result is a typo rather than a spelling mistake -- *ffølge*,
    *leavinng*, *abbsence* -- and a child rules those out without listening,
    which is the opposite of what this exercise is for.
    """
    out: set[str] = set()
    for index, char in enumerate(word):
        if index == 0 or char not in consonants or word[index - 1] not in vowels:
            continue
        if word[index : index + 2] == char * 2:
            after = word[index + 2 : index + 3]
            if not after or after in vowels:
                out.add(word[:index] + char + word[index + 2 :])
            continue
        after = word[index + 1 : index + 2]
        if not after or after in vowels:
            out.add(word[: index + 1] + char + word[index + 1 :])
    return out


# Where a silent d may be *added*: at the end of the word, or before an ending
# that starts with e. That is where Norwegian puts one -- `kald`, `kalde`,
# `land`, `landet`, `bord`, `bordet` -- and nowhere else, so `halvannet` cannot
# become *haldvannet* and `forstått` cannot become *fordstått*.
SILENT_ENDINGS = ("", "t")


def _silent(word: str, pairs: Iterable[tuple[str, str]], vowels: str) -> set[str]:
    """Drop a silent d anywhere; add one only where Norwegian writes one."""
    out: set[str] = set()
    for written, said in pairs:
        out |= _replacements(word, written, said)
        for start in _positions(word, said):
            if start == 0 or word[start - 1] not in vowels:
                continue
            # Not into a doubled consonant: *dennde* is a typo, not a mistake.
            if word[start - 1] == said or word[start + 1 : start + 2] == said:
                continue
            rest = word[start + len(said) :]
            if rest in SILENT_ENDINGS or rest.startswith("e"):
                out.add(word[:start] + written + rest)
    return out


def confusions(word: str, language: str) -> set[str]:
    """Everything `word` might plausibly have been written as instead.

    Ordered by nothing: the caller decides which to prefer, and does so
    deterministically so the same word always produces the same exercise.
    """
    vowels = VOWELS.get(language, VOWELS["nb"])
    out = (
        _swaps(word, PAIRS.get(language, NB_PAIRS))
        | _doubling(word, DOUBLES.get(language, DOUBLE_NB), vowels)
        | _silent(word, SILENT.get(language, NB_SILENT), vowels)
    )
    out.discard(word)
    return {variant for variant in out if variant}


def neighbours(word: str, language: str) -> set[str]:
    """Confusions, widened by any single-letter substitution.

    Used only to look for *real* words: a substitution the confusion table does
    not know about still makes an excellent distractor when it happens to spell
    something (`bok` for `bak`), and makes a terrible one when it does not
    (`bnk`). So this is never used to invent a spelling -- only to search the
    lexicon.
    """
    out = confusions(word, language)
    for index in range(len(word)):
        for char in ALPHABET.get(language, ALPHABET["nb"]):
            if char != word[index]:
                out.add(word[:index] + char + word[index + 1 :])
    out.discard(word)
    return out


def ranked_against(word: str) -> Callable[[str], tuple[int, str]]:
    """Sort key putting the harder distractor first.

    A word that differs from the target only after its first letter is the
    better question, because the opening sound is the one a child hears most
    clearly: `some` beside `same` demands attention where `came` beside `same`
    does not. Alphabetical order breaks the tie, and breaks it the same way
    every time so the exercise is reproducible.
    """

    def key(other: str) -> tuple[int, str]:
        starts_apart = not other or other[0] != word[0]
        return (1 if starts_apart else 0, other)

    return key


def distractor(word: str, language: str, lexicon: Lexicon) -> str | None:
    """The wrong answer to offer beside `word`.

    A real word if the lexicon holds one that is a plausible mishearing, and a
    plausible misspelling otherwise. Deterministic: the same word in the same
    lexicon always yields the same distractor, so an exercise does not change
    under a reload and a failing test can be read.

    None when the word is too short or too odd to confuse with anything, which
    the caller must treat as "do not ask about this word" rather than as an
    error -- a two-letter word has nowhere to go, and nor has one whose only
    misspellings are shapes the language does not have.
    """
    key = ranked_against(word)
    predicted = confusions(word, language)

    # A neighbour the confusion table predicted is a mistake a child makes,
    # where a bare letter substitution that happens to spell something is a
    # coincidence -- useful, but second choice.
    for candidates in (predicted & lexicon.words, neighbours(word, language) & lexicon.words):
        if candidates:
            return sorted(candidates, key=key)[0]

    invented = sorted(
        (variant for variant in predicted - lexicon.words if lexicon.plausible(variant)),
        key=key,
    )
    return invented[0] if invented else None
