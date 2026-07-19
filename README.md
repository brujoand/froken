# Frøken

What pupils in Norwegian *grunnskole* are expected to master by the end of each
*klasse*, and quizzes to check whether they do.

Built on **LK20**, the national curriculum, taken directly from
[Udir's open Grep API](https://data.udir.no/kl06/v201906/). Every competence goal
shown here is the official wording, in the *målform* Udir published it in, and
links back to its source record.

> **Frøken is an unofficial study aid.** It is not an assessment instrument, not
> a substitute for a teacher's judgement, and not affiliated with or endorsed by
> Utdanningsdirektoratet. "Passing 2. klasse" here is a friendly proxy, not a
> verdict — see [Honest limits](#honest-limits).

## Norsk

Frøken viser hva elever i norsk grunnskole skal mestre etter hvert hovedtrinn, og
gir quizer for å øve. Innholdet bygger på LK20-kompetansemålene fra
Utdanningsdirektoratet, hentet direkte fra det åpne Grep-APIet. Bokmål er
hovedspråket i grensesnittet; engelsk er også tilgjengelig.

Frøken er et uoffisielt hjelpemiddel, ikke et vurderingsverktøy, og er ikke
tilknyttet Utdanningsdirektoratet.

## How the curriculum is modelled

Udir's own hierarchy, kept deliberately intact so re-verification stays trivial:

```
Subject (læreplan)        MAT01-06, one revision of one subject
└── GoalSet               "etter 2. trinn" — a checkpoint
    └── Goal              a single kompetansemål, with a stable KM code
```

**LK20 defines checkpoints, not years.** Most subjects set goals after 2., 4.,
7. and 10. trinn; matematikk uniquely defines every trinn. Frøken invents no
per-year split — it uses Udir's own `benyttes-paa-aarstrinn`, which states which
school years each checkpoint covers. So a pupil in 1. klasse sees the 2. trinn
goals labelled as what they are working *towards*, and a 2nd-grader sees the same
goals labelled as what they should now master.

**Curricula are revised, and revisions renumber everything.** A goal's KM code is
not stable across revisions — when MAT01-05 became MAT01-06, every code changed
and no goal kept its old identifier. The ingest therefore selects curricula by
*validity date*, never by a hardcoded list, so a revision is a re-run rather than
a rewrite.

## Running it

```bash
docker run -p 8000:8000 ghcr.io/brujoand/froken
```

The image carries the curriculum baked in. It needs no network access, no API
key, and no configuration.

## Development

```bash
mise install
uv sync --group dev --group ingest
uv run pytest
uv run pre-commit run --all-files
```

Refreshing the curriculum from Udir:

```bash
uv run froken-ingest --as-of 2026-08-01     # writes data/curriculum/
```

The output is committed, sorted, and stable, so a curriculum change arrives as a
readable diff — that diff is the review artifact.

## Honest limits

- **Not every competence goal can be tested in writing.** Many are framed as
  *utforske*, *samtale om*, *delta i* — things a pupil does, not things a quiz
  can check. Frøken marks those as not assessable and shows them without
  quizzing them, rather than inventing a question that misrepresents the goal.
  Coverage is therefore uneven by design.
- **Quiz questions are drafted with an LLM and reviewed by hand.** Questions are
  committed as readable YAML so every one of them is reviewable, and the released
  build serves only reviewed items. The curriculum text itself is never
  generated — it is quoted verbatim from Udir.
- **No accounts, no tracking, no analytics.** Frøken stores nothing about who is
  using it. Quiz progress lives in memory for the length of a session and is
  gone afterwards.

## Licence

MIT — see [LICENSE](LICENSE). The curriculum text is © Utdanningsdirektoratet and
is redistributed here under the terms of their open data publication; each goal
links to its official source record.
