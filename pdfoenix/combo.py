"""Model combinations: the model pair behind transcribe and verify.

A *combo* is an immutable ``(transcribe, verify)`` model pair declared in
``config.toml`` under ``[[expt.combo]]``, together with its connection info
(``[[expt.provider]]``).  Immutability is deliberate: experiments reference
combos by id, so history stays interpretable; changing a pair means creating a
new id.

``config.toml`` is the single file users edit.  This module turns its model
wiring into a working adapter:

* reading the declared combos and providers,
* the *active* combo behind plain ``pdfoenix transcribe`` / ``verify``,
* the generated ``working/opencode.jsonc`` (an artifact of the packaged agent
  template plus ``[[expt.provider]]``, never hand-edited).

Combos are written by hand in ``config.toml`` — the people tuning model pairs
know what they want, and no preset catalog can cover real needs.  The
experiment runner in :mod:`pdfoenix.expt` builds on this, as does the
production pipeline via :mod:`pdfoenix.pipeline`.
"""
import copy
import json
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.config import ConfigError, parse_model_spec


# ── Configuration accessors ─────────────────────────────────────────────
def settings(book: Book) -> dict:
    """The ``[expt]`` table: model wiring plus experiment runner knobs."""
    return book.config.get("expt", {})


def combos(book: Book) -> dict[int, dict]:
    """Return ``{combo_id: entry}`` in declaration order."""
    return {int(entry["id"]): entry
            for entry in settings(book).get("combo", [])}


def providers(book: Book) -> dict[str, dict]:
    """Return ``{provider_name: entry}`` for the declared gateways."""
    return {str(entry["name"]): entry
            for entry in settings(book).get("provider", [])}


def combo_pair(entry: dict) -> dict:
    """Resolve one combo into both agent specs."""
    pair = {}
    for role in ("transcribe", "verify"):
        provider, model, thinking = parse_model_spec(entry.get(role, ""))
        pair[role] = {"spec": f"{provider}/{model}", "model": model,
                      "thinking": thinking or "default"}
    return pair


def active_combo(book: Book) -> dict | None:
    """The combo driving plain transcribe/verify, if one is selected."""
    active = int(settings(book).get("active") or 0)
    return combos(book).get(active)


# ── Model wiring ────────────────────────────────────────────────────────
def generate_opencode_config(book: Book) -> Path:
    """Materialize the adapter's OpenCode config from ``[[expt.provider]]``.

    The book's ``config.toml`` is the single source of truth; the generated
    file (``working/opencode.jsonc``) is an artifact users never edit.
    """
    template = Path(__file__).resolve().parent / "presets" / "opencode.jsonc"
    text = "\n".join(line for line in template.read_text(encoding="utf-8").splitlines()
                     if not line.strip().startswith("//"))
    data = json.loads(text)

    referenced: dict[str, set[str]] = {}
    for entry in combos(book).values():
        for role in ("transcribe", "verify"):
            provider, model, _ = parse_model_spec(entry.get(role, ""))
            referenced.setdefault(provider, set()).add(model)

    generated = {}
    for name, entry in providers(book).items():
        if name not in referenced:
            continue
        generated[name] = {
            "name": name,
            # Every [[expt.provider]] is an OpenAI-compatible HTTP endpoint;
            # the package picks opencode's matching driver for unknown names.
            "package": "@opencode/ai/providers/openai-compatible",
            "settings": {"baseURL": entry["base_url"],
                         "apiKey": f"{{env:{entry['api_key_env']}}}"},
            "models": {model: {"name": model} for model in sorted(referenced[name])},
        }
    missing = set(referenced) - set(generated)
    if missing:
        raise ConfigError(f"no [[expt.provider]] for: {sorted(missing)}")
    data["providers"] = generated

    destination = book.root / "working" / "opencode.jsonc"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return destination


def pipeline_config(book: Book, entry: dict) -> dict:
    """Adapter configuration for one combo: its models plus the generated file."""
    config = copy.deepcopy(book.config)
    pair = combo_pair(entry)
    # [models] is adapter input, not user configuration: it is generated here
    # from the combo and never appears in config.toml.
    config["models"] = {
        "transcribe": {"model": pair["transcribe"]["spec"],
                       "thinking": pair["transcribe"]["thinking"]},
        "verify": {"model": pair["verify"]["spec"],
                   "thinking": pair["verify"]["thinking"]},
    }
    config.setdefault("transcriber", {})
    config["transcriber"]["opencode_config"] = str(generate_opencode_config(book))
    return config
