# Contributing to pdfoenix

Thanks for helping improve pdfoenix. This project is a deterministic pipeline
with two narrowly scoped model calls; most contributions should not need an LLM
at all.

## Development setup

```bash
git clone https://github.com/ChenHingO/BooksProject.git
cd BooksProject
./start.sh
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Run the checks:

```bash
pytest
ruff check pdfoenix tests
pylint --disable=all --enable=duplicate-code pdfoenix
```

## Rules

- Comments and docstrings are English.
- Every module ships with tests. New behaviour needs a failing test first.
- Keep the deterministic layer deterministic: no model call belongs in
  `pdfoenix/core`, `pdfoenix/verify` (except through `Transcriber`), or the
  build path.
- Do not commit real books, scanned pages, manuscripts, EPUBs, or logs. The
  entire `books/` tree is ignored; use temporary directories or synthetic PDFs
  in tests.
- Never commit API keys, tokens, gateway credentials, or `.env` files. Keys are
  supplied through environment variables named by `api_key_env`.
- Before changing a preset, update both the packaged file in
  `pdfoenix/presets/` and any relevant tests. `pdfoenix init` refreshes existing
  books' text presets but never their CSS or configuration.
- Math must render through pandoc/texmath to MathML. `pdfoenix check` is the
  gate; `build` performs no content validation.

## Pull requests

- Keep a PR focused. Refactors and behaviour changes should be separate.
- Include a short rationale and the commands you ran.
- Update `README.md`, `README.en.md`, `ARCHITECTURE.md`, and `CHANGELOG.md` when
  user-visible behaviour changes.
- CI must be green: Ruff, duplicate-code Pylint, tests, and package build.

By contributing, you agree that your contribution is licensed under
AGPL-3.0-or-later.
