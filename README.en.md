# pdfoenix

This project rebuilds **scanned PDFs** into modern, well-typeset **EPUB** files. If you have ever
opened a book published in the last century, you will probably agree that this project is badly
needed. That is exactly what PDF Phoenix (pdfoenix) stands for.

When designing the project, I decided that there is no need to reproduce the original layout
pixel for pixel. A reprint can take some liberties, so the transcription uses modern typesetting
conventions. The content, however, is never distorted: we do our best to keep text, formulas,
figures, and tables accurate. See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design.

[简体中文](README.md) · **English**

[Features](#features) · [Installation](#installation) · [Quick Start](#quick-start) ·
[Command Handbook](#command-handbook) · [Configuration](#configuration) · [FAQ](#faq)

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-8A2BE2)
![pandoc](https://img.shields.io/badge/pandoc-EPUB-7a4f1d)
![status](https://img.shields.io/badge/status-alpha-orange)
![version](https://img.shields.io/badge/version-0.4.0-blue)
![license](https://img.shields.io/badge/license-AGPL--3.0--or--later-green)

---

## What it is

`pdfoenix` is a command-line pipeline that turns a **scanned PDF** (especially a math-heavy old
book) into a **modern EPUB**. Run it command by command, or script the whole book; interrupted
runs resume where they left off, and every cent spent on models is on the books.

It does not aim for a pixel-perfect copy. It aims for:

- **Absolutely accurate text** — the only hard requirement
- **Faithful formulas** — LaTeX, with the original equation numbers preserved
- **Addressable objects** — every figure, table, and formula can be located and re-checked
- **Accountable cost** — every model call is recorded, so cost/quality experiments are possible

> The project is in **alpha**: the pipeline can already produce complete books, but formatting
> conventions and edge cases are still settling down. Issues welcome.

## Features

| Feature                 | Description                                                                                                         |
|-------------------------|---------------------------------------------------------------------------------------------------------------------|
| **Deterministic first** | Rendering, scouting, merging, checking, and building are plain scripts; LLMs only transcribe and verify page images |
| **Files as state**      | Every intermediate artifact, registry, and log is persisted, so runs resume after interruption                      |
| **Page anchors**        | Transcription writes `<!-- page:N -->`, creating a two-way index between objects and page images                    |
| **Adversarial review**  | Transcription and verification use different models; risky pages are sampled and fully reviewed                     |
| **Pluggable**           | The `Transcriber` protocol, sampling strategy, risk weights, thresholds, and probabilities are all configurable     |

## How it works

```text
PDF
 │
 ├─ transcribe transcribe page by page (auto caches, write gate)
 ├─ merge      merge, anchors/links, crop figures
 ├─ verify     another model reviews a sample of pages
 ├─ check      local rules + math gate (`--all` audits batches/figures/progress)
 ├─ build      pandoc assembles the EPUB (no content validation)
 └─ inspect    check the EPUB structure
```

## Installation

### Requirements

| Dependency                       | Purpose                  | Requirement               |
|----------------------------------|--------------------------|---------------------------|
| Python                           | run the pipeline         | 3.12+                     |
| [uv](https://docs.astral.sh/uv/) | environment & deps       | latest                    |
| pandoc                           | build the EPUB           | 3.x                       |
| Node.js + OpenCode               | call vision models       | Node 20+                  |
| Model gateway                    | transcribe / verify      | any OpenCode provider     |

> The PDF must carry a **text layer** (used to map the chapter structure). For pure image scans, OCR first,
> e.g. `ocrmypdf input.pdf output.pdf`.
>
> On Windows, run `start.sh` inside WSL; macOS and Linux can run it directly.

### One-shot environment setup

```bash
git clone https://github.com/ChenHingO/BooksProject.git
cd BooksProject
./start.sh
source .venv/bin/activate
```

`start.sh` only sets up the environment; it never runs the book pipeline. It installs `uv`,
creates `.venv` (Python 3.12), installs dependencies, and installs `pandoc` and `opencode` if
missing.

The default configuration comes from experiments with MiMo and DeepSeek and tries to get the
highest quality at the lowest possible cost. It is a trade-off, not the quality optimum. If you
want better quality, switch to stronger models or another agent. The project has many
configuration knobs; if you decide to change models, delegate the task to an agent that can edit
the configuration and help you run it.

### Configuring models (config.toml only)

Models, gateways, and combos **all live in each book's `config.toml`** — no other file needs
touching. The default configuration generated by `init` already ships the official MiMo and
DeepSeek gateways plus a ready-to-use combo; you only put keys into the environment:

```bash
export MIMO_API_KEY="your-key"        # transcription side of the default combo
export DEEPSEEK_API_KEY="your-key"    # verification side of the default combo
```

To change gateways, edit `base_url` in `[[expt.provider]]`. Keys travel through environment
variables only (`api_key_env`) and never touch disk. The packaged `presets/opencode.jsonc` is
just the agent-skeleton template; `working/opencode.jsonc` is generated at runtime — an
artifact you never edit.

## Quick start

```bash
# 1. Create a book
pdfoenix init books/my_book \
  --pdf /path/to/book.pdf \
  --title "Book Title" \
  --author "Author" \
  --page-offset 18

# 2. Try one chapter first
pdfoenix -C books/my_book transcribe --chapter ch01
pdfoenix -C books/my_book merge --chapter ch01
pdfoenix -C books/my_book verify --chapter ch01 --full
pdfoenix -C books/my_book check --all
pdfoenix -C books/my_book build

# 3. Once satisfied, run the whole book
pdfoenix -C books/my_book transcribe
pdfoenix -C books/my_book merge
pdfoenix -C books/my_book verify
pdfoenix -C books/my_book check --all
pdfoenix -C books/my_book build
pdfoenix -C books/my_book inspect
```

The artifact lands in `books/my_book/build/book.epub`.

No book at hand? The repository includes a **copyright-free, no-API-key**
synthetic example:

```bash
pdfoenix -C examples/sample_book check
pdfoenix -C examples/sample_book build --out sample.epub
```

You can also generate a synthetic PDF with a text layer for testing
`init`/transcription:

```bash
python examples/make_sample_pdf.py /tmp/pdfoenix-sample.pdf
```

### Optional: cover

If you have a cover, just drop it into the book's `source/` directory:

```text
books/my_book/source/cover.jpg
```

`build` automatically uses `source/cover.*` as the EPUB cover; no configuration needed.
`.jpg`, `.jpeg`, `.png` and friends work. Keep exactly one cover file; the first in sort order
wins.

## Command handbook

### Uniform rules (read this first)

Every command obeys four rules. Once you know them, `--help` is self-explanatory.

**1. Selecting the book: `-C / --book`**

`-C` (same as `--book`) selects **the book directory to operate on**; the default is `.`:

```bash
pdfoenix -C books/my_book check        # operate on any book from anywhere
cd books/my_book && pdfoenix check     # equivalent
```

**2. Scope: `--chapter` / `--pages`**

Scope options do exactly one thing: **restrict the work to the given scope and touch nothing
outside it**.

| Option             | Meaning           | Commands                        |
|--------------------|-------------------|---------------------------------|
| `--chapter ch09`   | only this chapter | `transcribe`, `merge`, `verify` |
| `--pages 19-30,40` | only these pages  | `transcribe`, `verify`          |

`merge` only has `--chapter`: its unit of work is "one chapter → section files", and slicing by
page would break registry consistency. For `verify`, `--pages` bypasses sampling (verify exactly
those pages) while `--chapter` restricts sampling/full verification to one chapter.

**3. Idempotency and `--force`**

All stages are **idempotent**: they skip work units already recorded as done, and rerunning the
same command resumes after an interruption. `--force` has exactly one meaning — **redo completed
units** (including refreshing those units' cached inputs, such as page images):

| Command      | What `--force` redoes                               |
|--------------|-----------------------------------------------------|
| `init`       | overwrite existing scaffold files                   |
| `transcribe` | redo existing batches                               |
| `verify`     | redo verified pages (incl. 300 dpi image re-render) |
| `expt run`   | re-run finished experiment cells                    |

**4. Exit codes and the read/write boundary**

| Code | Meaning                                           | Typical commands               |
|------|---------------------------------------------------|--------------------------------|
| 0    | success                                           | all                            |
| 1    | ran fine but found issues (checks failed / cells) | `check`, `inspect`, `expt run` |
| 2    | usage error or runtime error                      | all                            |

Read-only commands (**never write files, never spend money**): `watch`, `report cost|risk`,
`inspect`, `expt show`. `report quality` creates the experiment workspace and writes
`expt/report.md`. `check` applies safe MathML dialect rewrites in place
(`\Bigm` → `\Big`, doubled backslashes); everything else is report-only.
Only two commands call models: `transcribe` and `verify` (plus `expt run`).

> Every command supports `pdfoenix <command> --help` for the full option list.

### Pipeline commands (in execution order)

#### `pdfoenix init [TARGET] [--pdf FILE] [--title T] [--author A] [--page-offset N] [--force]`

Scaffolding: directory layout, `config.toml` (only values that differ from the defaults),
presets, empty state registries. `TARGET` is the book directory (falls back to `-C`);
`--page-offset` encodes `PDF page = printed page + offset`. On an existing directory,
`init` refreshes the text presets (task books, pricing, config reference) but leaves
`config.toml`, the manuscript, state and CSS untouched. Model combos are written by hand
in `config.toml` (see [Configuration](#configuration)).

#### `pdfoenix transcribe [--chapter chNN] [--pages SPEC] [--force]`

Chapter-layout scouting (`state/scout.json`) and page rendering (`working/pages/`) are internal
caches: `transcribe`/`verify` rebuild them on demand, so they are not separate commands.

Transcribe in batches → `working/batches/chNN/pXXX-YYY.md`. Batch size comes from
`pipeline.batch_pages`; every call's tokens/cost go to `logs/pipeline.db`; progress goes to
`state/progress.json` (the `watch` feed).

**Write gate**: each batch is validated in memory before it hits disk (all page anchors present,
anchored on their own lines, no duplicates, no restart fragments, no out-of-range anchors). A bad
window is retried (`transcriber.attempts`); if it still fails the batch is **not written**, the
reason goes into `state/progress.json`, and the command exits nonzero. Corrupt transcriptions
never enter the corpus. `--rescan` rebuilds the chapter-layout cache and `--rerender` re-renders
the selected page images first (escape hatches for the two internal caches).

#### `pdfoenix merge [--chapter chNN] [--no-assets] [--recrop]`

Merge batches, split into sections, register objects → `manuscript/chNN/`, updating
`state/{formulas,figures,tables}.json`. Page anchors are resolved and stripped from the prose;
re-merging first drops the chapter's previous contributions, so nothing is registered twice.

Merge also crops figures (`figures/figX-Y.png`) — the registry and the images are one object
layer. `--no-assets` registers only; `--recrop` overwrites existing crops; `--chapter` crops only
that chapter's figures.

Merge also materializes cross-reference targets: theorem-like statements, figures and
bibliography entries get `<span id="thm-…">` / `<span id="fig-…">` / `<span id="ref-…">`
anchors, and dangling links are rewritten to case-insensitive, `lemma-` aliased, parent-section
or fuzzy bibliography targets. Manual crop boxes survive re-merging.

#### `pdfoenix verify [--chapter chNN] [--pages SPEC] [--full] [--force] [--audit off|dry|apply]`

Sampled verification: pages are sampled by risk (see [Configuration](#configuration)), and a
different model reviews the full page. Corrections go to `state/corrections.json` and the cost
ledger.

- `--full` skips sampling and verifies every candidate page; `--pages` verifies explicit pages;
- Verification runs in chunks of `pipeline.verify_chunk_size` pages (default 5). Each chunk is
  an independent call with its own retries (`transcriber.attempts`, default 3;
  `transcriber.retry_delay` seconds of linear backoff);
- A chunk persists its corrections and page progress as soon as it succeeds, so a rerun only
  covers the remaining chunks; `watch` shows `done/total pages`, running chunks and failures;
- **Run `merge` first**: sampling risk comes from `state/formulas.json`, and `--audit` scans
  `manuscript/`. Without a merge the sample degrades to blind sampling and the audit finds no
  files;
- `--audit` controls the "global audit" (grep the whole book for the same error pattern):
  `dry` lists hits (default), `apply` rewrites them, `off` skips. The default can also be
  disabled via `review.global_audit`.

#### `pdfoenix check [--all]`

Local rule validation: equation numbering continuity, footnote balance, cross links, registry
consistency, residues (`\tag{}`, bare `■`, `\mathcal`), heading anchors, duplicate anchor ids and
unresolved FIGURE placeholders. Anchors include both heading attributes (`{#id}`) and
merge-generated inline spans (`<span id="…">`).

Math gate: safe MathML dialect rewrites (`\Bigm` → `\Big`, doubled backslashes) are applied in
place first, then every formula is checked through pandoc/texmath. Missing arguments or `$`,
unbalanced braces/environments, and spans texmath cannot render are errors with a `file:line`
location. Exit code 1 on errors.

`--all` additionally audits batch integrity (missing/duplicate/inline page anchors, restart
fragments, chapter-stream `$$`/environment balance), dangling-link candidates, figure
file/crop/page consistency and failed units. Legitimate dangling anchors go into
`known_dangling.txt` (one id per line).

#### `pdfoenix build [--out book.epub]`

Assemble Markdown (TOC, table inlining) → pandoc → EPUB → postprocess (chapter-end footnotes,
nav out of the spine). `--out` names the output file inside `build/`. `source/cover.*` becomes
the cover automatically.

#### `pdfoenix inspect [EPUB]`

Structural EPUB acceptance checks (mimetype position/compression, container.xml, OPF
manifest/spine, MathML, footnote back-links). Without a path, the newest `.epub` in `build/` is
checked. Exit code 1 on issues.

### Status and reports

#### `pdfoenix watch [--interval SEC] [--once]`

Read-only live dashboard (see [Live dashboard](#live-dashboard)).

#### `pdfoenix report cost [--by model|chapter]`

Token and cost totals by model or by chapter (`cost_usd` is computed from `presets/pricing.toml`;
models without a unit price show 0, but token counts are always real).

#### `pdfoenix report quality [--all]`

The experiment **cost-quality comparison**: one row per (batch, combo) cell with coverage,
corrections per 100 reviewed pages, and cost; also writes `expt/report.md`. `--all` includes
combos removed from their batch (their data is kept and hidden by default).

#### `pdfoenix report risk`

Formula/page risk distribution report (used to recalibrate sampling thresholds and
probabilities; read-only, changes no configuration).

### Experiments

One **batch** = one comparison round; one **cell** = one combo's complete run inside a batch
(artifacts in `expt/batch_N/combo_M/`). Experiments use their own ledger
(`expt/experiments.db`) and never pollute the production ledger.

#### `pdfoenix expt show [--batch N] [--combo ID ...] [--all]`

Read-only display:

| Invocation                      | Shows                                                                 |
|---------------------------------|-----------------------------------------------------------------------|
| `expt show`                     | overview of all batches                                               |
| `expt show --batch 2`           | cell detail for batch 2                                               |
| `expt show --combo 2 4`         | rows for these combos across batches                                  |
| `expt show --batch 2 --combo 4` | preview: what batch 2 with combo 4 would look like (config unchanged) |

#### `pdfoenix expt run [--batch N] [--combo ID ...] [--all] [-m NOTE] [--force]`

Run one experiment round (**spends money**). Without `--batch` a new batch is created; with
`--batch` the unfinished cells of that batch resume, and if `--combo` is given the batch's combo
set is replaced and gaps are filled (old data is kept). Finished cells are skipped unless
`--force`.

```bash
pdfoenix -C books/my_book report cost --by model      # cost by model
pdfoenix -C books/my_book expt show                   # batch × combo overview (read-only)
pdfoenix -C books/my_book expt run --combo 2 4 -m "retest after gateway change"
pdfoenix -C books/my_book expt run --batch 1          # resume batch 1
pdfoenix -C books/my_book report quality              # comparison + expt/report.md
```

#### Hands-on: pick a combo that pays off

If you want to test the cost/quality trade-off yourself, use `expt`. Scenario: the default
combo works well on your book, but would a cheaper one be good enough? A combo is
"transcribe model × verify model" — both cost money, so measure before switching.

**Step 1: declare the combos you want to try in `config.toml`** (by hand — there is no
wizard):

```toml
[[expt.combo]]
id = 2
transcribe = "mimo/mimo-v2.6-pro-ultraspeed"   # faster, pricier transcription
verify = "deepseek/deepseek-flash#high"

[[expt.combo]]
id = 3
transcribe = "deepseek/deepseek-flash#high"    # let deepseek transcribe
verify = "mimo/mimo-v2.6-pro"
```

Every gateway used here is already declared; only add `[[expt.provider]]` for a new one.

**Step 2: set the benchmark pages** (all combos run the same pages, or the comparison is
meaningless):

```toml
[expt]
pages = "220-224,507-511"   # a few representative pages: dense formulas + sparse
```

**Step 3: run one experiment round** (this spends money; 20 pages × 3 combos — estimate with
the price table below):

```bash
pdfoenix -C books/my_book expt run --combo 1 2 3 -m "first pricing pass"
```

This creates batch 1 and runs one cell per combo: transcribe the benchmark pages → the
combo's own judge verifies → cost and correction rates are recorded. Rerunning the same
command resumes after an interruption; only `--force` redoes work.

**Step 4: read the results**:

```bash
pdfoenix -C books/my_book expt show --batch 1    # corrections/100p and cost per combo
pdfoenix -C books/my_book report quality         # cross-batch comparison + expt/report.md
```

**Step 5: adopt the winner.** If combo 2 is cheaper and just as good, make it the production
combo:

```toml
[expt]
active = 2
```

Plain `transcribe` / `verify` will use it from then on. Old batches stay reviewable forever —
combos are immutable, and every ledger call remembers its combo.

## Configuration

Each book has one `config.toml` that **only lists values that differ from the defaults**;
missing keys are filled from the built-in defaults. The complete reference of keys and defaults
lives in `presets/config.schema.toml` (copied into each book by `init` and always in sync with
the built-in defaults). `init` also writes diffs only — the file never balloons into a full
snapshot.

```toml
[book]
title = "Book Title"
author = "Author"
pdf = "source/book.pdf"
page_offset = 18          # PDF page = printed page + offset

[pipeline]
batch_pages = 5
render_dpi = 150
verify_dpi = 300
verify_chunk_size = 5

[transcriber]
timeout = 3600            # per-call timeout in seconds
attempts = 3              # tries per transcribe window / verification chunk
retry_delay = 2.0         # base retry delay in seconds (linear backoff)

[expt]
pages = "220-224,507-511"     # benchmark pages shared by every combo
active = 1                    # the combo behind plain transcribe/verify
max_workers = 2               # concurrent windows per combo
attempts = 4                  # retries per window

[[expt.provider]]             # gateway connection; keys never hit disk, export them
name = "mimo"
base_url = "https://api.xiaomimimo.com/v1"
api_key_env = "MIMO_API_KEY"

[[expt.provider]]
name = "deepseek"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

# Default combo: the benchmark's value sweet spot (shipped, no need to write it)
[[expt.combo]]
id = 1                        # combos are immutable; create a new id to change one
transcribe = "mimo/mimo-v2.6-pro"
verify = "deepseek/deepseek-flash#high"   # must differ from transcribe

[sampling]
page_risk = "max"
budget = "20%"           # hard ceiling; the probability target is ~10%
low_max = 50
high_min = 125
low_prob = 0.01
medium_prob = 0.09
high_prob = 0.60
min_pages = 10           # absolute floor even for formula-sparse books
min_rate = 0.10          # relative floor: at least 10% of candidate pages
seed = 0

[review]
global_audit = true      # run the global audit after verify (dry mode)
```

### Combos: the model pair behind the two agents

A **combo** is the model pair of the two billed agents: `transcribe` writes Markdown, `verify`
(the judge) reviews it. Combos live in `config.toml` and are **immutable** — create a new id to
change one, so historical experiment records stay interpretable. **Adding a combo is plain
configuration** (no wizard, no preset model catalog — people tuning model pairs know what they
want):

```toml
# config.toml
[[expt.provider]]         # only for a new gateway; skip if already declared
name = "deepseek"
base_url = "https://api.deepseek.com"
api_key_env = "DEEPSEEK_API_KEY"

[[expt.combo]]
id = 2                    # a new id; never edit old ones
transcribe = "deepseek/deepseek-flash#high"
verify = "mimo/mimo-v2.6-pro"   # must differ from transcribe
```

Three rules:

- Specs are written `provider/model#thinking`; `verify` must differ from `transcribe`
  (a judge never reviews its own output);
- `[[expt.provider]]` declares the gateway connection: `base_url` plus the name of the key's
  environment variable (**keys never hit disk**; just export them);
- `active` points at the combo behind plain `transcribe` / `verify`.

The adapter turns a combo into `opencode run --model provider/model#variant`;
`working/opencode.jsonc` is generated at runtime (with the tool-free agent skeleton) and never
edited.

`thinking = "default"` (or empty) attaches no variant; any other value becomes `#<value>` and
is passed to the gateway as an explicit effort level. Which levels exist is up to the model and
gateway:

- The **MiMo** gateway accepts `default` only; `#high` / `#low` / `#max` yield
  `Variant unavailable`;
- **DeepSeek**'s `#low` / `#high` / `#max` map to `reasoning_effort` (default `high`);
  `#none` turns thinking off (quality drops sharply, see the table below);
- `deepseek-flash` (now V4.1 Flash) takes image input and can transcribe or verify;
  `deepseek-v4-pro` is text-only and cannot look at scans (it refuses the call).

### Combo reference prices (USD / 10 pages)

Measured on a statistics scan, 20 pages × 4 difficulty windows (dense formulas / medium / sparse
/ prose), full transcription + full verification including retry overhead, normalized to 10
pages:

| Combo (transcribe × verify)                       | Corrections/100p | USD/10p | Positioning      |
|---------------------------------------------------|-----------------:|--------:|------------------|
| mimo-v2.6-pro-ultraspeed × mimo-v2.6-pro          |           **55** |    0.68 | best quality     |
| **mimo-v2.6-pro × deepseek-flash#high** (default) |            50–65 |    0.13 | value sweet spot |
| deepseek-flash#high × mimo-v2.6-pro               |               85 |    0.12 | cost first       |
| mimo-v2.6-flash × mimo-v2.6-pro                   |              190 |    0.11 | not recommended  |
| deepseek-flash#none × mimo-v2.6-pro               |              250 |    0.13 | not recommended  |

- The default combo (row 2, the value sweet spot) ships with the defaults: `init`, then
  `export MIMO_API_KEY` and `export DEEPSEEK_API_KEY` and go; for maximum quality create a
  combo from row 1;
- Thinking levels matter a lot: deepseek-flash `#none` → `#high` moves the error rate 250 → 85
  for +$0.02/10 pages — thinking is worth enabling;
- `deepseek-v4-pro` cannot take image input (it refuses the call) at any thinking level;
- Prices scale with formula density; the table is the **full-verification** picture. In
  production, with ~10% risk-sampled verification, total cost ≈ transcribe + 10% × verify,
  another 30–40% below the table.

### Sampling

Risk bands come from calibration: page risk `≤50 / 51–125 / >125` maps to sampling probabilities
`1% / 9% / 60%`, targeting ~10% of pages in expectation; `budget` is only a hard ceiling. The
risk score is pure string analysis (weighted counts of sub/superscripts, fractions, accents,
bold fonts, matrices, big operators); weights live in `[sampling.risk_weights]`, and
`report risk` shows the measured distribution for recalibration.

Sampling has a "floor + ceiling": pages are drawn by risk probability first; if fewer than
`max(min_pages, ceil(min_rate × candidate pages))` were drawn, random pages fill the gap; if
fewer candidates exist than the floor, all are taken. Formula-sparse books therefore never end
up almost unsampled.

## Cost and quality

Every model call is booked into `logs/pipeline.db` (production) or `expt/experiments.db`
(experiments): model, thinking level, combo, batch, input/vision/output tokens, latency, status.
You can always answer "what did this book cost, and where did it go":

```bash
pdfoenix -C books/my_book report cost --by model     # tokens / cost by model
pdfoenix -C books/my_book report cost --by chapter   # tokens / cost by chapter
pdfoenix -C books/my_book report quality             # cost-quality comparison + expt/report.md
pdfoenix -C books/my_book report risk                # risk distribution (for calibration)
```

Experiment quality is judged by each combo's own `verify` agent: corrections per 100 reviewed
pages, plus coverage and structural defects (wrong page anchors, tool-markup leaks).

> `cost_usd` is computed from `presets/pricing.toml` unit prices; models without a configured
> price show 0, but token counts are always real.

## FAQ

**My PDF has no text layer. What now?**
Chapter-layout scouting needs the text layer. OCR pure image scans first:

```bash
ocrmypdf input.pdf output.pdf
```

**A run was interrupted. What now?**
Just rerun the same command. Every stage skips the parts already done and continues; to redo a
step from scratch, add `--force` to that command.

**`check` reports "dangling anchors", but those chapters aren't transcribed yet.**
That is expected: cross-references point at chapters you haven't reached. Put those anchor ids
into `known_dangling.txt` in the book directory (one per line) and `check` will accept them.

**I get `Variant unavailable`.**
You asked for a thinking level the gateway does not offer (e.g. `#high` on MiMo). Set
`thinking = "default"` or use a gateway that supports the level; see
[Combos](#combos-the-model-pair-behind-the-two-agents).

**What will a whole book cost?**
See [Combo reference prices](#combo-reference-prices-usd--10-pages). In production, total cost
≈ transcribe + ~10% verify. To see what you have already spent, run `report cost` any time.

**I want stronger or cheaper models.**
Do not edit an existing combo (it is immutable so history stays comparable); add a new id in
`config.toml` and measure it on your own book with `expt` first — full walkthrough in
[Hands-on: pick a combo that pays off](#hands-on-pick-a-combo-that-pays-off):

```toml
[[expt.combo]]
id = 2
transcribe = "mimo/mimo-v2.6-pro-ultraspeed"
verify = "mimo/mimo-v2.6-pro"
```

**Are API keys safe?**
Keys travel through environment variables only (`[[expt.provider]].api_key_env`) and never
touch any file; the whole `books/` directory is in `.gitignore` as well.

## Live dashboard

`pdfoenix watch` is **read-only**: it never starts or advances the pipeline. It reads
`state/progress.json` and `logs/pipeline.db`, so run it alongside the commands that write those
files:

```bash
# Terminal 1: run the pipeline
pdfoenix -C books/my_book transcribe

# Terminal 2: watch at the same time
pdfoenix -C books/my_book watch

# Single frame
pdfoenix -C books/my_book watch --once

# Refresh interval (seconds)
pdfoenix -C books/my_book watch --interval 2
```

For live progress use two terminals; `pdfoenix transcribe && pdfoenix watch` works too, but
`watch` only starts **after** transcribe finishes. Press `Ctrl-C` to exit. Without `rich`
installed the dashboard falls back to plain-text frames.

A frame looks like this (mid-transcription):

```text
pdfoenix · My Book
────────────────────────────────────────────────────────────────────────
chapter  stage       transcribe        verify   objects         corr  cost
-------  ----------  ----------------  -------  --------------  ----  -----
ch01     verify      5/5               5/5 pages  104 eq · 8 fig  1   $0.05
ch02     transcribe  0/5 1 run 1 fail  -          -               1   $0.00
────────────────────────────────────────────────────────────────────────
$0.05 · 2 calls · 104k tokens · 2 agents active · 5/5 pages verified · 2 corrections · 1 failure
```

One row per chapter: batch progress (done/total, `run` = in flight, `fail` = failed),
verification page progress (`done/total pages`, plus `run`/`fail` chunk counts while a
run is in flight), merged formulas/figures, corrections and cost; the bottom line is the
book-wide total. The dashboard itself is read-only — every number comes from state files
the pipeline has already written.

## Project structure

```text
pdfoenix/          # package source
├── core/          # deterministic kernel (no LLM): book/config/state/pages/telemetry/
│                  #   risk/mathlint/scout/render/merge/check/build/postprocess
├── combo.py       # model combos: provider/combo config, active combo, opencode config
│                  #   generation
├── pipeline.py    # shared stage operations: render-missing, transcribe windows,
│                  #   verify images, progress, correction bookkeeping
├── transcribe/    # Transcriber protocol + OpenCode adapter
├── verify/        # sampling, page content recovery, global audit
├── assets/        # figure cropping, verification page rendering
├── expt.py        # experiments: batch/cell ledger, runner, metrics, comparison report
├── report.py      # cost/quality/risk reports
├── display.py     # table rendering (plain text / Markdown)
├── inspect.py     # EPUB acceptance checks
├── watch.py       # dashboard
├── scaffold.py    # init scaffolding
├── cli.py         # command orchestration
└── presets/       # epub.css / opencode.jsonc (agent-skeleton template) / task books /
                   #   pricing / config reference
tests/             # unit and integration tests
examples/          # copyright-free sample book and synthetic PDF generator
.github/           # CI, issue / PR templates
LICENSE            # AGPL-3.0-or-later
start.sh           # environment setup script
```

`books/` is the local data directory (PDFs, manuscripts, page images, logs, build artifacts).
It is ignored by `.gitignore` and never enters the repository.

## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff check pdfoenix tests
pylint --disable=all --enable=duplicate-code pdfoenix
```

Conventions: comments and docstrings in English; every module ships with tests; `\tag{}`, bare
`■`, and anchor-less headings are forbidden. See [CONTRIBUTING.md](CONTRIBUTING.md) for the
contribution workflow, the no-real-books rule, and secret handling.

## Design

The full design of page anchors, the object model, verification sampling, the cost ledger, and
the configuration schema is documented in [ARCHITECTURE.md](ARCHITECTURE.md).

## License

[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).

AGPL was chosen because the core dependency **PyMuPDF** is dual-licensed under AGPL-3.0 or a
commercial Artifex license. If you need closed-source distribution, obtain a commercial PyMuPDF
license or replace it with a license-compatible PDF renderer.
