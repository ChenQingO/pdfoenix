"""Read-only whole-pipeline audit (``pdfoenix doctor``).

``check`` validates the manuscript that is about to be built.  ``doctor``
answers the earlier question: is the corpus itself sound?  It audits batches
(anchor coverage, restarted output, stream balance), manuscript anchors and
links, figure registry/file/crop consistency, and failed progress units.  Every
rule is deterministic; no model is called and nothing is written.

Use it after transcription, before spending money on verification, and after
merging a legacy book.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from pdfoenix.core.anchors import (
    HEADING_ID_RE,
    SPAN_ID_RE,
    parse_reference_entries,
    reference_suggestions,
    suggest_target,
)
from pdfoenix.core.batches import chapter_issues
from pdfoenix.core.book import Book

LINK_RE = re.compile(r"\]\(#([A-Za-z0-9_-]+)\)")
PLACEHOLDER_RE = re.compile(r"<!--\s*FIGURE\b")


@dataclass(frozen=True)
class Finding:
    """One deterministic audit finding."""

    severity: str
    area: str
    location: str
    message: str


def collect_findings(book: Book) -> list[Finding]:
    """Return every finding, errors first."""
    findings: list[Finding] = []
    findings.extend(_batch_findings(book))
    findings.extend(_manuscript_findings(book))
    findings.extend(_figure_findings(book))
    findings.extend(_progress_findings(book))
    order = {"error": 0, "warning": 1}
    return sorted(findings, key=lambda item: (order.get(item.severity, 2),
                                              item.area, item.location,
                                              item.message))


def run_doctor(book: Book) -> int:
    """Print findings and return 0 when there are no errors, 1 otherwise."""
    findings = collect_findings(book)
    for finding in findings:
        print(f"{finding.severity}: {finding.area}: {finding.location}: "
              f"{finding.message}")
    errors = sum(1 for finding in findings if finding.severity == "error")
    warnings = sum(1 for finding in findings if finding.severity == "warning")
    print(f"doctor: {errors} error(s), {warnings} warning(s)")
    return 1 if errors else 0


def _batch_findings(book: Book) -> list[Finding]:
    findings = []
    for chapter in book.chapters():
        for issue in chapter_issues(book, chapter):
            findings.append(Finding(issue.severity, "batch",
                                    issue.location, issue.message))
    return findings


def _manuscript_findings(book: Book) -> list[Finding]:
    files = sorted(book.manuscript_dir.rglob("*.md"))
    if not files:
        return []
    texts = {path: path.read_text(encoding="utf-8") for path in files}
    anchor_locations: dict[str, list[str]] = defaultdict(list)
    links: dict[str, list[str]] = defaultdict(list)
    entries = []
    findings = []
    for path, text in texts.items():
        rel = str(path.relative_to(book.manuscript_dir))
        for match in HEADING_ID_RE.finditer(text):
            anchor_locations[match.group(1)].append(rel)
        for match in SPAN_ID_RE.finditer(text):
            anchor_locations[match.group(1)].append(rel)
        for target in LINK_RE.findall(text):
            links[target].append(rel)
        if "# References" in text:
            entries.extend(parse_reference_entries(text))
        if PLACEHOLDER_RE.search(text):
            findings.append(Finding(
                "error", "manuscript", rel,
                "unresolved FIGURE placeholder left in the manuscript"))

    for anchor, locations in sorted(anchor_locations.items()):
        if len(locations) > 1:
            findings.append(Finding(
                "error", "anchor", ", ".join(sorted(set(locations))),
                f"duplicate anchor id {anchor!r}"))
    ids = set(anchor_locations)
    for target, locations in sorted(links.items()):
        if target in ids:
            continue
        finding = Finding("error", "link", ", ".join(sorted(set(locations))),
                          f"dangling #{target}")
        suggestion = suggest_target(target, ids, entries)
        if suggestion is not None:
            finding = Finding("error", "link", finding.location,
                              f"dangling #{target} (resolvable as #{suggestion})")
        elif target.startswith("ref-"):
            candidates = reference_suggestions(target, entries)
            if candidates:
                options = ", ".join(entry.id for entry in candidates)
                finding = Finding("error", "link", finding.location,
                                  f"dangling #{target} (candidates: {options})")
        findings.append(finding)
    return findings


def _figure_findings(book: Book) -> list[Finding]:
    registry = book.state.load("figures").get("figures", {})
    findings = []
    known_files = set()
    for figure_id, entry in sorted(registry.items()):
        if not isinstance(entry, dict):
            continue
        raw = entry.get("file") or f"figures/{figure_id}.png"
        path = Path(str(raw))
        if not path.is_absolute():
            path = book.root / path
        known_files.add(path.name)
        if not path.exists():
            findings.append(Finding("error", "figure", figure_id,
                                    f"missing image file {raw}"))
        if not entry.get("crop"):
            findings.append(Finding("warning", "figure", figure_id,
                                    "no crop box; run 'pdfoenix assets'"))
        page = entry.get("page")
        if not isinstance(page, int) or isinstance(page, bool) \
                or not 1 <= page <= book.pdf_page_count():
            findings.append(Finding("error", "figure", figure_id,
                                    f"invalid PDF page {page!r}"))
    for path in sorted(book.root.joinpath("figures").glob("*.png")):
        if path.name not in known_files:
            findings.append(Finding("warning", "figure", path.name,
                                    "image on disk but not in state/figures.json"))
    return findings


def _progress_findings(book: Book) -> list[Finding]:
    findings = []
    units = book.state.load("progress").get("units", {})
    for unit, entry in sorted(units.items()):
        if entry.get("status") != "failed":
            continue
        detail = entry.get("error") or "no error recorded"
        findings.append(Finding("warning", "progress", str(unit),
                                f"failed unit: {detail}"))
    return findings
