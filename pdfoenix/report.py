"""Cost reports over the SQLite ledger.

The ledger stores tokens, never money, so every report takes a pricing table
(USD per 1M tokens) and computes cost at read time.  :func:`summarize_costs`
groups calls by model or chapter (token and cost totals); experiment
comparisons live in ``pdfoenix.expt`` on the dedicated ``experiments.db``.

These functions are pure read-side helpers; the CLI decides how to render them.
"""
import re
import tomllib
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry, compute_cost

_PAGE_UNIT_RE = re.compile(r"p(\d+)")


class ReportError(ValueError):
    """Raised when a report grouping or pricing table is invalid."""


def load_pricing(path=None, book_root=None) -> dict:
    """Load a pricing table from ``[pricing."model"]`` TOML entries.

    With no explicit path, ``<book>/presets/pricing.toml`` is preferred, then
    the packaged presets.  A missing file yields an empty table, which makes
    token totals useful but costs zero.
    """
    candidates = []
    if path is not None:
        candidates.append(Path(path))
    else:
        if book_root is not None:
            candidates.append(Path(book_root) / "presets" / "pricing.toml")
        candidates.append(Path(__file__).resolve().parent / "presets" / "pricing.toml")

    for candidate in candidates:
        if not candidate.exists():
            continue
        with open(candidate, "rb") as handle:
            data = tomllib.load(handle)
        pricing = data.get("pricing", {})
        if not isinstance(pricing, dict):
            raise ReportError("pricing.toml must contain a [pricing] table")
        return {str(model): dict(values) for model, values in pricing.items()}
    return {}


def summarize_costs(book: Book, *, by: str = "model", pricing=None) -> list[dict]:
    """Return token/cost totals grouped by ``model`` or ``chapter``."""
    if by not in {"model", "chapter"}:
        raise ReportError("by must be 'model' or 'chapter'")
    pricing = pricing if pricing is not None else load_pricing(book_root=book.root)
    scout = book.state.load("scout") if by == "chapter" else None

    rows: dict[str, dict] = {}
    telemetry = Telemetry(book.root)
    try:
        calls = telemetry.fetch_calls()
    finally:
        telemetry.close()

    for call in calls:
        key = call["model"] if by == "model" else chapter_for_unit(call["unit_id"], scout)
        row = rows.setdefault(key, _empty_row(key))
        _add_call(row, call, pricing)
    return sorted(rows.values(), key=lambda row: row["key"])


def chapter_for_unit(unit_id: str, scout: dict | None) -> str:
    """Map a ``pNNN-MMM`` unit id to a chapter name using ``scout.json``."""
    if not scout:
        return "unknown"
    match = _PAGE_UNIT_RE.search(str(unit_id or ""))
    if match is None:
        return "unknown"
    page = int(match.group(1))
    chapters = sorted(scout.get("chapters", []), key=lambda item: item["pdf_page"])
    for index, chapter in enumerate(chapters):
        start = int(chapter["pdf_page"])
        end = (int(chapters[index + 1]["pdf_page"]) - 1
               if index + 1 < len(chapters) else 10**9)
        if start <= page <= end:
            return f"ch{int(chapter['number']):02d}"
    return "unknown"


def _empty_row(key: str) -> dict:
    return {
        "key": key,
        "calls": 0,
        "n_images": 0,
        "input_text_tokens": 0,
        "input_vision_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
    }


def _add_call(row: dict, call, pricing: dict) -> None:
    row["calls"] += 1
    if "n_images" in row:
        row["n_images"] += int(call["n_images"] or 0)
    row["input_text_tokens"] += int(call["input_text_tokens"] or 0)
    row["input_vision_tokens"] += int(call["input_vision_tokens"] or 0)
    row["output_tokens"] += int(call["output_tokens"] or 0)
    price = _price_for(call, pricing)
    row["cost_usd"] += compute_cost(
        int(call["input_text_tokens"] or 0),
        int(call["input_vision_tokens"] or 0),
        int(call["output_tokens"] or 0),
        price,
    )


def _price_for(call, pricing: dict) -> dict:
    model = str(call["model"] or "")
    provider = str(call["provider"] or "")
    for key in (model, f"{provider}/{model}" if provider else ""):
        if key and key in pricing:
            return pricing[key]
    return {}
