"""Read-only live dashboard for one book (``pdfoenix watch``).

The dashboard is intentionally decoupled from the orchestrator: it only reads
``state/progress.json`` and the append-only ``logs/pipeline.db``.  Rich is used
when installed; otherwise a plain-text frame is printed, so the command still
works in a minimal environment.
"""
import json
import sqlite3
import sys
import time
from typing import TYPE_CHECKING

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.report import ledger_totals

if TYPE_CHECKING:
    from rich.table import Table


def dashboard_data(book: Book) -> dict:
    """Collect the small snapshot rendered by :func:`render_dashboard`."""
    progress = book.state.load("progress")
    scout = book.state.load("scout")
    chapters = _chapter_rows(book, progress, scout)
    try:
        totals = ledger_totals(book)
    except sqlite3.Error:
        totals = {"calls": 0, "tokens": 0, "cost": 0.0}
    totals["agents"] = _active_agents(book)
    return {"title": book.config["book"].get("title") or book.root.name,
            "chapters": chapters, "totals": totals}


def render_dashboard(book: Book) -> str:
    """Plain-text dashboard frame (used by the fallback renderer and tests)."""
    data = dashboard_data(book)
    lines = [f"pdfoenix · {data['title']}", "─" * 66]
    for row in data["chapters"]:
        status = row["status"]
        detail = f"{row['formulas']} formulas · {row['figures']} figs"
        lines.append(f"{row['name']:<6} {status:<10} {detail:>28}")
    totals = data["totals"]
    lines.append("─" * 66)
    lines.append(
        f"累计: ${totals['cost']:.2f} · {totals['calls']} 次调用 · "
        f"{_format_tokens(totals['tokens'])} token · {totals['agents']} 代理活跃")
    return "\n".join(lines)


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


def _chapter_rows(book: Book, progress: dict, scout: dict) -> list[dict]:
    chapters = progress.get("chapters", {})
    names = sorted(set(book.chapters()) | set(chapters))
    if not names:
        names = [f"ch{int(chapter['number']):02d}"
                 for chapter in scout.get("chapters", [])]
    spans = book.chapter_ranges()
    rows = []
    for name in names:
        merged = chapters.get(name, {})
        status = "merged" if merged.get("merged") else "pending"
        span = spans.get(name)
        rows.append({
            "name": name,
            "status": status,
            "formulas": int(merged.get("formulas", 0)),
            "figures": int(merged.get("figures", 0)),
            "pages": (span[1] - span[0] + 1) if span else 0,
        })
    return rows


def _active_agents(book: Book) -> int:
    path = book.logs_dir / "pipeline.db"
    if not path.exists():
        return 0
    try:
        telemetry = Telemetry(book.root)
        try:
            events = telemetry.fetch_events()
        finally:
            telemetry.close()
    except sqlite3.Error:
        return 0
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
    return len(active)


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

    data = dashboard_data(book)
    table = Table(title=f"pdfoenix · {data['title']}", expand=True)
    table.add_column("章")
    table.add_column("阶段")
    table.add_column("公式", justify="right")
    table.add_column("图", justify="right")
    for row in data["chapters"]:
        table.add_row(row["name"], row["status"], str(row["formulas"]),
                      str(row["figures"]))
    totals = data["totals"]
    table.caption = (
        f"${totals['cost']:.2f} · {totals['calls']} calls · "
        f"{_format_tokens(totals['tokens'])} tokens · {totals['agents']} agents")
    return table
