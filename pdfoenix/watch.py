"""Read-only live dashboard for one book (``pdfoenix watch``).

The dashboard is intentionally decoupled from the orchestrator: it renders
only what the stages already persist — batch files on disk, the units in
``state/progress.json``, and the append-only ``logs/pipeline.db``.  A frame
answers three questions at a glance: how far each chapter is (batches
transcribed, pages verified), what is running or failing right now, and what
it has cost so far.  Rich is used when installed; otherwise a plain-text
frame is printed, so the command still works in a minimal environment.
"""
import json
import sqlite3
import sys
import time
from typing import TYPE_CHECKING

from pdfoenix.core.book import Book
from pdfoenix.core.pages import batch_ranges
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.display import format_table
from pdfoenix.report import chapter_for_unit, ledger_totals, summarize_costs

if TYPE_CHECKING:
    from rich.table import Table

#: One column tuple shared by the plain and the Rich renderer.
COLUMNS = ("chapter", "stage", "transcribe", "verify", "objects", "corr", "cost")


def dashboard_data(book: Book) -> dict:
    """Collect the snapshot rendered by :func:`render_dashboard`."""
    progress = book.state.load("progress")
    scout = book.state.load("scout")
    corrections = book.state.load("corrections").get("corrections", [])
    ranges = book.chapter_ranges()
    try:
        totals = ledger_totals(book)
    except sqlite3.Error:
        totals = {"calls": 0, "tokens": 0, "cost": 0.0}
    try:
        costs = {row["key"]: row["cost_usd"]
                 for row in summarize_costs(book, by="chapter")}
    except sqlite3.Error:
        costs = {}
    agents = _active_agents(book)
    chapters = _chapter_rows(book, progress, scout, ranges,
                             corrections, costs, agents)
    totals.update({
        "agents": len(agents),
        "verified": sum(row["verified"] for row in chapters),
        "corrections": sum(row["corrections"] for row in chapters),
        "failed": sum(row["failed"] for row in chapters),
    })
    return {"title": book.config["book"].get("title") or book.root.name,
            "chapters": chapters, "totals": totals}


def render_dashboard(book: Book) -> str:
    """Plain-text dashboard frame (used by the fallback renderer and tests)."""
    data = dashboard_data(book)
    rows = [_frame_cells(row) for row in data["chapters"]]
    rule = "─" * 72
    return "\n".join([
        f"pdfoenix · {data['title']}",
        rule,
        format_table(rows, COLUMNS) if rows else "(no chapters yet)",
        rule,
        _totals_line(data["totals"]),
    ])


def watch(book: Book, *, interval: float = 1.0, iterations: int | None = None) -> None:
    """Refresh the dashboard in place until interrupted (or ``iterations`` frames).

    On a terminal the frame is redrawn at the same spot: Rich's alternate
    screen when Rich is installed, ANSI cursor moves otherwise.  When stdout is
    not a terminal (a pipe, a log file, a captured session) nothing can be
    redrawn in place, so frames are only printed when their content changed.
    """
    in_place = sys.stdout.isatty()
    if in_place and _rich_available():
        _watch_rich(book, interval, iterations)
    else:
        _watch_plain(book, interval, iterations, in_place=in_place)


def _watch_plain(book: Book, interval: float, iterations: int | None, *,
                 in_place: bool) -> None:
    """Plain-text loop: redraw at the top of the screen, or print on change."""
    last = None
    frame = 0
    try:
        while True:
            text = render_dashboard(book)
            if in_place:
                print("\033[H\033[J" + text, flush=True)
            elif text != last:
                print(text, flush=True)
            last = text
            frame += 1
            if iterations is not None and frame >= iterations:
                return
            time.sleep(interval)
    except KeyboardInterrupt:
        return


# ── Snapshot assembly ───────────────────────────────────────────────────
def _chapter_rows(book: Book, progress: dict, scout: dict,
                  ranges: dict[str, tuple[int, int]], corrections: list,
                  costs: dict, agents: set) -> list[dict]:
    chapters = progress.get("chapters", {})
    names = sorted(set(book.chapters()) | set(chapters)
                   | {f"ch{int(item['number']):02d}"
                      for item in scout.get("chapters", [])})
    verified: dict[str, int] = {}
    for page in _verified_pages(progress):
        name = next((key for key, (start, end) in ranges.items()
                     if start <= page <= end), None)
        if name:
            verified[name] = verified.get(name, 0) + 1
    corrected: dict[str, int] = {}
    for item in corrections:
        name = chapter_for_unit(str(item.get("unit_id") or ""), ranges)
        if name != "unknown":
            corrected[name] = corrected.get(name, 0) + 1
    failed = _failed_units(progress)
    size = int(book.config["pipeline"]["batch_pages"])
    rows = []
    for name in names:
        merged = chapters.get(name, {})
        span = ranges.get(name)
        row = {
            "name": name,
            "batches_done": len(book.batches(name)),
            "batches_total": (len(batch_ranges(span[0], span[1], size, None))
                              if span else 0),
            "running": sum(1 for agent in agents if agent.startswith(f"{name}/")),
            "failed": failed.get(name, 0),
            "verified": verified.get(name, 0),
            "merged": bool(merged.get("merged")),
            "formulas": int(merged.get("formulas", 0)),
            "figures": int(merged.get("figures", 0)),
            "corrections": corrected.get(name, 0),
            "cost": float(costs.get(name, 0.0)),
        }
        row["stage"] = _stage(row)
        rows.append(row)
    return rows


def _stage(row: dict) -> str:
    """The chapter's current position along the pipeline."""
    if row["verified"]:
        return "verify"
    if row["merged"]:
        return "merged"
    if row["batches_total"] and row["batches_done"] >= row["batches_total"]:
        return "merge"
    if row["batches_done"] or row["running"]:
        return "transcribe"
    return "pending"


def _verified_pages(progress: dict) -> list[int]:
    pages = []
    for unit, entry in progress.get("units", {}).items():
        if str(unit).startswith("verify/p") and entry.get("status") == "done":
            pages.append(int(str(unit).removeprefix("verify/p")))
    return pages


def _failed_units(progress: dict) -> dict[str, int]:
    """Failed units per chapter (``transcribe/chNN/pXXX-YYY`` markers)."""
    failed: dict[str, int] = {}
    for unit, entry in progress.get("units", {}).items():
        if entry.get("status") != "failed":
            continue
        parts = str(unit).split("/")
        if len(parts) >= 2:
            failed[parts[1]] = failed.get(parts[1], 0) + 1
    return failed


def _active_agents(book: Book) -> set:
    """Agent ids currently between ``agent_start`` and ``agent_done/fail``."""
    path = book.logs_dir / "pipeline.db"
    if not path.exists():
        return set()
    try:
        telemetry = Telemetry(book.root)
        try:
            events = telemetry.fetch_events()
        finally:
            telemetry.close()
    except sqlite3.Error:
        return set()
    active = set()
    for event in events:
        try:
            data = json.loads(event["payload"] or "{}")
        except json.JSONDecodeError:
            continue
        agent = data.get("agent") or data.get("unit") or "agent"
        if event["type"] == "agent_start":
            active.add(agent)
        elif event["type"] in {"agent_done", "agent_fail"}:
            active.discard(agent)
    return active


# ── Frame rendering ─────────────────────────────────────────────────────
def _frame_cells(row: dict) -> dict[str, str]:
    transcribe = f"{row['batches_done']}/{row['batches_total']}"
    if row["running"]:
        transcribe += f" {row['running']} run"
    if row["failed"]:
        transcribe += f" {row['failed']} fail"
    return {
        "chapter": row["name"],
        "stage": row["stage"],
        "transcribe": transcribe,
        "verify": f"{row['verified']} pages" if row["verified"] else "-",
        "objects": (f"{row['formulas']} eq · {row['figures']} fig"
                    if row["merged"] else "-"),
        "corr": str(row["corrections"]) if row["corrections"] else "-",
        "cost": f"${row['cost']:.2f}",
    }


def _totals_line(totals: dict) -> str:
    return " · ".join([
        f"${totals['cost']:.2f}",
        f"{totals['calls']} calls",
        f"{_format_tokens(totals['tokens'])} tokens",
        f"{_plural(totals['agents'], 'agent')} active",
        f"{totals['verified']} pages verified",
        _plural(totals["corrections"], "correction"),
        _plural(totals["failed"], "failure"),
    ])


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _format_tokens(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.0f}k"
    return str(tokens)


def _rich_available() -> bool:
    try:
        import rich  # noqa: F401
    except ImportError:
        return False
    return True


def _watch_rich(book: Book, interval: float, iterations: int | None, *,
                console=None) -> None:
    """Rich loop: one alternate screen, redrawn in place by ``Live``."""
    from rich.console import Console
    from rich.live import Live

    console = console or Console()
    frame = 0
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while True:
                live.update(_rich_table(book), refresh=True)
                frame += 1
                if iterations is not None and frame >= iterations:
                    return
                time.sleep(interval)
    except KeyboardInterrupt:
        return


def _rich_table(book: Book) -> "Table":
    """Build the Rich table for one dashboard frame."""
    from rich.table import Table
    from rich.text import Text

    data = dashboard_data(book)
    table = Table(title=f"pdfoenix · {data['title']}", expand=True)
    for column in COLUMNS:
        table.add_column(column, justify="right" if column in {"corr", "cost"}
                         else "left")
    for row in data["chapters"]:
        cells = _frame_cells(row)
        values = []
        for column in COLUMNS:
            cell = cells[column]
            if column == "transcribe" and row["failed"]:
                cell = Text(cell, style="red")
            values.append(cell)
        table.add_row(*values)
    table.caption = _totals_line(data["totals"])
    return table
