"""Read-only live dashboard for one book (``pdfoenix watch``).

The dashboard is intentionally decoupled from the orchestrator: it only reads
``state/progress.json`` and the append-only ``logs/pipeline.db``.  Rich is used
when installed; otherwise a plain-text frame is printed, so the command still
works in a minimal environment.
"""
import json
import sqlite3
import time

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import compute_cost
from pdfoenix.report import load_pricing


def dashboard_data(book: Book) -> dict:
    """Collect the small snapshot rendered by :func:`render_dashboard`."""
    progress = book.state.load("progress")
    scout = book.state.load("scout")
    chapters = _chapter_rows(book, progress, scout)
    totals = _ledger_totals(book)
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
    """Render the dashboard until interrupted (or ``iterations`` frames)."""
    rich_available = _rich_available()
    frame = 0
    try:
        while True:
            if rich_available:
                _render_rich(book)
            else:
                print("\033[2J\033[H" + render_dashboard(book), flush=True)
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
    page_ranges = _chapter_pages(book, scout)
    rows = []
    for name in names:
        merged = chapters.get(name, {})
        status = "merged" if merged.get("merged") else "pending"
        rows.append({
            "name": name,
            "status": status,
            "formulas": int(merged.get("formulas", 0)),
            "figures": int(merged.get("figures", 0)),
            "pages": page_ranges.get(name, 0),
        })
    return rows


def _chapter_pages(book: Book, scout: dict) -> dict[str, int]:
    chapters = sorted(scout.get("chapters", []), key=lambda item: item["pdf_page"])
    if not chapters:
        return {}
    try:
        import fitz

        with fitz.open(book.pdf()) as doc:
            last_page = doc.page_count
    except Exception:
        last_page = int(chapters[-1]["pdf_page"])
    ranges = {}
    for index, chapter in enumerate(chapters):
        start = int(chapter["pdf_page"])
        end = (int(chapters[index + 1]["pdf_page"]) - 1
               if index + 1 < len(chapters) else last_page)
        name = f"ch{int(chapter['number']):02d}"
        ranges[name] = max(0, end - start + 1)
    return ranges


def _ledger_totals(book: Book) -> dict:
    totals = {"calls": 0, "tokens": 0, "cost": 0.0}
    path = book.logs_dir / "pipeline.db"
    if not path.exists():
        return totals
    pricing = load_pricing(book_root=book.root)
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        rows = connection.execute("SELECT * FROM llm_calls").fetchall()
        connection.close()
    except sqlite3.Error:
        return totals
    for row in rows:
        totals["calls"] += 1
        text = int(row["input_text_tokens"] or 0)
        vision = int(row["input_vision_tokens"] or 0)
        output = int(row["output_tokens"] or 0)
        totals["tokens"] += text + vision + output
        price = pricing.get(row["model"] or "", {})
        totals["cost"] += compute_cost(text, vision, output, price)
    return totals


def _active_agents(book: Book) -> int:
    path = book.logs_dir / "pipeline.db"
    if not path.exists():
        return 0
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        rows = connection.execute(
            "SELECT type, payload FROM events ORDER BY id").fetchall()
        connection.close()
    except sqlite3.Error:
        return 0
    active = set()
    for event_type, payload in rows:
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            continue
        agent = data.get("agent") or data.get("unit") or "agent"
        if event_type == "agent_start":
            active.add(agent)
        elif event_type in {"agent_done", "agent_fail"}:
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


def _render_rich(book: Book) -> None:
    from rich.console import Console
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
    Console().clear()
    Console().print(table)
