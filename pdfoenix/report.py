"""Cost reports over the SQLite ledger.

The ledger stores tokens, never money, so every report takes a pricing table
(USD per 1M tokens) and computes cost at read time.  :func:`summarize_costs`
groups calls by model or chapter (token and cost totals); experiment
comparisons live in ``pdfoenix.expt`` on the dedicated ``experiments.db``.

These functions are pure read-side helpers; the CLI decides how to render them.
"""
import re
import statistics
import tomllib
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry, compute_cost

_PAGE_UNIT_RE = re.compile(r"p(\d+)")
_QUANTILES = (10, 25, 50, 75, 90, 95, 99)


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
    ranges = book.chapter_ranges() if by == "chapter" else {}

    rows: dict[str, dict] = {}
    for call in _fetch_calls(book):
        key = call["model"] if by == "model" else chapter_for_unit(call["unit_id"], ranges)
        row = rows.setdefault(key, _empty_row(key))
        _add_call(row, call, pricing)
    return sorted(rows.values(), key=lambda row: row["key"])


def ledger_totals(book: Book, *, pricing=None) -> dict:
    """Aggregate ``calls``, ``tokens`` and ``cost`` across the whole ledger.

    This is the single cost roll-up used by the dashboard and the reports, so
    every consumer prices calls the same way.
    """
    totals = {"calls": 0, "tokens": 0, "cost": 0.0}
    pricing = pricing if pricing is not None else load_pricing(book_root=book.root)
    for call in _fetch_calls(book):
        totals["calls"] += 1
        totals["tokens"] += (int(call["input_text_tokens"] or 0)
                             + int(call["input_vision_tokens"] or 0)
                             + int(call["output_tokens"] or 0))
        totals["cost"] += call_cost(call, pricing)
    return totals


def _fetch_calls(book: Book) -> list:
    """Every ledger row, or an empty list when the book has no ledger yet."""
    if not (book.logs_dir / "pipeline.db").exists():
        return []
    telemetry = Telemetry(book.root)
    try:
        return telemetry.fetch_calls()
    finally:
        telemetry.close()


def chapter_for_unit(unit_id: str, ranges: dict[str, tuple[int, int]]) -> str:
    """Map a ``pNNN-MMM`` unit id to a chapter name using chapter page ranges."""
    match = _PAGE_UNIT_RE.search(str(unit_id or ""))
    if match is None:
        return "unknown"
    page = int(match.group(1))
    for name, (start, end) in ranges.items():
        if start <= page <= end:
            return name
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
    row["cost_usd"] += call_cost(call, pricing)


def call_cost(call, pricing: dict) -> float:
    """Cost in USD of one ledger row at the given pricing table."""
    return compute_cost(
        int(call["input_text_tokens"] or 0),
        int(call["input_vision_tokens"] or 0),
        int(call["output_tokens"] or 0),
        price_for(pricing, str(call["model"] or ""), str(call["provider"] or "")),
    )


def price_for(pricing: dict, model: str, provider: str = "") -> dict:
    """Unit prices for one model: bare ``model`` wins, then ``provider/model``."""
    for key in (model, f"{provider}/{model}" if provider else ""):
        if key and key in pricing:
            return pricing[key]
    return {}


# ── Risk calibration report (``pdfoenix report risk``) ──────────────────
def formula_scores(book: Book) -> list[int]:
    """Risk scores for every display formula in the manuscript.

    Scores use the book's effective risk weights, matching what ``merge``
    stores in ``state/formulas.json``.
    """
    from pdfoenix.core.config import risk_weights
    from pdfoenix.core.risk import risk_score

    weights = risk_weights(book.config)
    scores = []
    for path in sorted(book.manuscript_dir.rglob("*.md")):
        for latex in _display_formulas(path.read_text(encoding="utf-8")):
            scores.append(risk_score(latex, weights))
    return scores


def risk_report(book: Book) -> dict:
    """Return formula/page risk summaries and current sampling bands.

    Read-only: this never changes configuration.  It reports the empirical
    distributions so thresholds and probabilities can be recalibrated after a
    full book is available.
    """
    from pdfoenix.verify.sampling import SamplingSettings, compute_page_risks

    formulas = formula_scores(book)
    settings = SamplingSettings.from_config(book.config)
    pages = compute_page_risks(book, settings)
    page_values = list(pages.values())
    return {
        "formulas": _summary(formulas),
        "pages": _summary(page_values),
        "settings": settings,
        "bands": _band_counts(page_values, settings),
    }


def format_risk_report(report: dict) -> str:
    """Human-readable risk calibration table."""
    from pdfoenix.verify.sampling import SamplingSettings

    settings: SamplingSettings = report["settings"]
    lines = [
        "formula risk:",
        _format_summary(report["formulas"]),
        "",
        "page risk:",
        _format_summary(report["pages"]),
        "",
        (
            f"current bands: low <= {settings.low_max} (p={settings.low_prob:.0%}), "
            f"medium <= {settings.high_min} (p={settings.medium_prob:.0%}), "
            f"high > {settings.high_min} (p={settings.high_prob:.0%})"
        ),
    ]
    for name, count in report["bands"].items():
        lines.append(f"  {name}: {count} pages")
    if report["formulas"]["n"] and not report["pages"]["max"]:
        lines.append("  note: no page-level formula mapping yet; run merge first")
    return "\n".join(lines)


def _display_formulas(text: str) -> list[str]:
    lines = text.splitlines()
    formulas = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "$$":
            end = index + 1
            while end < len(lines) and lines[end].strip() != "$$":
                end += 1
            if end < len(lines):
                formulas.append("\n".join(lines[index + 1:end]))
                index = end + 1
                continue
        index += 1
    return formulas


def _summary(values: list[int]) -> dict:
    if not values:
        return {"n": 0, "mean": 0.0, "median": 0.0, "max": 0, "quantiles": {}}
    ordered = sorted(values)
    return {
        "n": len(ordered),
        "mean": round(statistics.fmean(ordered), 2),
        "median": float(statistics.median(ordered)),
        "max": ordered[-1],
        "quantiles": {
            percentile: round(_quantile(ordered, percentile), 1)
            for percentile in _QUANTILES
        },
    }


def _format_summary(summary: dict) -> str:
    if not summary["n"]:
        return "  (no data)"
    quantiles = ", ".join(f"P{q}={value:g}"
                          for q, value in summary["quantiles"].items())
    return (f"  n={summary['n']} mean={summary['mean']:g} "
            f"median={summary['median']:g} max={summary['max']}; {quantiles}")


def _quantile(ordered: list[int], percentile: int) -> float:
    position = (len(ordered) - 1) * percentile / 100
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _band_counts(values: list[int], settings) -> dict[str, int]:
    counts = {"low": 0, "medium": 0, "high": 0}
    for value in values:
        counts[settings.band(value)] += 1
    return counts
