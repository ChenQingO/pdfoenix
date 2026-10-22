#!/usr/bin/env bash
# Bootstrap the local development environment only.
#
# This script installs uv, creates a project-local .venv, installs pdfoenix,
# and makes sure pandoc/opencode exist. It never runs a book pipeline.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage:
  ./start.sh          # prepare .venv and external tools
  ./start.sh --help

Environment: uv + .venv. Conda is not used.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

# ── Python environment ────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
  echo "==> Installing uv"
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Preparing Python 3.12 virtual environment (.venv)"
uv python install 3.12
uv venv --python 3.12 "$ROOT/.venv"

PY="$ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  PY="$ROOT/.venv/Scripts/python.exe"
fi
uv pip install --python "$PY" -e "$ROOT"

# ── External tools ────────────────────────────────────────────────────
if ! command -v pandoc >/dev/null 2>&1; then
  echo "==> Installing pandoc"
  if command -v brew >/dev/null 2>&1; then
    brew install pandoc
  elif command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update && sudo apt-get install -y pandoc
  elif command -v winget >/dev/null 2>&1; then
    winget install --id JohnMacFarlane.Pandoc -e
  else
    echo "pandoc is required: https://pandoc.org/installing.html" >&2
    exit 1
  fi
fi

if ! command -v opencode >/dev/null 2>&1; then
  echo "==> Installing OpenCode"
  if command -v npm >/dev/null 2>&1; then
    npm install -g @opencode/cli
  else
    echo "Node.js 20+ is required before installing OpenCode." >&2
    exit 1
  fi
fi

if [ ! -f "$HOME/.config/opencode/opencode.jsonc" ] && \
   [ ! -f "${XDG_CONFIG_HOME:-$HOME/.config}/opencode/opencode.jsonc" ]; then
  echo "warning: ~/.config/opencode/opencode.jsonc not found." >&2
  echo "         Configure your model gateway before calling a model." >&2
fi

cat <<EOF

Environment ready.

Activate it in this shell:
  source .venv/bin/activate

Then create a book and run the pipeline yourself:
  pdfoenix init books/my_book --pdf /path/to/book.pdf --title "Title" --author "Author" --page-offset 0
  pdfoenix -C books/my_book scout
  pdfoenix -C books/my_book transcribe --chapter ch01
  pdfoenix -C books/my_book merge --chapter ch01
  pdfoenix -C books/my_book verify --chapter ch01 --full
  pdfoenix -C books/my_book build
EOF
