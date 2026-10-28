"""Shared stage operations used by the CLI and the experiment runner.

The production pipeline (``pdfoenix transcribe`` / ``verify``) and the
experiment runner (``pdfoenix expt run``) are two orchestrators over the same
units of work: render a page window, call one model agent, record progress and
corrections.  This module owns those unit operations so both orchestrators
stay thin and cannot drift apart.

Everything here is orchestration glue; the deterministic stages live in
``pdfoenix.core`` and the model adapter in ``pdfoenix.transcribe``.
"""
import dataclasses
from datetime import UTC, datetime
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.render import render_pages
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.transcribe.protocol import Correction


# ── Ledger and adapter wiring ───────────────────────────────────────────
def production_ledger(book: Book) -> Telemetry:
    """Production ledger, tagged with the active combo for provenance."""
    from pdfoenix.combo import active_combo

    entry = active_combo(book)
    return Telemetry(book.root, combo_id=entry["id"] if entry else None)


def make_transcriber(book: Book, telemetry: Telemetry, *, config: dict | None = None,
                     max_workers: int = 1):
    """Build the model adapter (kept injectable so tests can fake the model).

    With no explicit ``config`` the active combo drives the models, which is
    the production behaviour; the experiment runner passes each combo's own
    generated configuration.  Task books always come from the book's
    ``presets/``; the generated OpenCode config lives elsewhere and must not
    move them.
    """
    from pdfoenix.transcribe.opencode import OpenCodeTranscriber

    if config is None:
        from pdfoenix.combo import active_combo, pipeline_config
        from pdfoenix.core.config import ConfigError

        entry = active_combo(book)
        if entry is None:
            raise ConfigError(
                "no active combo; declare [[expt.combo]] entries and "
                "[expt] active in config.toml")
        config = pipeline_config(book, entry)
    return OpenCodeTranscriber(book.root, config, telemetry=telemetry,
                               max_workers=max_workers,
                               presets_dir=book.root / "presets")


# ── Page windows ────────────────────────────────────────────────────────
def render_missing(book: Book, pages) -> None:
    """Render any of ``pages`` that have no page image yet (150 dpi)."""
    missing = [page for page in pages if not book.page_image(page).exists()]
    if missing:
        render_pages(book.pdf(), missing, book.config["pipeline"]["render_dpi"],
                     book.pages_dir)


def transcription_task(first: int, last: int, chapter: str | None = None) -> str:
    """The task line handed to the transcription agent for one window."""
    prefix = f"Chapter {chapter}, " if chapter else ""
    return (f"{prefix}PDF pages {first}-{last}. "
            "Transcribe these pages in order.")


def transcribe_window(book: Book, transcriber, *, first: int, last: int,
                      chapter: str | None = None) -> str:
    """Transcribe one page window and return the Markdown text."""
    pages = list(range(first, last + 1))
    render_missing(book, pages)
    images = [book.page_image(page) for page in pages]
    return transcriber.transcribe(images, transcription_task(first, last, chapter))


def verify_images(book: Book, pages, *, force: bool = False) -> list[Path]:
    """Verification images for ``pages`` at ``verify_dpi``, in order."""
    from pdfoenix.assets.crop import render_verify_pages

    return render_verify_pages(book, pages, force=force)


# ── Progress and corrections ────────────────────────────────────────────
def set_progress(book: Book, unit: str, *, status: str,
                 path: str | None = None, pages=None,
                 error: str | None = None) -> None:
    """Record one unit's status in ``state/progress.json`` (the watch feed).

    ``pages`` is stored on verification chunks so ``watch`` can show page-level
    progress (``done/total``) instead of just a unit count.  ``error`` keeps the
    deterministic validation/retry reason on failed transcribe units.
    """
    def mutate(data):
        entry = data["units"].get(unit, {})
        entry["status"] = status
        entry["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        if path:
            entry["path"] = path
        if pages is not None:
            entry["pages"] = [int(page) for page in pages]
        if error:
            entry["error"] = error
        else:
            entry.pop("error", None)
        data["units"][unit] = entry

    book.state.update("progress", mutate)


def clear_verify_plan(book: Book) -> None:
    """Drop unfinished verification chunks before a new plan is written.

    Per-page ``verify/pN`` marks stay untouched: they are the durable record of
    completed work, while ``verify/chNN/pA-pB`` chunk units are just the current
    plan and would otherwise linger after a failed or interrupted run.
    """
    def mutate(data):
        units = data.get("units", {})
        for unit in [name for name, entry in units.items()
                     if str(name).startswith("verify/")
                     and str(name).count("/") == 2
                     and entry.get("status") != "done"]:
            units.pop(unit, None)

    book.state.update("progress", mutate)


def verified_pages(book: Book) -> set[int]:
    """PDF pages already marked verified in ``state/progress.json``."""
    import re

    progress = book.state.load("progress")
    pages = set()
    for unit, entry in progress.get("units", {}).items():
        if not str(unit).startswith("verify/p") or entry.get("status") != "done":
            continue
        match = re.search(r"verify/p(\d+)", str(unit))
        if match:
            pages.add(int(match.group(1)))
    return pages


def record_corrections(telemetry: Telemetry, corrections) -> None:
    """Record corrections in the ledger only (experiment scope)."""
    for correction in corrections:
        telemetry.record_correction(
            verify_call_id=correction.call_id,
            unit_id=correction.unit_id,
            formula_id=correction.formula_id,
            severity=correction.severity,
            pattern=correction.pattern,
            source=correction.source,
        )


def save_corrections(book: Book, telemetry: Telemetry,
                     corrections: list[Correction]) -> None:
    """Persist corrections to ``state/corrections.json`` and the ledger."""
    if not corrections:
        return

    def mutate(data):
        data["corrections"].extend(dataclasses.asdict(item) for item in corrections)

    book.state.update("corrections", mutate)
    record_corrections(telemetry, corrections)


# ── Chapter layout ──────────────────────────────────────────────────────
def ensure_chapter_ranges(book: Book) -> dict[str, tuple[int, int]]:
    """Chapter page ranges, running ``scout`` first if the book is unscouted."""
    if not book.state.load("scout").get("chapters"):
        from pdfoenix.core.scout import scout_book

        scout_book(book)
    return book.chapter_ranges()
