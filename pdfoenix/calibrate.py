"""Risk calibration helper (``pdfoenix calibrate``).

This does not change any configuration.  It reports the empirical formula and
page risk distributions so thresholds/probabilities can be recalibrated after a
full book is available.
"""
import statistics

from pdfoenix.core.book import Book
from pdfoenix.core.risk import risk_score
from pdfoenix.verify.sampling import SamplingSettings, compute_page_risks

_QUANTILES = (10, 25, 50, 75, 90, 95, 99)


def formula_scores(book: Book) -> list[int]:
    """Risk scores for every display formula in the manuscript."""
    scores = []
    for path in sorted(book.manuscript_dir.rglob("*.md")):
        for latex in _display_formulas(path.read_text(encoding="utf-8")):
            scores.append(risk_score(latex))
    return scores


def calibration_report(book: Book) -> dict:
    """Return formula/page risk summaries and current sampling bands."""
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


def format_report(report: dict) -> str:
    """Human-readable calibration table."""
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


def _band_counts(values: list[int], settings: SamplingSettings) -> dict[str, int]:
    counts = {"low": 0, "medium": 0, "high": 0}
    for value in values:
        counts[settings.band(value)] += 1
    return counts
