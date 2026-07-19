---
name: write-quiz-items
description: Author Frøken quiz items for one LK20 competence goal set (kompetansemålsett), producing a validated YAML file under data/items/. Use when asked to write, draft or extend quiz questions for a subject and checkpoint — e.g. "write items for NAT01-05 KV81", "add questions for norsk 4. trinn", "fill in the missing goal sets".
---

# Writing quiz items

You are writing practice questions for Norwegian schoolchildren, keyed to
official LK20 competence goals. The questions are **ours**; the goals are
Udir's, quoted verbatim elsewhere and never rewritten.

Your output is one YAML file: `data/items/<SUBJECT>/<GOALSET>.yaml`.

## Before writing anything

Read the goals you are writing for. Never work from the goal codes alone:

```bash
uv run python -c "
from froken.catalogue.loader import Catalogue
gs = Catalogue.load().subject('MAT01-06').goal_set('KV1021')
print(gs.code, 'after year', gs.after_year, '| klasse', gs.applies_to_years)
for g in gs.goals:
    print(f'  {g.code}: {g.text.get(\"nob\")}')
"
```

Read `data/items/MAT01-06/KV1021.yaml` too. It is the hand-written reference
set and it defines the register — match it rather than inventing a new voice.

## The bar

**Every goal must be either tested or explicitly excused.** Validation enforces
it, so a goal you silently skip fails the build. That is deliberate: an omitted
goal reads as an oversight, whereas a recorded one reads as a decision.

**Refusing is often the right answer.** Many kompetansemål describe things a
pupil *does* — *utforske*, *samtale om*, *delta i*, *lage*, *reflektere over*.
No written question can check those; it can only check something adjacent and
pretend. When that is the case, add the goal to `not_assessable` with a reason
of at least a sentence, and write no items for it.

Do not pad. Two good questions beat five where three are strained. One or two
items per assessable goal is normal; three only where the goal genuinely has
that much range.

**A question must test the goal, not the reading.** If a pupil who knows the
material could still get it wrong because the sentence was long, rewrite it.

## Register, by year

| Years | How to write |
|---|---|
| 1–4 | One short sentence. Concrete nouns. No subordinate clauses. Numbers a child can hold in their head. `multiple_choice` almost always — reading and typing are still hard work. |
| 5–7 | Two sentences at most. Everyday contexts. |
| 8–10 | Normal prose, still plain. |

Contexts should be ordinary and Norwegian: school, home, outdoors, sport, shops,
weather, animals. Avoid anything assuming money to spend, foreign travel, a
particular family shape, a religion, or a body type. A pupil should never meet a
question that quietly excludes them.

Bokmål is the original; write it first and translate to English. Never translate
a competence goal — Udir's own wording is used for that, and paraphrasing a
legal text would be a correctness bug.

## Subject-specific care

**KRLE and samfunnsfag.** Test knowledge *about* religions, worldviews and
society — never adherence to one, and never whether the pupil holds an opinion.
"Hva feirer muslimer under id?" is a fact. "Hvorfor er det viktig å…" is not a
quiz question. Never present a contested political, moral or theological claim
as having one correct answer; if a goal's substance is the pupil's own reasoning
or discussion, that part is `not_assessable`. Norway's minorities — Sami people,
national minorities, immigrant communities, religious groups — are subjects of
these curricula and will be reading. Write as though they are.

**Engelsk.** The pupil is a Norwegian child learning English, so the `nb` prompt
is the instruction and the English is the material being tested. It is the one
subject where the two locales are not a translation of each other: a vocabulary
item asking for the English word cannot show the answer in its own Norwegian
prompt. Test comprehension, vocabulary and structure — not accent, and not
cultural trivia about English-speaking countries beyond what the goals name.

**Norsk.** Grammar, reading comprehension and vocabulary are testable. Writing,
presenting and discussing are not — those goals belong in `not_assessable`.
Where a goal covers both bokmål and nynorsk, remember the pupil may have either
as their hovedmål; do not assume which.

**Naturfag.** Facts and reasoning are testable; *utforske*, practical
investigation and lab work are not. Keep to settled science: a quiz for children
is not the place for a contested finding, and anything you are unsure of should
not be a question at all.

## Item types

- **`multiple_choice`** — at least 3 options, exactly one `correct: true`.
  Distractors must be plausible but clearly wrong to someone who knows the
  material. No trick questions, no near-identical options.
- **`numeric`** — a single number in `answer`. Set `tolerance` above 0 only for
  estimation or decimals.
- **`short_text`** — one or two words. List **every** reasonable spelling,
  synonym and inflection under `accept`. There is no model grading answers at
  runtime; anything not on the list is marked wrong. Prefer multiple choice
  unless the answer is genuinely a single unambiguous word.

`difficulty` is 1–3 **relative to this checkpoint**. A hard year-2 question is
not a hard year-10 question.

Every `explanation` should teach. A pupil who got it wrong should understand
why, not just be told they were.

## File shape

```yaml
# <Subject>, kompetansemål etter <N>. trinn (<SUBJECT> / <GOALSET>).
#
# Questions here are ours, not Udir's. The competence goals they test are quoted
# verbatim elsewhere; nothing in this file is official curriculum text.
---
subject: MAT01-06
goal_set: KV1021

items:
  - id: KM13228-01          # <goal code>-<two digits>, unique in the file
    goal: KM13228
    type: multiple_choice
    difficulty: 1
    prompt:
      nb: Hvilket tall er et partall?
      en: Which number is an even number?
    choices:
      - id: a
        text:
          nb: "7"
          en: "7"
      - id: b
        text:
          nb: "10"
          en: "10"
        correct: true
      - id: c
        text:
          nb: "13"
          en: "13"
    explanation:
      nb: 10 kan deles i to like grupper på 5. Da er det et partall.
      en: 10 splits into two equal groups of 5, which makes it even.
    reviewed: false

not_assessable:
  - goal: KM13229
    reason: >-
      Asks the pupil to explore numbers and counting through play, nature, art,
      music and children's literature. It describes an activity a teacher sets
      up, not knowledge a question can check.
```

### `reviewed: false`, always

You are generating, not publishing. The released build serves only reviewed
items, and a human flips that flag after reading. **Never write
`reviewed: true`** — that flag is the only thing standing between a draft
question and a child, and setting it yourself would defeat the safeguard rather
than satisfy it. The hand-written reference set is the sole exception, and it
was written by a person.

### YAML traps

All three of these have already broken this repo:

- **No flow mappings for prose.** `{nb: En sirkel, en ball}` parses "en ball" as
  a *key*, because Norwegian text is full of commas. Always use block style.
- **Quote bare numbers** in choice text: `nb: "7"`, not `nb: 7`.
- **Quote anything containing a colon followed by a space.** This bites hardest
  in maths, because Norwegian writes division as `12 : 3 = 4` — and to YAML a
  colon-space inside a plain scalar means "this is a mapping", so the file stops
  parsing. Write `nb: "12 : 3 = 4, fordi 3 × 4 = 12."` with the quotes, or use a
  block scalar:

  ```yaml
  explanation:
    nb: >-
      Vi deler 12 i 3 like grupper. 12 : 3 = 4, fordi 3 × 4 = 12.
  ```

**Run the validator before you report back.** It parses every file, so it
catches all three of these. A file that does not parse is not a finished file,
and "I wrote it but validation failed" is not done.

## Finishing

```bash
uv run python -m froken.items.validate    # must print nothing and exit 0
uv run pytest -q
```

The validator checks the schema, that every `goal` code exists in that goal set,
that ids are unique, and that no goal is unaccounted for. Fix what it reports;
do not work around it.

To see your items in the running app before they are reviewed:

```bash
FROKEN_INCLUDE_UNREVIEWED=1 bin/run_local --native
```

## Report back

State: how many items per goal, which goals you marked not assessable and why,
and anything you were unsure about. Flag any goal where you suspect the question
tests reading rather than the skill — that judgement is worth more than a clean
report.
