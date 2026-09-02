"""The listening exercise: a word is spoken, and is picked or spelled.

The thing worth testing here is not the marking -- two strings either match or
they do not -- but the *distractor*. Every wrong answer on this screen is
generated, and a generated wrong answer fails in two directions:

* Too easy. `ffølge` beside `følge` is spotted without hearing anything, so the
  exercise stops being a listening exercise.
* Impossible. A distractor that differs only in a letter nobody could hear, or
  one built from a rule that does not apply where it was applied, teaches a
  child that they misheard when they did not.

So most of what follows is about which variants the generator will and will not
produce, and where it draws the line between the two.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pensum.catalogue.loader import Catalogue
from pensum.config import Settings
from pensum.items.loader import ItemBank
from pensum.listening import confusable
from pensum.listening.exercise import (
    ROUND_LENGTH,
    WRITE_FROM_YEAR,
    Question,
    build_round,
    is_correct,
    mode_for,
    normalise,
    question_for,
)
from pensum.listening.lexicon import (
    MAX_ASKED,
    MIN_ASKED,
    SHAPE_FLOOR,
    Lexicon,
    build,
    words_in,
)
from pensum.listening.library import MIN_ROUND, ListeningLibrary
from pensum.listening.marking import MAX_ANSWER, Answers, mark
from pensum.reading.library import ReadingLibrary
from pensum.reading.schema import ReadingText
from pensum.web.app import create_app


def lexicon(*words: str) -> Lexicon:
    return Lexicon.of(set(words))


def pool_of(*words: str) -> Lexicon:
    """A believable little language.

    The shape table needs `SHAPE_FLOOR` sightings before it will admit a letter
    pair, which is right for the five thousand words the app builds it from and
    impossible for the handful a test can write out. So a test lexicon counts
    every pair it sees -- what is being tested here is which variants the
    generator produces, not how many words it takes to learn Norwegian.
    """
    return Lexicon.of(set(words), floor=1)


@pytest.fixture(scope="module")
def real() -> dict[str, Lexicon]:
    """Pensum's own vocabulary, as the app builds it."""
    return build(
        ItemBank.load(include_unreviewed=True), ReadingLibrary.load(include_unreviewed=True)
    )


# --- the confusion tables --------------------------------------------------


def test_the_famous_norwegian_confusion_goes_all_three_ways() -> None:
    """kj, skj and sj spell one sound, and telling them apart is two years of
    Norwegian school. Anything less than all three directions would leave out
    the mistake a child actually makes."""
    variants = confusable.confusions("kjøre", "nb")
    assert "skjøre" in variants
    assert "sjøre" in variants
    assert "skjøre" in confusable.confusions("sjøre", "nb")


def test_a_short_vowel_wants_two_consonants() -> None:
    """The commonest Norwegian spelling error there is, in both directions."""
    assert "hatt" in confusable.confusions("hat", "nb")
    assert "hat" in confusable.confusions("hatt", "nb")


def test_the_first_letter_is_never_doubled() -> None:
    """`ffølge` is a typo rather than a mistake. A child asked to choose between
    `følge` and `ffølge` has learned nothing by getting it right."""
    assert not any(v.startswith("ff") for v in confusable.confusions("følge", "nb"))
    assert not any(v.startswith("bb") for v in confusable.confusions("blir", "nb"))


def test_doubling_happens_between_a_vowel_and_a_vowel_or_the_end() -> None:
    """Doubling marks the vowel in front of it as short, so it means nothing
    before another consonant: *leavinng*, *abbsence* and *menntion* are typos
    wearing a spelling mistake's clothes."""
    assert "leavinng" not in confusable.confusions("leaving", "en")
    assert "abbsence" not in confusable.confusions("absence", "en")
    # And the real thing still works, between two vowels and at the end.
    assert "bananna" in confusable.confusions("banana", "en")
    assert "oness" in confusable.confusions("ones", "en")


def test_a_silent_d_can_be_dropped_anywhere() -> None:
    assert "kal" in confusable.confusions("kald", "nb")
    assert "lan" in confusable.confusions("land", "nb")


def test_a_silent_d_is_only_added_where_norwegian_writes_one() -> None:
    """After a vowel, and at the end of the word or before an `e` ending. The
    old rule put one anywhere and produced *bldir*, *haldvannet* and
    *fordstått*, none of which anyone has written."""
    assert "bldir" not in confusable.confusions("blir", "nb")
    assert "haldvannet" not in confusable.confusions("halvannet", "nb")
    assert "fordstått" not in confusable.confusions("forstått", "nb")
    assert "dennde" not in confusable.confusions("denne", "nb")
    # `hele` -> `helde` is the mistake this rule exists to keep.
    assert "helde" in confusable.confusions("hele", "nb")


def test_english_confusions_are_letter_groups_rather_than_letters() -> None:
    """English spelling is irregular rather than rule-governed, so its table is
    about which letters spell one sound."""
    assert "phone" in confusable.confusions("fone", "en")
    assert "kar" in confusable.confusions("car", "en")


def test_a_variant_is_never_the_word_itself_and_never_empty() -> None:
    for word in ("kjøre", "banana", "a", "gh"):
        variants = confusable.confusions(word, "en") | confusable.confusions(word, "nb")
        assert word not in variants
        assert "" not in variants


def test_neighbours_widen_the_net_but_only_for_finding_real_words() -> None:
    """A substitution the table does not know about still makes a fine
    distractor when it spells something, and a terrible one when it does not.
    So `neighbours` is bigger than `confusions` -- and only ever intersected
    with the lexicon."""
    assert "bok" in confusable.neighbours("bak", "nb")
    assert "bok" not in confusable.confusions("bak", "nb")


# --- picking a distractor --------------------------------------------------


def test_a_real_word_is_preferred_over_an_invention() -> None:
    """Both spellings correct means the only way through is to have heard which
    was said, which is the best question this exercise can ask."""
    words = pool_of("hat", "hatt", "hus", "har", "hos")
    assert confusable.distractor("hat", "nb", words) == "hatt"


def test_a_predicted_mistake_beats_a_coincidence() -> None:
    """`hus`/`hos` is the o/u confusion the table knows about; `hus`/`hun` is a
    letter substitution that happens to spell something. Both are real words,
    and only one is a mistake a child makes."""
    words = pool_of("hus", "hos", "hun", "har")
    assert confusable.distractor("hus", "nb", words) == "hos"


def test_an_invented_misspelling_when_no_real_word_is_near(real: dict) -> None:
    """Against the real lexicon, because this is the half of the generator that
    the lexicon has to be big enough to judge.

    A misspelling has to look like the language, and "looks like the language"
    is measured from the language: on a handful of made-up words the shape table
    has never seen a word end in `pp`, so it would refuse `kjøleskapp` -- which
    is the exact non-word the exercise wants. At five thousand words it has seen
    `opp` and `stopp`, and the same check passes. There is no way to test this
    honestly on a toy pool.
    """
    words = real["nb"]
    other = confusable.distractor("kjøleskap", "nb", words)
    assert other is not None
    assert other not in words.words
    assert words.plausible(other)


def test_an_invented_misspelling_must_look_like_the_language(real: dict) -> None:
    """The shape table is what keeps *sdrategi* and *ldikevel* off the screen."""
    for word in ("strategi", "likevel", "halvannet", "forstått"):
        other = confusable.distractor(word, "nb", real["nb"])
        assert other is not None
        assert real["nb"].plausible(other)


def test_no_askable_word_is_left_without_a_question(real: dict) -> None:
    """A word the generator gives up on is a word the exercise skips, so the
    give-up rate is a number worth pinning: at more than a few per cent, a
    checkpoint's passages could stop yielding a full round.
    """
    for language in ("nb", "en"):
        words = real[language]
        askable = words.askable(set(words.words))
        stuck = [w for w in askable if confusable.distractor(w, language, words) is None]
        assert len(stuck) / len(askable) < 0.1


def test_a_word_with_nowhere_to_go_gets_no_distractor() -> None:
    """None means "do not ask about this word", which the caller has to treat as
    an ordinary answer rather than an error."""
    assert confusable.distractor("ø", "nb", lexicon("ø")) is None


def test_the_same_word_always_yields_the_same_distractor() -> None:
    """An exercise that changed under a reload could not be tested, and a pupil
    could reload until the question got easier."""
    words = pool_of("hat", "hatt", "hus", "hos", "har")
    first = confusable.distractor("hat", "nb", words)
    assert all(confusable.distractor("hat", "nb", words) == first for _ in range(5))


def test_the_harder_distractor_is_chosen_where_there_is_a_choice() -> None:
    """The opening sound is the one a child hears most clearly, so a word that
    differs only later is the better question: `some` beside `same` demands
    attention where `came` beside `same` does not."""
    ranked = sorted(["came", "cane", "some", "sane"], key=confusable.ranked_against("same"))
    assert ranked[:2] == ["sane", "some"]

    # And end to end, where nothing the confusion table predicts is a real word.
    words = pool_of("same", "some", "sane", "dame")
    assert confusable.distractor("same", "en", words) == "sane"


# --- the lexicon -----------------------------------------------------------


def test_the_lexicon_holds_letters_and_nothing_else() -> None:
    assert words_in("Fem katter, 3 hunder!") == {"fem", "katter", "hunder"}


def test_a_shape_seen_often_enough_is_a_shape_of_the_language() -> None:
    words = {f"katt{n}e" for n in "abcdefgh"} | {"kake", "kart", "katt"}
    built = Lexicon.of(words)
    assert "ka" in built.initial
    assert "tt" in built.inner


def test_a_shape_seen_once_is_an_accident_rather_than_a_rule() -> None:
    """A shape table that admits every accident admits every misspelling too."""
    built = Lexicon.of({"zyx", "hus", "hus1"})
    assert "zy" not in built.initial
    assert SHAPE_FLOOR > 1


def test_a_word_too_short_to_have_a_shape_is_waved_through() -> None:
    assert lexicon("hus").plausible("a")


def test_askable_words_are_the_ones_worth_speaking() -> None:
    built = pool_of("hus", "på", "elektrisitetsverk", "katten")
    asked = built.askable({"hus", "på", "elektrisitetsverk", "katten", "ukjent"})
    assert asked == ["hus", "katten"]
    assert MIN_ASKED == 3
    assert MAX_ASKED == 9


def test_askable_is_sorted_rather_than_shuffled() -> None:
    """Set iteration order must not decide which words a checkpoint asks."""
    built = pool_of("hus", "katt", "bil")
    assert built.askable({"hus", "katt", "bil"}) == ["bil", "hus", "katt"]


def test_the_lexicon_is_built_from_every_language_pensum_authors(real: dict) -> None:
    assert {"nb", "nn", "en"} <= set(real)
    assert len(real["nb"].words) > 1000
    assert len(real["en"].words) > 1000


def test_nynorsk_falls_back_to_bokmaal_rather_than_to_nothing(real: dict) -> None:
    """There is no authored nynorsk text yet. An empty lexicon would silently
    turn every distractor into an invention, which is worse than being wrong
    about which målform a word belongs to."""
    assert real["nn"].words == real["nb"].words


# --- the exercise ----------------------------------------------------------


def text(body: str, language: str = "nb") -> ReadingText:
    return ReadingText(
        id="t1",
        goal="KM1",
        language=language,
        title="T",
        body=body,
        difficulty=1,
        source="pensum",
        reviewed=True,
    )


def test_the_youngest_pick_and_the_older_ones_write() -> None:
    assert mode_for(2) == "pick"
    assert mode_for(WRITE_FROM_YEAR) == "write"
    assert mode_for(10) == "write"


def test_a_written_question_shows_nothing_to_choose_between() -> None:
    """Options on a dictation would be the answer, printed."""
    question = question_for("hus", "nb", "write", pool_of("hus", "hos"))
    assert question is not None
    assert question.options == ()
    assert question.answer_index is None


def test_a_picked_question_carries_both_spellings_and_knows_the_right_one() -> None:
    question = question_for("hat", "nb", "pick", pool_of("hat", "hatt", "hus"))
    assert question is not None
    assert set(question.options) == {"hat", "hatt"}
    assert question.options[question.answer_index] == "hat"


def test_a_word_with_nothing_to_confuse_it_with_is_not_asked() -> None:
    """ "Which of these two completely different words did you hear" is a hearing
    test, and this is a spelling exercise."""
    assert question_for("ø", "nb", "pick", lexicon("ø")) is None


def test_the_answer_does_not_always_sit_on_the_same_side() -> None:
    """Otherwise the exercise is "press the left button" and the child is right
    every time without listening."""
    words = pool_of(
        "hat", "hatt", "bak", "bok", "hus", "hos", "tak", "tok", "lat", "latt", "mat", "matt"
    )
    sides = {
        question.answer_index
        for word in ("hat", "bak", "hus", "tak", "lat", "mat")
        for question in [question_for(word, "nb", "pick", words)]
        if question is not None
    }
    assert sides == {0, 1}


def test_a_round_asks_the_words_from_the_passages() -> None:
    passage = text(
        "Katten satt på matta og så på hunden. Hunden så på katten og bjeffet "
        "en gang. Da hoppet katten opp i vinduet og ble sittende der."
    )
    words = pool_of(*passage.word_list, "matte", "hund", "kette")
    built = build_round([passage], after_year=2, language="nb", lexicon=words)
    assert built.mode == "pick"
    assert built.questions
    assert all(question.word in passage.word_list for question in built.questions)


def test_a_round_stops_at_its_length() -> None:
    passage = text(" ".join(f"katt{n}en huset {n}" for n in "abcdefghijklmnopqrstuv"))
    words = pool_of(*passage.word_list)
    built = build_round([passage], after_year=5, language="nb", lexicon=words, length=ROUND_LENGTH)
    assert len(built.questions) == ROUND_LENGTH


def test_a_round_is_not_in_alphabetical_order() -> None:
    """Sorted alphabetically, every round would open with `av`, `and` and `at` --
    duller than the passage and easier than it."""
    passage = text(
        "Anna og Bjørn og Carl og David gikk til elva. Der fant de en fisk og "
        "en frosk og en stein og en pinne som lå i vannet ved bredden."
    )
    words = pool_of(*passage.word_list)
    built = build_round([passage], after_year=5, language="nb", lexicon=words)
    asked = [question.word for question in built.questions]
    assert asked != sorted(asked)


def test_the_same_data_always_builds_the_same_round() -> None:
    passage = text(
        "Katten satt på matta og så på hunden. Hunden så på katten og bjeffet "
        "en gang. Da hoppet katten opp i vinduet og ble sittende der."
    )
    words = pool_of(*passage.word_list)
    rounds = [build_round([passage], after_year=2, language="nb", lexicon=words) for _ in range(3)]
    assert rounds[0] == rounds[1] == rounds[2]


# --- marking ---------------------------------------------------------------


def question(word: str) -> Question:
    return Question(word=word, language="nb")


def test_case_and_surrounding_space_are_forgiven() -> None:
    """Neither is what is being practised, and a tablet keyboard capitalises on
    its own."""
    assert is_correct(question("hus"), "  Hus ")
    assert normalise(" HUS ") == "hus"


def test_a_norwegian_vowel_is_not_a_latin_one() -> None:
    """`å` is not `a`. A dictation that accepted it would be teaching the
    opposite of the lesson."""
    assert not is_correct(question("båt"), "bat")


def test_a_round_is_marked_word_by_word() -> None:
    from pensum.listening.exercise import Round

    built = Round(mode="write", language="nb", questions=(question("hus"), question("båt")))
    result = mark(built, Answers(given=("hus", "bat")))
    assert result.right == 1
    assert result.total == 2
    assert result.score == 0.5
    assert result.marks[1].given == "bat"


def test_finishing_survives_getting_them_wrong() -> None:
    """The badge that cannot mislead, as on the writing screen: a child who
    spelled all of them and missed three still spelled all of them."""
    from pensum.listening.exercise import Round

    built = Round(mode="write", language="nb", questions=(question("hus"), question("båt")))
    assert mark(built, Answers(given=("hos", "bat"))).finished
    assert not mark(built, Answers(given=("hus", "   "))).finished


def test_a_missing_answer_is_wrong_rather_than_a_crash() -> None:
    from pensum.listening.exercise import Round

    built = Round(mode="write", language="nb", questions=(question("hus"), question("båt")))
    result = mark(built, Answers(given=("hus",)))
    assert result.right == 1
    assert not result.marks[1].answered


def test_stars_need_most_of_the_round_right() -> None:
    from pensum.listening.exercise import Round

    built = Round(
        mode="write",
        language="nb",
        questions=tuple(question(w) for w in ("a", "b", "c", "d")),
    )
    assert mark(built, Answers(given=("a", "b", "c", "d"))).stars == 3
    assert mark(built, Answers(given=("a", "b", "c", "x"))).stars == 2
    assert mark(built, Answers(given=("a", "b", "x", "y"))).stars == 1
    assert mark(built, Answers(given=("a", "x", "y", "z"))).stars == 0


def test_an_answer_longer_than_any_word_is_refused() -> None:
    """Somebody testing what the box accepts, rather than a child spelling.

    Bounded in the schema rather than trimmed on the way in: unbounded strings
    would let a request of any size through and into memory, and there is no
    reading of a 5000-character answer that is worth keeping.
    """
    with pytest.raises(ValueError):
        Answers(given=("x" * (MAX_ANSWER + 1),))

    assert Answers(given=("x" * MAX_ANSWER,)).at(0)


def test_more_answers_than_any_round_has_are_refused() -> None:
    with pytest.raises(ValueError):
        Answers(given=tuple(str(n) for n in range(100)))


# --- the library -----------------------------------------------------------


@pytest.fixture(scope="module")
def library() -> ListeningLibrary:
    return ListeningLibrary.of(ItemBank.load(), ReadingLibrary.load())


def test_a_checkpoint_with_passages_has_an_exercise(library: ListeningLibrary) -> None:
    assert library.has_listening("KV1107", 2)
    built = library.round_for("KV1107", 2)
    assert built is not None
    assert built.mode == "pick"
    assert built.language == "nb"
    assert len(built.questions) >= MIN_ROUND


def test_a_checkpoint_with_no_passages_has_no_exercise(library: ListeningLibrary) -> None:
    assert not library.has_listening("KV1021", 2)
    assert library.round_for("KV1021", 2) is None


def test_the_mode_follows_the_checkpoint(library: ListeningLibrary) -> None:
    assert library.round_for("KV1107", 2).mode == "pick"
    assert library.round_for("KV1108", 4).mode == "write"


def test_a_round_is_built_once_and_kept(library: ListeningLibrary) -> None:
    """The subject page asks whether an exercise exists at all, and can only
    find out by building one."""
    assert library.round_for("KV1107", 2) is library.round_for("KV1107", 2)


def test_the_english_exercise_speaks_english(library: ListeningLibrary) -> None:
    built = library.round_for("KV1030", 2)
    assert built is not None
    assert built.language == "en"


# --- the routes ------------------------------------------------------------


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(create_app(Catalogue.load(), settings=Settings()))


def test_the_page_carries_the_words_and_the_mode(client: TestClient) -> None:
    response = client.get("/nb/klasse/1/NOR01-08/lytting")
    assert response.status_code == 200
    assert 'data-mode="pick"' in response.text
    assert 'data-language="nb"' in response.text
    assert response.text.count("listening-question") >= MIN_ROUND


def test_the_choosing_page_shows_two_spellings_per_word(client: TestClient) -> None:
    body = client.get("/nb/klasse/1/NOR01-08/lytting").text
    assert body.count("data-value=") == 2 * body.count("data-word=")


def test_the_writing_page_shows_no_spellings_at_all(client: TestClient) -> None:
    """A box, and no letters to copy from. Options here would be the answer."""
    body = client.get("/nb/klasse/4/NOR01-08/lytting").text
    assert 'data-mode="write"' in body
    assert "listening-option" not in body
    assert "listening-input" in body


def test_the_page_says_it_needs_a_script(client: TestClient) -> None:
    """There is nothing to fall back to: the browser is what says the word."""
    body = client.get("/nb/klasse/1/NOR01-08/lytting").text
    assert "noscript" in body


def test_a_checkpoint_with_no_passages_is_a_404(client: TestClient) -> None:
    assert client.get("/nb/klasse/7/NAT01-05/lytting").status_code == 404


def test_an_unknown_locale_is_a_404(client: TestClient) -> None:
    assert client.get("/fr/klasse/1/NOR01-08/lytting").status_code == 404


def words_on(client: TestClient, url: str) -> list[str]:
    import re

    return re.findall(r'data-word="([^"]+)"', client.get(url).text)


def test_a_perfect_round_earns_every_star(client: TestClient) -> None:
    words = words_on(client, "/nb/klasse/1/NOR01-08/lytting")
    response = client.post("/nb/klasse/1/NOR01-08/lytting/svar", json={"given": words})
    assert response.status_code == 200
    assert response.text.count("star--on") == 3
    assert "badge--finished" in response.text


def test_an_empty_round_earns_none(client: TestClient) -> None:
    response = client.post("/nb/klasse/1/NOR01-08/lytting/svar", json={"given": []})
    assert response.status_code == 200
    assert response.text.count("star--on") == 0
    assert "badge--finished" not in response.text


def test_the_result_shows_the_right_answers_as_well_as_the_wrong_ones(
    client: TestClient,
) -> None:
    """A dictation marked only where it failed is a list of the child's
    mistakes, which is the least useful way to print eight words they mostly
    spelled correctly."""
    words = words_on(client, "/nb/klasse/4/NOR01-08/lytting")
    given = [words[0]] + ["xxx"] * (len(words) - 1)
    body = client.post("/nb/klasse/4/NOR01-08/lytting/svar", json={"given": given}).text
    assert all(word in body for word in words)
    assert "listening-mark--wrong" in body


def test_the_server_marks_the_questions_it_set(client: TestClient) -> None:
    """The round is rebuilt here rather than posted back, so a request cannot
    name its own easier questions."""
    words = words_on(client, "/nb/klasse/4/NOR01-08/lytting")
    body = client.post(
        "/nb/klasse/4/NOR01-08/lytting/svar", json={"given": ["a"] * len(words)}
    ).text
    assert all(word in body for word in words)


def test_the_subject_page_links_to_the_exercise_where_passages_exist(
    client: TestClient,
) -> None:
    assert "/lytting" in client.get("/nb/klasse/1/NOR01-08").text


def test_the_subject_page_stays_quiet_where_none_do(client: TestClient) -> None:
    assert "/lytting" not in client.get("/nb/klasse/7/NAT01-05").text
