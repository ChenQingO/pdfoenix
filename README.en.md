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
[Usage](#usage) · [Configuration](#configuration) · [Development](#development)

![Python](https://img.shields.io/badge/Python-3.12%2B-blue?logo=python&logoColor=white)
![uv](https://img.shields.io/badge/uv-package%20manager-8A2BE2)
![pandoc](https://img.shields.io/badge/pandoc-EPUB-7a4f1d)
![status](https://img.shields.io/badge/status-alpha-orange)

---

## What it is

`pdfoenix` is a command-line pipeline that turns a **scanned PDF** (especially a math-heavy old
book) into a **modern EPUB**.

It does not aim for a pixel-perfect copy. It aims for:

- **Absolutely accurate text** — the only hard requirement
- **Faithful formulas** — LaTeX, with the original equation numbers preserved
- **Addressable objects** — every figure, table, and formula can be located and re-checked
- **Accountable cost** — every model call is recorded, so cost/quality experiments are possible

## Features

| Feature                 | Description                                                                                                         |
|-------------------------|---------------------------------------------------------------------------------------------------------------------|
| **Deterministic first** | Rendering, scouting, merging, checking, and building are plain scripts; LLMs only transcribe and verify page images |
| **Files as state**      | Every intermediate artifact, registry, and log is persisted, so runs resume after interruption                      |
| **Page anchors**        | Transcription writes `<!-- page:N -->`, creating a two-way index between objects and page images                    |
| **Adversarial review**  | Transcription and verification use different models; risky pages are sampled and fully reviewed                     |
| **Pluggable**           | The `Transcriber` protocol, sampling strategy, risk weights, thresholds, and probabilities are all configurable     |

## Current status

- `init/scout/render/transcribe/merge/verify/assets/check/build/inspect/watch/report/expt/calibrate` are all available.
- Sampling now has absolute and relative floors: formula-sparse books are filled randomly to `min_pages` / `min_rate` instead of being almost skipped.
- `books/an_introduction` has been reindexed: 1531 formulas, chapter progress, and a dangling-anchor whitelist.
- `pdfoenix check` is clean; `pdfoenix build` produces an EPUB; `pdfoenix inspect` passes.
- Full `pytest`, `ruff`, and duplicate-code checks pass.

## How it works

```text
PDF
 │
 ├─ scout      scan the text layer; find chapters/sections/figures/tables/equations
 ├─ render     render pages to images
 ├─ transcribe vision model transcribes each page → Markdown + LaTeX
 ├─ merge      merge batches, split sections, register objects
 ├─ assets     crop figures (optional)
 ├─ verify     another model reviews sampled pages
 ├─ check      local rule-based validation
 ├─ build      pandoc → EPUB
 └─ inspect    structural EPUB checks
```

## Installation

### Requirements

| Dependency                       | Purpose                               | Version                         |
|----------------------------------|---------------------------------------|---------------------------------|
| Python                           | Run the pipeline                      | 3.12+                           |
| [uv](https://docs.astral.sh/uv/) | Environment and dependency management | latest                          |
| pandoc                           | Build EPUB                            | 3.x                             |
| Node.js + OpenCode               | Call vision models                    | Node 20+                        |
| Model gateway                    | Transcription / verification          | any OpenCode-supported provider |

> The PDF must have a **text layer** so `scout` can find the structure. For image-only scans,
> run OCR first, for example `ocrmypdf input.pdf output.pdf`.

### One-command setup

```bash
git clone https://github.com/<your-name>/pdfoenix.git
cd pdfoenix
./start.sh
source .venv/bin/activate
```

The default configuration comes from experiments with MiMo and DeepSeek. It aims for the best
possible quality at the lowest possible cost; it is a trade-off, not a quality-first optimum.
If you want better quality, try a stronger model or another agent. Because the project has many
configuration options, you can delegate changes to an agent if you decide to switch.

`start.sh` only prepares the environment; it never runs a book pipeline. It installs `uv`,
creates `.venv` with Python 3.12, installs the package, and installs `pandoc` / `opencode` if
they are missing.

### Configure the model (project-local)

pdfoenix reads **the book's own** `presets/opencode.jsonc`; you do not need to
edit `~/.config/opencode`. `pdfoenix init` copies the packaged template to
`books/my_book/presets/opencode.jsonc`.

Put the key in an environment variable:

```bash
export MIMO_API_KEY="your-key"
```

Then edit `books/my_book/presets/opencode.jsonc` and set
`providers.mimo.settings.baseURL` to your gateway. You may also write the key
directly into that file; `books/` is ignored by Git, so it is never committed.

For an existing book created from the old template, overwrite it once:

```bash
cp pdfoenix/presets/opencode.jsonc books/my_book/presets/opencode.jsonc
```

## Quick start

```bash
# 1. Create a book
pdfoenix init books/my_book \
  --pdf /path/to/book.pdf \
  --title "Book Title" \
  --author "Author" \
  --page-offset 18

# 2. Run one chapter first
pdfoenix -C books/my_book scout
pdfoenix -C books/my_book transcribe --chapter ch01
pdfoenix -C books/my_book merge --chapter ch01
pdfoenix -C books/my_book verify --chapter ch01 --full
pdfoenix -C books/my_book build

# 3. Then run the whole book
pdfoenix -C books/my_book transcribe
pdfoenix -C books/my_book merge
pdfoenix -C books/my_book assets
pdfoenix -C books/my_book verify
pdfoenix -C books/my_book check
pdfoenix -C books/my_book build
pdfoenix -C books/my_book inspect
```

The EPUB is written to `books/my_book/build/book.epub`.

> Every stage is idempotent. To resume after an interruption, run the same command again.
> Use `--force` only when you really want to redo completed work.

### Optional: cover image

If you have a cover, just drop it into the book's `source/` directory:

```text
books/my_book/source/cover.jpg
```

`build` automatically uses `source/cover.*` as the EPUB cover. Keep only one cover file
(the first one in sorted order is used).

## Usage

### `-C / --book`

`-C` (same as `--book`) selects the **book directory to operate on**. The default is the current
directory (`.`).

```bash
# These are equivalent
cd books/my_book && pdfoenix check
pdfoenix -C books/my_book check
```

With `-C`, you can run commands from anywhere in the repository, or from a script, without
`cd`-ing into the book directory first.

### Command reference

| Command                                                    | Description                                                             |
|------------------------------------------------------------|-------------------------------------------------------------------------|
| `pdfoenix init <book> --pdf a.pdf`                         | Scaffold directories, `config.toml`, presets, and empty registries      |
| `pdfoenix scout`                                           | Scan the PDF text layer → `state/scout.json`                            |
| `pdfoenix render`                                          | Render page images → `working/pages/` (optional; transcribe fills gaps) |
| `pdfoenix transcribe [--chapter ch01] [--force]`           | Batch transcription → `working/batches/`                                |
| `pdfoenix merge [--chapter ch01]`                          | Merge batches, split sections, register objects → `manuscript/`         |
| `pdfoenix assets [--force] [--no-auto]`                    | Crop figures; auto-detect or use registry `crop`                        |
| `pdfoenix verify [--full\|--pages 19-23] [--chapter ch01]` | Sampled review; `--full` reviews everything                             |
| `pdfoenix check`                                           | Validate numbering, footnotes, links, registries, and residue           |
| `pdfoenix build [--out book.epub]`                         | Assemble → pandoc → EPUB → post-process                                 |
| `pdfoenix inspect [--epub path]`                           | Structural EPUB checks                                                  |
| `pdfoenix watch`                                           | Read-only live dashboard                                                |
| `pdfoenix report --by model\|chapter`                      | Cost summary                                                            |
| `pdfoenix expt [--batch N] [--combo ID ...] [-a]`          | Experiment display: batch × combo overview or detail                    |
| `pdfoenix expt ... --run [-m msg] [--force]`               | Run one experiment round (spend); batches auto-increment                |
| `pdfoenix expt --report [-a]`                              | Cost-vs-quality comparison + `expt/report.md`                           |
| `pdfoenix calibrate`                                       | Formula/page risk distribution report                                   |

Run `pdfoenix <command> --help` for all options of a command.

## Configuration

Each book has a single `config.toml`. Only override values that differ from the defaults; the rest
is filled in from `presets/config.schema.toml`.

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

[expt]
pages = "220-224,507-511"     # benchmark pages shared by every combo
active = 1                    # combo driving plain transcribe/verify

[[expt.provider]]
name = "mimo"
base_url = "https://api.xiaomimimo.com/v1"
api_key_env = "MIMO_API_KEY"  # keys are never stored; export them

# default combo: quality winner of our benchmark (shipped with the defaults)
[[expt.combo]]
id = 1                        # combos are immutable; create a new id to change
transcribe = "mimo/mimo-v2.6-pro-ultraspeed"
verify = "mimo/mimo-v2.6-pro" # must differ from transcribe

[sampling]
page_risk = "max"
budget = "20%"           # hard ceiling; target probability is about 10%
low_max = 50
high_min = 125
low_prob = 0.01
medium_prob = 0.09
high_prob = 0.60
min_pages = 10           # absolute floor for formula-sparse books
min_rate = 0.10          # at least 10% of candidate pages
seed = 0
```

### Combos: the models behind the two agents

A **combo** is the model pair behind the two billed agents: `transcribe` writes the
Markdown, `verify` (the judge) audits it.  Combos live in `config.toml` and are
**immutable** — to change a pair, create a new combo id, so historical experiment
records stay interpretable.  Adding combos is done only through init:

```bash
pdfoenix init books/my_book --combo 2 4   # adopt existing ids, create missing ones
```

Missing ids open an interactive wizard (Tab/Enter picks from `presets/providers.toml`;
**API keys are never asked** — export the provider's `api_key_env` instead).  A combo
spec is `provider/model#thinking`; `verify` must differ from `transcribe` (a judge
never reviews its own output).  The adapter passes the spec to the corresponding
agent as `opencode run --model provider/model#variant`; connection details
(`base_url`, key env var) come from `[[expt.provider]]` and are materialized into a
generated `working/opencode.jsonc` — users never edit that file.

`thinking = "default"` (or empty) means no variant suffix. Any other value becomes `#<value>`
and is passed to the gateway as an explicit variant, including `thinking = "none"` for gateways
that expose `none` as a real variant (DeepSeek: `deepseek/deepseek-v4-pro#none`).
For example, `thinking = "high"` produces `mimo/mimo-v2.6-pro#high`.

> The available variants depend on the model and gateway. The current MiMo gateway only accepts
> `default`; `high` / `low` / `medium` / `max` return `Variant unavailable`.

### Combo reference prices (USD per 10 pages)

Measured on a math-heavy scanned book: 20 pages across four difficulty windows,
full transcription + full review including retry waste, scaled to 10 pages:

| combo (transcribe × verify)                              | err/100p | $/10pp | role         |
|----------------------------------------------------------|---------:|-------:|--------------|
| **mimo-v2.6-pro-ultraspeed × mimo-v2.6-pro** (default)   |  **55**  |  0.68  | best quality |
| mimo-v2.6-pro × deepseek-flash#high                      |   50–65  |  0.13  | cost/quality |
| deepseek-flash#high × mimo-v2.6-pro                      |    85    |  0.12  | cheapest ok  |
| mimo-v2.6-flash × mimo-v2.6-pro                          |   190    |  0.11  | avoid        |
| deepseek-flash#none × mimo-v2.6-pro                      |   250    |  0.13  | avoid        |

Combo 1 (the quality winner) ships with the default configuration: `init`, then
`export MIMO_API_KEY`, and you are ready.  Thinking matters: deepseek-flash
`#none` → `#high` moves 250 → 85 err/100p for +$0.02/10pp.  `deepseek-v4-pro`
has no image input and refuses these tasks at every thinking level.  Prices
scale with formula density; with ~10% sampled review in production the book
cost is roughly transcription + 10% × review, 30–40% below the table.

The risk bands come from calibration: page risk `≤50 / 51–125 / >125` maps to sampling
probabilities `1% / 9% / 60%`, about 10% of pages on average. `budget` is only a hard ceiling.

Sampling has a floor and a ceiling. First pages are drawn by risk probability. If the result is
below `max(min_pages, ceil(min_rate × candidate_pages))`, uniformly random remaining pages are
added until the floor is reached. If the book has fewer candidate pages than the floor, every page
is sampled. This prevents formula-sparse books from being almost entirely skipped.

## Cost and quality

```bash
pdfoenix -C books/my_book report --by model     # tokens / cost by model
pdfoenix -C books/my_book report --by chapter   # tokens / cost by chapter

pdfoenix -C books/my_book expt                  # batch × combo overview (display)
pdfoenix -C books/my_book expt --combo 2 4 --run -m "after prompt v2"   # new batch
pdfoenix -C books/my_book expt --batch 1 --run  # resume batch 1's unfinished cells
pdfoenix -C books/my_book expt --report         # comparison + expt/report.md
pdfoenix -C books/my_book watch                 # live dashboard
```

Nothing spends without `--run`; every other `expt` invocation is display.  A batch is one
comparison round over a set of combos (`expt/batch_N/combo_M/`); with `--batch` and `--combo`,
`--run` replaces the batch's combo set and fills its gaps.  Finished cells are skipped unless
`--force`, and the ledger only ever grows.

Every model call is recorded in `logs/pipeline.db` (production) or `expt/experiments.db`
(experiments): model, thinking level, combo, batch, input/vision/output tokens, latency,
and status.  Experiment quality is judged by the combo's own `verify` model: corrections
per 100 verified pages, reported next to coverage and structural defects (bad page
anchors, leaked tool-call markup).

> `cost_usd` is computed from the prices in `presets/pricing.toml`. A model without a configured
> price shows 0, but token counts are always recorded.

## Project structure

```text
pdfoenix/          # package source
├── core/          # deterministic kernel (no LLM)
├── transcribe/    # Transcriber protocol + OpenCode adapter
├── verify/        # sampling, page-content recovery, global audit
├── assets/        # figure cropping, verification page rendering
├── presets/       # epub.css / opencode.jsonc / task books / pricing / defaults
├── cli.py         # command orchestration
├── report.py      # cost / experiment reports
├── calibrate.py   # risk distributions
├── inspect.py     # EPUB inspection
└── watch.py       # dashboard
tests/             # unit and integration tests
start.sh           # environment setup script
```

`books/` is a local data directory (PDFs, manuscripts, page images, logs, build artifacts).
It is ignored by `.gitignore` and is never committed.

## Development

```bash
uv pip install -e ".[dev]"
pytest
ruff check pdfoenix tests
pylint --disable=all --enable=duplicate-code pdfoenix
```

Conventions: comments and docstrings are in English; every module has tests; `\tag{}`, bare `■`,
and unanchored headings are forbidden.

## Design

See [ARCHITECTURE.md](ARCHITECTURE.md) for page anchors, the object model, verification sampling,
the cost ledger, and the configuration schema.

## License

TBD
