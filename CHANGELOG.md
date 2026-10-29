# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.4.0] - 2026-10-29

### Added

- Deterministic math gate (`pdfoenix/core/mathlint.py`): delimiter, brace and
  environment checks plus pandoc/texmath validation, with safe MathML dialect
  rewrites applied by `check`, `merge`, and transcription-batch validation.
- `check` now reports math errors with `file:line` locations and returns a
  non-zero exit code. `build` deliberately performs no content validation.
- Transcribe task book now documents the texmath dialect and includes a
  pre-return math self-check; the verify task book also checks malformed TeX.
- `init` refreshes text presets for existing books, leaving configuration,
  manuscripts, state, and CSS untouched.
- CI on Linux and macOS (Ruff, duplicate-code Pylint, tests, package build).
- Contributor Covenant, contribution guide, security policy, issue templates,
  and release changelog.

### Changed

- Version aligned to `0.4.0` with a single source in `pdfoenix/__init__.py`.
- The OpenCode adapter invokes the installed `opencode` CLI directly instead of
  relying on `npx opencode`.
- Installation docs and `start.sh` updated for the current single-file
  `config.toml` workflow.

### Fixed

- Fixed math transcription defects that produced literal TeX or failed MathML
  in the reference book, including unbalanced braces, missing `$`, bare
  `\boldsymbol`, doubled backslashes, and unsupported big-delimiter commands.

### Licensing

- Released under AGPL-3.0-or-later, matching the license of PyMuPDF.
