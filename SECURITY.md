# Security policy

## Supported versions

Security fixes are applied to the latest released minor version. The project is
currently in the `0.4.x` alpha series.

## Reporting a vulnerability

Please do **not** open a public issue for a vulnerability. Use GitHub's private
vulnerability reporting:

<https://github.com/ChenQingO/pdfoenix/security/advisories/new>

Include the affected version, a minimal reproduction, and the impact. We will
acknowledge the report and coordinate a fix and disclosure.

## Secrets and local data

- API keys are never read from the repository. They are resolved from the
  environment variables named in `config.toml` (`api_key_env`).
- `books/` (PDFs, manuscripts, page images, logs, EPUBs, SQLite ledgers) is
  ignored and must never be committed. See `CONTRIBUTING.md`.
- If you believe a key or a real book has entered Git history, treat it as
  compromised: rotate the key and rewrite the history before publishing.
