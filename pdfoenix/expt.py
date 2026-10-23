"""Model-combination experiments (``pdfoenix expt``).

Design
------
A *combo* is the model pair behind the two billed agents (transcribe + verify).
Combos are added only through ``pdfoenix init --combo`` and are immutable: to
change a pair, create a new combo id.  A *batch* is one comparison round over a
set of combos, materialized as ``expt/batch_N/``.

Every command is read-only display unless ``--run`` is given.  With ``--run``
and no ``--batch`` a new batch is created from the selected combos; with
``--batch`` the batch's combo set is replaced by ``--combo`` (if given) and its
unfinished cells run.  Finished cells are skipped unless ``force``.

Artifacts live under ``expt/batch_N/combo_M/`` (transcripts, run log, per-cell
progress) and all cost accounting lands in ``expt/experiments.db``: batches,
batch membership, per-(batch, combo) metrics and the shared call ledger
(``llm_calls`` rows carry ``combo_id``/``batch_no``).
"""
import copy
import json
import re
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from pdfoenix.assets.crop import render_verify_pages
from pdfoenix.core.book import Book
from pdfoenix.core.config import ConfigError, parse_model_spec, write_config
from pdfoenix.core.pages import batch_ranges, parse_pages
from pdfoenix.core.telemetry import Telemetry, compute_cost
from pdfoenix.report import load_pricing
from pdfoenix.verify.content import content_for_pages

_MARKUP_RE = re.compile(
    r"<function=|<invoke|<tool_call|<function_call|<tool_calls|<function_calls|"
    r"｜｜DSML｜｜",  # noqa: RUF001 - DeepSeek's marker really uses fullwidth pipes
    re.IGNORECASE)
_ANCHOR_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")
_BATCH_STEM_RE = re.compile(r"^p(\d+)(?:-(\d+))?$")

_EXPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
  batch_no INTEGER PRIMARY KEY,
  message TEXT, started TEXT, finished TEXT, status TEXT
);
CREATE TABLE IF NOT EXISTS batch_combos (
  batch_no INTEGER, combo_id TEXT, position INTEGER,
  PRIMARY KEY (batch_no, combo_id)
);
CREATE TABLE IF NOT EXISTS metrics (
  batch_no INTEGER, combo_id TEXT,
  pages_total INTEGER, pages_covered INTEGER, corrections INTEGER,
  error_rate REAL, transcribe_cost REAL, verify_cost REAL,
  failed_calls INTEGER, anchor_defects INTEGER, markup_leaks INTEGER,
  updated TEXT,
  PRIMARY KEY (batch_no, combo_id)
);
"""

OVERVIEW_COLUMNS = ("batch", "combos", "cells", "status", "message")
CELL_COLUMNS = ("combo", "transcribe", "thinking", "verify", "verify_thinking",
                "pages", "corrections", "error_rate", "cost", "status")

_progress_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── Workspace and store ─────────────────────────────────────────────────
def expt_dir(book: Book) -> Path:
    return book.root / "expt"


def db_path(book: Book) -> Path:
    return expt_dir(book) / "experiments.db"


def ensure_workspace(book: Book) -> Path:
    """Create ``expt/`` and its schema; idempotent, safe to call from display."""
    root = expt_dir(book)
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path(book)) as connection:
        connection.executescript(_EXPT_SCHEMA)
    return root


def _connect(book: Book) -> sqlite3.Connection:
    ensure_workspace(book)
    connection = sqlite3.connect(db_path(book))
    connection.row_factory = sqlite3.Row
    return connection


# ── Config accessors ────────────────────────────────────────────────────
def expt_settings(book: Book) -> dict:
    return book.config.get("expt", {})


def combos(book: Book) -> dict[int, dict]:
    """Return ``{combo_id: entry}`` in declaration order."""
    return {int(entry["id"]): entry
            for entry in expt_settings(book).get("combo", [])}


def providers(book: Book) -> dict[str, dict]:
    return {str(entry["name"]): entry
            for entry in expt_settings(book).get("provider", [])}


def combo_pair(entry: dict) -> dict:
    """Resolve one combo into both agent specs."""
    pair = {}
    for role in ("transcribe", "verify"):
        provider, model, thinking = parse_model_spec(entry.get(role, ""))
        pair[role] = {"spec": f"{provider}/{model}", "model": model,
                      "thinking": thinking or "default"}
    return pair


def benchmark_pages(book: Book) -> list[int]:
    """The fixed benchmark pages of ``[expt].pages`` (required for runs)."""
    spec = str(expt_settings(book).get("pages") or "").strip()
    if not spec:
        raise ConfigError(
            "expt.pages is empty; declare benchmark pages in config.toml "
            "([expt] pages = \"220-224,...\") so combos stay comparable")
    pages = parse_pages(spec, _total_pages(book))
    if not pages:
        raise ConfigError(f"expt.pages matches no page: {spec!r}")
    return pages


def _total_pages(book: Book) -> int:
    import fitz

    with fitz.open(book.pdf()) as doc:
        return doc.page_count


def window_plan(book: Book) -> list[tuple[int, int]]:
    """Contiguous ``(first, last)`` windows over the benchmark pages."""
    size = int(book.config["pipeline"]["batch_pages"])
    pages = benchmark_pages(book)
    runs: list[list[int]] = []
    for page in pages:
        if runs and page != runs[-1][-1] + 1:
            runs.append([])
        if not runs:
            runs.append([])
        runs[-1].append(page)
    windows = []
    for run in runs:
        windows.extend(batch_ranges(run[0], run[-1], size, set(run)))
    return windows


# ── Batch bookkeeping ───────────────────────────────────────────────────
def list_batches(book: Book) -> list[dict]:
    with _connect(book) as connection:
        rows = connection.execute(
            "SELECT * FROM batches ORDER BY batch_no").fetchall()
        members = connection.execute(
            "SELECT batch_no, combo_id FROM batch_combos "
            "ORDER BY batch_no, position").fetchall()
    grouped: dict[int, list[str]] = {}
    for row in members:
        grouped.setdefault(row["batch_no"], []).append(str(row["combo_id"]))
    return [{**dict(row), "combos": grouped.get(row["batch_no"], [])}
            for row in rows]


def create_batch(book: Book, combo_ids: list[str], message: str = "") -> int:
    """Create the next batch round holding ``combo_ids``; return its number."""
    with _connect(book) as connection:
        row = connection.execute(
            "SELECT COALESCE(MAX(batch_no), 0) AS last FROM batches").fetchone()
        batch_no = int(row["last"]) + 1
        connection.execute(
            "INSERT INTO batches (batch_no, message, started, status) "
            "VALUES (?, ?, ?, 'running')", (batch_no, message, _now()))
        _set_members(connection, batch_no, combo_ids)
    return batch_no


def _set_members(connection, batch_no: int, combo_ids: list[str]) -> None:
    connection.execute("DELETE FROM batch_combos WHERE batch_no = ?", (batch_no,))
    for position, combo_id in enumerate(combo_ids):
        connection.execute(
            "INSERT INTO batch_combos (batch_no, combo_id, position) "
            "VALUES (?, ?, ?)", (batch_no, str(combo_id), position))


def batch_members(book: Book, batch_no: int) -> list[str]:
    with _connect(book) as connection:
        rows = connection.execute(
            "SELECT combo_id FROM batch_combos WHERE batch_no = ? "
            "ORDER BY position", (batch_no,)).fetchall()
    return [str(row["combo_id"]) for row in rows]


def set_batch_combos(book: Book, batch_no: int, combo_ids: list[str]) -> None:
    """Replace a batch's combo set (replace semantics; old data is kept)."""
    with _connect(book) as connection:
        exists = connection.execute(
            "SELECT 1 FROM batches WHERE batch_no = ?", (batch_no,)).fetchone()
        if exists is None:
            raise ConfigError(f"no such batch: {batch_no}")
        _set_members(connection, batch_no, combo_ids)


def finish_batch(book: Book, batch_no: int, status: str) -> None:
    with _connect(book) as connection:
        connection.execute(
            "UPDATE batches SET status = ?, finished = ? WHERE batch_no = ?",
            (status, _now(), batch_no))


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
    config["models"]["transcribe"] = {
        "model": pair["transcribe"]["spec"],
        "thinking": pair["transcribe"]["thinking"]}
    config["models"]["verify"] = {
        "model": pair["verify"]["spec"],
        "thinking": pair["verify"]["thinking"]}
    config.setdefault("transcriber", {})
    config["transcriber"]["opencode_config"] = str(generate_opencode_config(book))
    return config


def active_combo(book: Book) -> dict | None:
    """The combo driving plain transcribe/verify, if one is selected."""
    active = int(expt_settings(book).get("active") or 0)
    return combos(book).get(active)


# ── Runner ──────────────────────────────────────────────────────────────
def run_expt(book: Book, *, batch_no: int | None = None,
             combo_ids: list[str] | None = None, message: str = "",
             force: bool = False) -> int:
    """Execute one experiment round; see the module docstring for semantics."""
    ensure_workspace(book)
    declared = set(combos(book))
    selected = [str(value) for value in (combo_ids or [])]
    unknown = [value for value in selected if int(value) not in declared]
    if unknown:
        raise ConfigError(f"unknown combo id(s): {unknown}; add them with "
                          "'pdfoenix init --combo'")
    if batch_no is None:
        if not selected:
            raise ConfigError("select combos with --combo <id> [...] or --all")
        batch_no = create_batch(book, selected, message)
    elif selected:
        set_batch_combos(book, batch_no, selected)
    members = batch_members(book, batch_no)
    if not members:
        raise ConfigError(f"batch {batch_no} has no combos")

    failures = []
    for combo_id in members:
        ok = run_cell(book, batch_no, combo_id, force=force)
        if not ok:
            failures.append(combo_id)
    finish_batch(book, batch_no, "failed" if failures else "done")
    return 1 if failures else 0


def run_cell(book: Book, batch_no: int, combo_id: str,
             force: bool = False) -> bool:
    """Run (or resume) one (batch, combo) cell; returns True when complete."""
    entry = combos(book)[int(combo_id)]
    cell = expt_dir(book) / f"batch_{batch_no}" / f"combo_{combo_id}"
    batches_root = cell / "batches"
    batches_root.mkdir(parents=True, exist_ok=True)
    if not force and _cell_done(book, batch_no, combo_id):
        print(f"batch_{batch_no} combo_{combo_id}: done (skip)", flush=True)
        return True

    windows = window_plan(book)
    config = pipeline_config(book, entry)
    attempts = int(expt_settings(book).get("attempts", 4))
    workers = int(expt_settings(book).get("max_workers", 2))
    progress = _Progress(cell / "progress.json")

    from concurrent.futures import ThreadPoolExecutor

    results: list[bool] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_transcribe_window, book, config, cell,
                               first, last, attempts, progress,
                               combo_id, batch_no, force)
                   for first, last in windows]
        results = [future.result() for future in futures]
    if not all(results):
        print(f"batch_{batch_no} combo_{combo_id}: FAILED (transcribe); "
              f"see {cell / 'logs' / 'run.log'}", flush=True)
        return False

    try:
        corrections = _verify_cell(book, config, cell, batches_root, attempts,
                                   combo_id, batch_no)
    except Exception as exc:  # report and keep the ledger
        _log(cell, f"verify failed: {exc}")
        print(f"batch_{batch_no} combo_{combo_id}: FAILED (verify); "
              f"see {cell / 'logs' / 'run.log'}", flush=True)
        return False
    metrics = compute_metrics(book, batch_no, combo_id, batches_root,
                              len(corrections))
    store_metrics(book, batch_no, combo_id, metrics)
    print(f"batch_{batch_no} combo_{combo_id}: "
          f"{metrics['pages_covered']}/{metrics['pages_total']} pages, "
          f"{metrics['corrections']} corrections, "
          f"{metrics['error_rate']:.0f}/100p", flush=True)
    return True


def _make_adapter(book: Book, config: dict, telemetry: Telemetry):
    """Adapter factory kept separate so tests can inject a fake model.

    Task books always come from the book's ``presets/``; the generated
    OpenCode config lives elsewhere and must not move them.
    """
    from pdfoenix.transcribe.opencode import OpenCodeTranscriber

    return OpenCodeTranscriber(book.root, config, telemetry=telemetry,
                               max_workers=1, presets_dir=book.root / "presets")


def _transcribe_window(book: Book, config: dict, cell: Path,
                       first: int, last: int, attempts: int,
                       progress: "_Progress",
                       combo_id: str, batch_no: int,
                       force: bool = False) -> bool:
    """Transcribe one window with retries; idempotent via the cell's progress."""
    from pdfoenix.transcribe.opencode import OpenCodeError

    unit = f"p{first}-{last}"
    target = cell / "batches" / f"{unit}.md"
    if not force and target.exists() and progress.get(unit) == "done":
        return True
    task = (f"PDF pages {first}-{last}. Transcribe these pages in order.")
    images = [book.page_image(page) for page in range(first, last + 1)]
    for attempt in range(1, attempts + 1):
        telemetry = Telemetry(book.root, db_path=db_path(book),
                              combo_id=combo_id, batch_no=batch_no)
        transcriber = _make_adapter(book, config, telemetry)
        try:
            _render_missing(book, range(first, last + 1))
            text = transcriber.transcribe(images, task)
        except OpenCodeError as exc:
            _log(cell, f"{unit} attempt {attempt}: {exc}")
            continue
        finally:
            transcriber.close()
            telemetry.close()
        target.write_text(text.rstrip() + "\n", encoding="utf-8")
        progress.set(unit, "done")
        return True
    return False


def _verify_cell(book: Book, config: dict, cell: Path,
                 batches_root: Path, attempts: int,
                 combo_id: str, batch_no: int) -> list:
    """Run the judge over every benchmark page of this cell."""
    from pdfoenix.transcribe.opencode import OpenCodeError

    pages = benchmark_pages(book)
    content, kept = content_for_pages(book, pages, batches_dir=batches_root)
    if not kept:
        raise ConfigError("no transcribed content to verify; windows failed")
    render_verify_pages(book, kept)
    images = [book.verify_dir / f"pdfpage_{page:03d}.png" for page in kept]
    for attempt in range(1, attempts + 1):
        telemetry = Telemetry(book.root, db_path=db_path(book),
                              combo_id=combo_id, batch_no=batch_no)
        transcriber = _make_adapter(book, config, telemetry)
        try:
            corrections = transcriber.verify(images, content)
        except OpenCodeError as exc:
            _log(cell, f"verify attempt {attempt}: {exc}")
            continue
        finally:
            transcriber.close()
            telemetry.close()
        for item in corrections:
            ledger = Telemetry(book.root, db_path=db_path(book),
                               combo_id=combo_id, batch_no=batch_no)
            ledger.record_correction(
                verify_call_id=item.call_id, unit_id=item.unit_id,
                formula_id=item.formula_id, severity=item.severity,
                pattern=item.pattern, source=item.source)
            ledger.close()
        return corrections
    raise ConfigError(f"verify failed after {attempts} attempts")


def _render_missing(book: Book, pages) -> None:
    from pdfoenix.core.render import render_pages

    missing = [page for page in pages if not book.page_image(page).exists()]
    if missing:
        render_pages(book.pdf(), missing, book.config["pipeline"]["render_dpi"],
                     book.pages_dir)


class _Progress:
    """Per-cell window progress with cross-thread safe updates."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}

    def get(self, unit: str):
        return self.data.get(unit)

    def set(self, unit: str, status: str) -> None:
        with _progress_lock:
            self.data[unit] = status
            self.path.write_text(json.dumps(self.data, indent=2) + "\n",
                                 encoding="utf-8")


def _log(cell: Path, message: str) -> None:
    path = cell / "logs" / "run.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{_now()} {message}\n")


# ── Metrics and structural checks ───────────────────────────────────────
def _cell_done(book: Book, batch_no: int, combo_id: str) -> bool:
    with _connect(book) as connection:
        row = connection.execute(
            "SELECT 1 FROM metrics WHERE batch_no = ? AND combo_id = ?",
            (batch_no, str(combo_id))).fetchone()
    return row is not None


def structural_stats(batches_root: Path) -> dict:
    """Model-free transcript health: anchors, printed-page labels, markup.

    ``covered_pages`` mirrors :mod:`pdfoenix.verify.content`: anchors with at
    least one in-range label are trusted verbatim, wholly mislabeled anchors
    are renumbered positionally, and anchorless files fall back to their first
    page.  ``anchor_defects`` counts labels outside the batch's own range.
    """
    anchors = defects = leaks = 0
    pages: set[int] = set()
    if batches_root.exists():
        for batch in sorted(batches_root.rglob("p*.md")):
            text = batch.read_text(encoding="utf-8")
            leaks += len(_MARKUP_RE.findall(text))
            match = _BATCH_STEM_RE.match(batch.stem)
            if not match:
                continue
            first = int(match.group(1))
            expected = set(range(first, int(match.group(2) or first) + 1))
            found = [int(value) for value in _ANCHOR_RE.findall(text)]
            anchors += len(found)
            if not found:
                pages.add(first)
            elif set(found) & expected:
                defects += sum(1 for page in found if page not in expected)
                pages.update(set(found) & expected)
            else:
                defects += len(found)   # every label was wrong (printed pages)
                pages.update(sorted(expected)[:len(found)])
    return {"anchor_pages": anchors, "anchor_defects": defects,
            "markup_leaks": leaks, "covered_pages": pages}


def compute_metrics(book: Book, batch_no: int, combo_id: str,
                    batches_root: Path, corrections: int) -> dict:
    """Aggregate one cell's cost, coverage and quality into one metrics row."""
    pricing = load_pricing(book_root=book.root)
    ledger = Telemetry(book.root, db_path=db_path(book))
    try:
        calls = [row for row in ledger.fetch_calls()
                 if str(row["combo_id"] or "") == str(combo_id)
                 and row["batch_no"] == batch_no]
    finally:
        ledger.close()
    transcribe_cost = verify_cost = 0.0
    failed = 0
    for call in calls:
        cost = compute_cost(call["input_text_tokens"],
                            call["input_vision_tokens"],
                            call["output_tokens"],
                            pricing.get(call["model"] or "", {}))
        if call["stage"] == "verify":
            verify_cost += cost
        else:
            transcribe_cost += cost
        if call["status"] != "ok":
            failed += 1
    stats = structural_stats(batches_root)
    total = len(benchmark_pages(book))
    covered = len(stats["covered_pages"]) if stats["covered_pages"] else 0
    return {
        "pages_total": total,
        "pages_covered": min(covered, total),
        "corrections": corrections,
        "error_rate": (100 * corrections / covered) if covered else 0.0,
        "transcribe_cost": transcribe_cost,
        "verify_cost": verify_cost,
        "failed_calls": failed,
        "anchor_defects": stats["anchor_defects"],
        "markup_leaks": stats["markup_leaks"],
    }


def store_metrics(book: Book, batch_no: int, combo_id: str, metrics: dict) -> None:
    columns = ("pages_total", "pages_covered", "corrections", "error_rate",
               "transcribe_cost", "verify_cost", "failed_calls",
               "anchor_defects", "markup_leaks")
    values = [metrics[key] for key in columns]
    with _connect(book) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO metrics "
            "(batch_no, combo_id, " + ", ".join(columns) + ", updated) "
            "VALUES (?, ?, " + ", ".join("?" for _ in columns) + ", ?)",
            [batch_no, str(combo_id), *values, _now()])


# ── Display and report ──────────────────────────────────────────────────
def overview_rows(book: Book) -> list[dict]:
    """One row per batch: members, cell progress and message."""
    rows = []
    for batch in list_batches(book):
        done = 0
        for combo_id in batch["combos"]:
            if _cell_done(book, batch["batch_no"], combo_id):
                done += 1
        rows.append({
            "batch": f"batch_{batch['batch_no']}",
            "combos": " ".join(batch["combos"]) or "-",
            "cells": f"{done}/{len(batch['combos'])}",
            "status": batch["status"],
            "message": batch["message"] or "",
        })
    return rows


def cell_rows(book: Book, batch_no: int) -> list[dict]:
    """One row per combo of a batch, with models and latest metrics."""
    rows = []
    for combo_id in batch_members(book, batch_no):
        entry = combos(book).get(int(combo_id))
        pair = combo_pair(entry) if entry else None
        metrics = _metrics_of(book, batch_no, combo_id)
        rows.append(_cell_row(combo_id, pair, metrics))
    return rows


def combo_rows(book: Book, combo_ids: list[str]) -> list[dict]:
    """One row per (combo, batch) across batches for the selected combos."""
    wanted = {str(value) for value in combo_ids} if combo_ids else None
    rows = []
    for batch in list_batches(book):
        for combo_id in batch["combos"]:
            if wanted and combo_id not in wanted:
                continue
            entry = combos(book).get(int(combo_id))
            pair = combo_pair(entry) if entry else None
            metrics = _metrics_of(book, batch["batch_no"], combo_id)
            row = _cell_row(combo_id, pair, metrics)
            row["combo"] = f"{combo_id} (batch_{batch['batch_no']})"
            rows.append(row)
    return rows


def _cell_row(combo_id: str, pair: dict | None, metrics: dict | None) -> dict:
    row = {"combo": combo_id, "transcribe": "-", "thinking": "-",
           "verify": "-", "verify_thinking": "-", "pages": "-",
           "corrections": "-", "error_rate": "-", "cost": "-",
           "status": "pending"}
    if pair:
        row.update({
            "transcribe": pair["transcribe"]["spec"],
            "thinking": pair["transcribe"]["thinking"],
            "verify": pair["verify"]["spec"],
            "verify_thinking": pair["verify"]["thinking"],
        })
    if metrics:
        row.update({
            "pages": f"{metrics['pages_covered']}/{metrics['pages_total']}",
            "corrections": metrics["corrections"],
            "error_rate": round(metrics["error_rate"], 1),
            "cost": round(metrics["transcribe_cost"] + metrics["verify_cost"], 4),
            "status": "done",
        })
    return row


def _metrics_of(book: Book, batch_no: int, combo_id: str) -> dict | None:
    with _connect(book) as connection:
        row = connection.execute(
            "SELECT * FROM metrics WHERE batch_no = ? AND combo_id = ?",
            (batch_no, str(combo_id))).fetchone()
    return dict(row) if row else None


def report_rows(book: Book, include_hidden: bool = False) -> list[dict]:
    """Every (batch, combo) metrics row.

    Combos no longer listed in their batch are hidden by default (their data is
    kept); ``include_hidden`` expands them.
    """
    members = {batch["batch_no"]: set(batch["combos"])
               for batch in list_batches(book)}
    rows = []
    with _connect(book) as connection:
        found = connection.execute(
            "SELECT * FROM metrics ORDER BY batch_no, combo_id").fetchall()
    for metrics in found:
        if (not include_hidden
                and str(metrics["combo_id"])
                not in members.get(metrics["batch_no"], set())):
            continue
        entry = combos(book).get(int(metrics["combo_id"]))
        pair = combo_pair(entry) if entry else None
        row = _cell_row(metrics["combo_id"], pair, dict(metrics))
        row["combo"] = f"{metrics['combo_id']} (batch_{metrics['batch_no']})"
        rows.append(row)
    return rows


def markdown_report(book: Book) -> str:
    """The comparison report: one table plus a cross-batch aggregate."""
    rows = report_rows(book)
    lines = [f"# Experiment report — {book.config['book'].get('title') or book.root.name}",
             "",
             "| " + " | ".join(CELL_COLUMNS) + " |",
             "|" + "---|" * len(CELL_COLUMNS)]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in CELL_COLUMNS) + " |")
    aggregate: dict[str, list[dict]] = {}
    for row in rows:
        aggregate.setdefault(str(row["combo"]).split(" ", 1)[0], []).append(row)
    lines += ["", "## Across batches", "",
              "| combo | runs | error_rate (mean..range) | cost (mean) |",
              "|---|---|---|---|"]
    for combo_id, items in sorted(aggregate.items()):
        rates = [item["error_rate"] for item in items if item["status"] == "done"]
        costs = [item["cost"] for item in items if item["status"] == "done"]
        if not rates:
            continue
        mean_rate = sum(rates) / len(rates)
        mean_cost = sum(costs) / len(costs)
        lines.append(
            f"| {combo_id} | {len(rates)} | {mean_rate:.1f} "
            f"({min(rates):.1f}..{max(rates):.1f}) | ${mean_cost:.4f} |")
    return "\n".join(lines) + "\n"


def write_report(book: Book) -> Path:
    ensure_workspace(book)
    path = expt_dir(book) / "report.md"
    path.write_text(markdown_report(book), encoding="utf-8")
    return path


# ── Combo creation (the only entry point: ``init --combo``) ─────────────
def load_catalog(book: Book) -> dict:
    """Provider catalog: the book's presets/providers.toml or the packaged one."""
    import tomllib

    candidates = [book.root / "presets" / "providers.toml",
                  Path(__file__).resolve().parent / "presets" / "providers.toml"]
    for candidate in candidates:
        if candidate.exists():
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
            return dict(data.get("providers", {}))
    return {}


def create_combo_wizard(book: Book, combo_id: int, *, ask=None,
                        catalog: dict | None = None) -> dict:
    """Interactively define one combo.  API keys are never asked."""
    ask = ask or input
    catalog = catalog if catalog is not None else load_catalog(book)
    print(f"combo {combo_id} — models use 'provider/model#thinking'")
    for name, entry in catalog.items():
        models = ", ".join(entry.get("models", []))
        print(f"  {name:<12} {entry.get('base_url', '')}  [{models}]")
    result = {"id": int(combo_id)}
    for role in ("transcribe", "verify"):
        spec = ""
        while not spec:
            spec = ask(f"  {role} model: ").strip()
        result[role] = spec
    if not str(expt_settings(book).get("pages") or "").strip():
        pages = ask("  benchmark pages (e.g. 220-224,507-511, empty for now): ").strip()
    else:
        pages = ""
    if pages:
        result["_pages"] = pages
    return result


def ensure_provider(book: Book, name: str, catalog: dict, *, ask=None) -> dict:
    """Return the ``[[expt.provider]]`` entry for ``name``, declaring it once."""
    ask = ask or input
    known = providers(book)
    if name in known:
        return known[name]
    preset = catalog.get(name, {})
    base_url = str(preset.get("base_url") or "").strip() or ask(
        f"  {name} base URL: ").strip()
    api_key_env = str(preset.get("api_key_env") or "").strip() or ask(
        f"  {name} key environment variable: ").strip()
    return {"name": name, "base_url": base_url, "api_key_env": api_key_env}


def save_expt_config(book: Book, *, new_providers=(), new_combos=(),
                     active: int | None = None, pages: str | None = None) -> None:
    """Persist the expt configuration to config.toml (the only write path)."""
    from pdfoenix.core.config import load_config, validate_config

    config = load_config(book.root / "config.toml")
    expt = config.setdefault("expt", {})
    declared = {str(entry["name"]) for entry in expt.get("provider", [])}
    for provider in new_providers:
        if provider["name"] not in declared:
            expt.setdefault("provider", []).append(provider)
    known = {int(entry["id"]) for entry in expt.get("combo", [])}
    for combo in new_combos:
        if int(combo["id"]) not in known:
            expt.setdefault("combo", []).append(combo)
    if pages:
        expt["pages"] = pages
    if active:
        expt["active"] = int(active)
    validate_config(config)
    write_config(book.root / "config.toml", config)


def ensure_combo(book: Book, combo_id: int, *, ask=None) -> dict:
    """``init --combo``: adopt an existing combo, or create it via the wizard."""
    ask = ask or input
    entry = combos(book).get(int(combo_id))
    if entry is not None:
        save_expt_config(book, active=int(combo_id))
        return entry

    catalog = load_catalog(book)
    created = create_combo_wizard(book, combo_id, ask=ask, catalog=catalog)
    pages = created.pop("_pages", None)
    local: dict[str, dict] = {}
    for role in ("transcribe", "verify"):
        name = parse_model_spec(created[role])[0]
        if name not in local:
            local[name] = ensure_provider(book, name, catalog, ask=ask)
    save_expt_config(book, new_providers=local.values(), new_combos=[created],
                     active=int(combo_id), pages=pages)
    _remind_exports(local.values())
    return created


def _remind_exports(providers_to_run) -> None:
    import os

    for provider in providers_to_run:
        env = provider.get("api_key_env", "")
        if env and not os.environ.get(env):
            print(f"reminder: export {env}=... before running experiments")
