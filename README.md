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

Running it locally:

```bash
bin/run_local              # build the image and run it on :8000
bin/run_local --native     # run from source with reload, no Docker
bin/run_local --no-build   # reuse the image you already built
bin/run_local --port 9000
```

### Keeping the curriculum current

Udir revises curricula on their own schedule, and a revision **renumbers every
competence goal** — which orphans every quiz item keyed to the old codes. The
committed data gives no sign of this by itself: it stays valid-looking
indefinitely. So noticing is automated.

```bash
uv run froken-ingest --check-drift      # what has changed upstream? (read-only)
uv run froken-ingest --as-of 2026-08-01 # re-vendor; writes data/curriculum/
```

`--check-drift` reads only Udir's index endpoint, so it is cheap enough to run
weekly — which it does, via `.github/workflows/curriculum-drift.yml`, opening an
issue when something needs attention. It reports four things: a subject revised
upstream, one expiring or expired, one withdrawn, and one **superseded by a newer
revision**.

That last check is inferred from a higher revision number appearing upstream
rather than read directly, because the index endpoint carries no `erstattes-av`.
It has to be: NOR01-07 was replaced by NOR01-08 while carrying no expiry date at
all, so a newer revision in the index is the *only* signal that the vendored copy
is stale.

Re-ingesting is deliberately a human decision, not an automated PR — a revision
reworks goal wording and moves checkpoints, so items need re-authoring rather
than a rubber-stamped merge. The output is sorted and stable, so the change
arrives as a readable diff, and that diff is the review artifact.

Udir is the single source of truth: nothing under `data/curriculum/` is
hand-authored or hand-edited, so the whole catalogue can always be re-derived
and re-verified against the official source.

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

The code is MIT — see [LICENSE](LICENSE).

The curriculum data under `data/curriculum/` is not ours. It is redistributed
under **NLOD**, the Norwegian Licence for Open Government Data, which permits
copying, redistribution, modification and commercial use, and requires the
source to be credited:

> Inneholder data under [NLOD](https://data.norge.no/nlod/no),
> tilgjengeliggjort på [data.udir.no](https://data.udir.no).
>
> Contains data under [NLOD](https://data.norge.no/nlod/en), made available on
> [data.udir.no](https://data.udir.no).

Two conditions of that licence shape how Frøken is built, not just how it is
credited:

- **The data must not be presented in a misleading or distorted manner.** So
  competence-goal text is shown verbatim, never paraphrased or summarised, and
  is always visually distinct from quiz questions — which are ours, not Udir's.
  Each goal links to its official source record so any claim we make about the
  curriculum can be checked against it.
- **Udir's logo may not be used** without a separate agreement. Frøken does not
  use it, and displays nothing implying official endorsement.
