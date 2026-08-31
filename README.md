# Pensum

What pupils in Norwegian *grunnskole* are expected to master by the end of each
*klasse*, and quizzes to check whether they do.

Built on **LK20**, the national curriculum, taken directly from
[Udir's open Grep API](https://data.udir.no/kl06/v201906/). Every competence goal
shown here is the official wording, in the *målform* Udir published it in, and
links back to its source record.

> **Pensum is an unofficial study aid.** It is not an assessment instrument, not
> a substitute for a teacher's judgement, and not affiliated with or endorsed by
> Utdanningsdirektoratet. "Passing 2. klasse" here is a friendly proxy, not a
> verdict — see [Honest limits](#honest-limits).

## Norsk

Pensum viser hva elever i norsk grunnskole skal mestre etter hvert hovedtrinn, og
gir quizer for å øve. Innholdet bygger på LK20-kompetansemålene fra
Utdanningsdirektoratet, hentet direkte fra det åpne Grep-APIet. Bokmål er
hovedspråket i grensesnittet; engelsk er også tilgjengelig.

Pensum er et uoffisielt hjelpemiddel, ikke et vurderingsverktøy, og er ikke
tilknyttet Utdanningsdirektoratet.

## How the curriculum is modelled

Udir's own hierarchy, kept deliberately intact so re-verification stays trivial:

```
Subject (læreplan)        MAT01-06, one revision of one subject
└── GoalSet               "etter 2. trinn" — a checkpoint
    └── Goal              a single kompetansemål, with a stable KM code
```

**LK20 defines checkpoints, not years.** Most subjects set goals after 2., 4.,
7. and 10. trinn; matematikk uniquely defines every trinn. Pensum invents no
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
docker run -p 8000:8000 ghcr.io/brujoand/pensum:1.0.0
```

No credentials needed — the image is public, like the repo. It carries the
curriculum baked in and needs no network access, no API key and no
configuration. Optional sign-in and score history are the one exception, and
they stay off until configured: see [Accounts and score
history](#accounts-and-score-history).

There is deliberately **no `latest` tag**. A deployment should name the version
it wants; a moving tag makes that impossible to do honestly. Published tags are
`{major}.{minor}.{patch}`, `{major}.{minor}`, `{major}` and the full commit sha.
Releases are cut from Conventional Commits — see
[releases](https://github.com/brujoand/pensum/releases) for what each version
changed.

The running app reports its version at `/healthz`. An image that says `dev` was
built outside the release pipeline.

## Accounts and score history

**Off unless you turn it on.** Run the image as above and Pensum has no accounts,
writes nothing to disk, and forgets every quiz the moment the tab closes. Point
it at an OIDC provider and two things become possible: a pupil can sign in, and
an adult in a nominated group can see how the signed-in pupils have done.

Four properties hold whenever it *is* configured, and they are enforced in code
rather than documented as intent:

- **Signing in is optional, always.** Every quiz works signed out, and an
  anonymous attempt is never recorded — there is nobody to record it against.
  There is no page on the site that requires an account except the admin ones.
- **Only finished quizzes are kept.** An abandoned attempt is not a result and
  leaves nothing behind.
- **A summary, not a transcript.** What is stored is the checkpoint, the score
  and the per-goal tally the pupil's own result page shows. Not which answer was
  given to which question. The tally is what an adult can act on; a log of a
  seven-year-old's individual mistakes is not.
- **The pupil is told.** A signed-in pupil's result page says their score was
  saved and that an adult with access can see it.

Nothing is recorded until **both** switches are on: an OIDC client *and* a
database path. Sign-in without a database is still a site that forgets.

### Configuring it

| Variable | What it does |
|---|---|
| `PENSUM_OIDC_ISSUER` | Provider base URL, e.g. `https://id.example.com`. Discovery is read from `/.well-known/openid-configuration`. |
| `PENSUM_OIDC_CLIENT_ID` | The client you registered for Pensum. |
| `PENSUM_OIDC_CLIENT_SECRET` | Its secret. |
| `PENSUM_BASE_URL` | Pensum's own public origin, e.g. `https://pensum.example.com`. Required behind a TLS-terminating proxy — the redirect URI is built from it. |
| `PENSUM_ADMIN_GROUP` | Group whose members may read other people's scores. Default `pensum-admins`. |
| `PENSUM_DATABASE_PATH` | SQLite file for finished attempts, e.g. `/data/pensum.db`. Unset means nothing is recorded. |
| `PENSUM_SESSION_SECRET` | Signs the login cookie. Generated per process when unset, so a restart signs everyone out. |

Sign-in needs all three OIDC values; any fewer and the feature stays off rather
than half-on.

```bash
docker run -p 8000:8000 \
  -e PENSUM_OIDC_ISSUER=https://id.example.com \
  -e PENSUM_OIDC_CLIENT_ID=pensum \
  -e PENSUM_OIDC_CLIENT_SECRET=... \
  -e PENSUM_BASE_URL=https://pensum.example.com \
  -e PENSUM_ADMIN_GROUP=pensum-admins \
  -e PENSUM_DATABASE_PATH=/data/pensum.db \
  -e PENSUM_SESSION_SECRET=... \
  -v pensum-data:/data \
  ghcr.io/brujoand/pensum:1.0.0
```

The container runs as uid 65532, so the mounted volume has to be writable by it.
Without the volume the history is real but lasts until the container is replaced.

### On the provider side

Register a confidential client with:

- redirect URI `https://pensum.example.com/auth/callback` — one, exactly
- scopes `openid profile email groups`
- PKCE (S256) — Pensum always sends a challenge

Then make a group matching `PENSUM_ADMIN_GROUP` and put the adults in it.
Membership is read from the `groups` claim, so granting or revoking admin is done
in the provider and never needs Pensum redeployed. It takes effect when the
person's session next refreshes, not instantly — the group list is read from the
signed login cookie rather than from the provider on every page load.

Any OIDC provider emitting a `groups` claim works; pocket-id is what it is
developed against. For providers that expose groups only from `/userinfo`, Pensum
falls back to asking there once, at sign-in.

### What an admin sees

`/{locale}/admin` lists everyone who has finished at least one quiz while signed
in: name, how many quizzes, an average weighted by question rather than by quiz,
and the date of the last one. Each row opens that pupil's history, with every
attempt broken down by competence goal.

The pages are read-only. There is nothing there to re-grade or delete a child's
record with — for that, the database is one SQLite file and `sqlite3` is a better
tool than a web form anyone can misclick.

## Reading aloud

Norsk and engelsk carry a second exercise: a passage to read out loud. Pensum
times the reading and, when the deployment has speech models, checks it against
the printed text and reports **correct words per minute** — words heard in the
order they were printed, divided by the time spent reading. Plain words per
minute rewards reading fast by skipping, which is the opposite of the point.

The passage is chosen per checkpoint and is ours, like the quiz questions: LK20
is quoted verbatim elsewhere, and nothing a pupil reads here is curriculum text.

### What a pupil is told

A band, never a threshold — for example *30–60 correct words a minute after 2.
trinn* — followed by the caveat that comes with it. **LK20 sets no words-per-
minute figure and Udir publishes no national norm for reading speed**, so every
band in `data/reading/norms.yaml` is Pensum's own guideline. The schema makes a
band without a cited source impossible to load, and the result page renders the
source's caveat next to the number every time. Reading speed varies enormously
between children who all read perfectly well, and the wording says so.

### Owning the screen

Starting a reading takes over the page. The header, the breadcrumb, the notes
and the footer go; the passage grows to fill the screen with a progress bar and
a clock above it, and the browser is asked for fullscreen on top. `Escape` ends
the reading. None of it is required — with JavaScript off the passage is simply
a passage to read aloud, which is the exercise anyway.

**Words light up as they are read.** While the reading is going, audio is sent
to Pensum in two-second slices; each one is transcribed and matched against the
next stretch of the passage, and the highlight moves forward to wherever the
words were recognised. It runs a beat behind and it only ever moves forward — a
highlight that jumps backwards mid-sentence is worse than one that lags. **The
live pass never touches the score.** It sees eight-second windows with no idea
what came before; the result comes from one pass over the whole recording when
the reading ends.

**Then it plays back.** Whisper reports when each word was heard, so the passage
lights up again at the times the pupil actually read it, with unrecognised words
marked as they pass. A reading nobody listened to replays at an even pace and
the page says that is what it is doing, rather than implying a recording it
never made.

### What it celebrates

Four rewards, and they are not equally defensible:

- **Finishing the passage.** The one badge that cannot mislead: a slow reader
  who reaches the last word earns exactly what a fast one earns.
- **Reaching the band.** Awarded for reaching *or passing* the range for the
  trinn, never for landing inside it — rewarding only the middle would turn a
  guideline into a target with a penalty on both sides.
- **Stars, from accuracy.** Thresholds are deliberately forgiving, and the
  sentence explaining that the recogniser mishears children, dialects and
  second-language speakers is rendered directly underneath rather than in a
  footnote. A reading nobody listened to shows no stars at all.
- **Personal bests and a daily streak.** Computed in the pupil's own browser
  from `localStorage` and never sent anywhere, so Pensum still does not know
  that anyone read the same passage twice.

### Two modes, and only one of them needs configuration

| | Default image | With speech models |
|---|---|---|
| Passage shown, screen taken over | yes | yes |
| Reading timed | yes | yes |
| Audio recorded | **no** | in memory, for the length of the reading |
| Words light up live | no | yes |
| Read against the text | no | yes — accuracy, and which words were not heard |
| Replay | even pace, labelled as such | the real times each word was read |
| Stars | no | yes |

The default is the left column. There is no microphone prompt, no recording and
no upload; the page times the reading and says outright that nobody checked it.

Turning on the right column takes a model directory:

```bash
bin/fetch_speech_models                       # ~1 GB, once
PENSUM_SPEECH_MODEL_DIR=/models docker run \
  -e PENSUM_SPEECH_MODEL_DIR=/models \
  -v "$PWD/data/speech:/models:ro" \
  -p 8000:8000 ghcr.io/brujoand/pensum:1.0.0
```

The image needs speech support compiled in for this to do anything — build it
with `--build-arg WITH_SPEECH=1`, which adds the `speech` extra. The published
image is built without it.

`PENSUM_SPEECH_LIVE=0` keeps the checking and drops the live highlight. That is
the dial to reach for first under load: lighting words up costs a transcription
every two seconds per pupil reading, on top of the single pass that produces the
score. In-flight readings hold their audio in memory, so concurrency is capped
(32 at once) rather than allowed to grow.

### Where the audio goes

Nowhere. A recording is captured in the page, posted to Pensum's own origin in
slices, held in memory only while the reading is in progress, transcribed by a
model on local disk, and dropped the moment the reading is scored — not on a
timer. It is never written to disk, never sent to a speech API,
never attached to a pupil — signed in or not — and never kept. Transcription is
`faster-whisper` running in-process, so a container with models mounted still
makes no outbound request.

The browser's own `SpeechRecognition` API would have been free and would have
shipped a child's voice to a third-party service. That is why it is not used.

**Vosk is not used either, for a duller reason: it publishes no Norwegian
model.** Some three dozen languages, Swedish the only Nordic one. It could have
served engelsk and nothing else.

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
uv run pensum-ingest --check-drift      # what has changed upstream? (read-only)
uv run pensum-ingest --as-of 2026-08-01 # re-vendor; writes data/curriculum/
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
  can check. Pensum marks those as not assessable and shows them without
  quizzing them, rather than inventing a question that misrepresents the goal.
  Coverage is therefore uneven by design.
- **Quiz questions are drafted with an LLM and reviewed by hand.** Questions are
  committed as readable YAML so every one of them is reviewable, and the released
  build serves only reviewed items. The curriculum text itself is never
  generated — it is quoted verbatim from Udir.
- **A reading speed is a guideline, and a rough one.** No words-per-minute
  figure appears anywhere in LK20, and Udir publishes no national norm for
  reading speed, so the bands in `data/reading/norms.yaml` are Pensum's own and
  are shown with that stated. A speech recogniser also mishears children,
  dialects and second-language speakers more than it mishears anyone else, so a
  word listed as "not heard" may be the machine's mistake rather than the
  pupil's — which the result page says as well.
- **The reading screen is gamified, and two of its rewards are in tension with
  everything above.** Stars come from a recogniser that is least accurate for
  the pupils they would most discourage, and rewarding a words-per-minute band
  makes a target of a range the norms file explicitly says is not one. They are
  built the least harmful way we could — forgiving thresholds, the caveat next
  to the stars, credit for reaching the band rather than for landing inside it,
  no stars at all when nothing was listened to — but the tension is real and is
  recorded here rather than smoothed over.
- **No accounts and no analytics unless a deployment adds them.** Out of the
  box Pensum stores nothing about who is using it: quiz progress lives in memory
  for the length of a session and is gone afterwards, and there is no third-party
  script on any page in any configuration. A deployment can enable sign-in and
  keep a score history — see [Accounts and score
  history](#accounts-and-score-history) for exactly what that stores and what it
  still refuses to. Reading aloud is the one feature that handles audio, and it
  keeps none of it: see [Where the audio goes](#where-the-audio-goes). If you
  run the published image with no environment set, none of it applies to you.

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

Two conditions of that licence shape how Pensum is built, not just how it is
credited:

- **The data must not be presented in a misleading or distorted manner.** So
  competence-goal text is shown verbatim, never paraphrased or summarised, and
  is always visually distinct from quiz questions — which are ours, not Udir's.
  Each goal links to its official source record so any claim we make about the
  curriculum can be checked against it.
- **Udir's logo may not be used** without a separate agreement. Pensum does not
  use it, and displays nothing implying official endorsement.
