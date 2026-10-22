"""Deterministic manuscript validation.

Checks are split into "global" (footnotes and link targets across the whole
manuscript) and "per chapter/file" (equation numbering, registry consistency,
page anchors, table/figure references, LaTeX residue, heading anchors).

The report distinguishes ``error`` (fails the build) from ``warning`` (e.g. a
section whose numbering does not start at 1 is suspicious but not fatal).
"""
import re
from dataclasses import dataclass, field
from pathlib import Path

from pdfoenix.core.book import Book, natural_key

LINK_RE = re.compile(r"]\(#([A-Za-z0-9_-]+)\)")
ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_-]+)")
FN_DEF_RE = re.compile(r"^\[\^([A-Za-z0-9_-]+)]", re.MULTILINE)
FN_USE_RE = re.compile(r"\[\^([A-Za-z0-9_-]+)](?!:)")
EQ_NUM_RE = re.compile(r"\\qquad\s*\((\d+)\)")
TABLE_PH_RE = re.compile(r"<!--\s*TABLE\s+([0-9A-Za-z.\-]+)\s*-->")
IMG_RE = re.compile(r"!\[[^]]*]\((figures/[^)]+)\)")
PAGE_RE = re.compile(r"<!--\s*page\s*:\s*\d+\s*-->")
HEADING_LINE_RE = re.compile(r"^#{1,3} ")


@dataclass
class CheckIssue:
    """One validation finding."""

    kind: str
    location: str
    message: str
    severity: str = "error"      # "error" or "warning"


@dataclass
class CheckReport:
    """Collected findings plus a few summary statistics."""

    issues: list = field(default_factory=list)
    eq_ranges: dict = field(default_factory=dict)
    footnotes: int = 0

    @property
    def ok(self) -> bool:
        """True when there are no error-severity issues."""
        return not any(issue.severity == "error" for issue in self.issues)

    def add(self, kind, location, message, severity="error"):
        self.issues.append(CheckIssue(kind, location, message, severity))


def count_display_equations(text: str) -> int:
    """Count display-equation blocks (lines that are exactly ``$$``)."""
    count, inside = 0, False
    for line in text.splitlines():
        if line.strip() == "$$":
            if not inside:
                inside = True
                count += 1
            else:
                inside = False
    return count


def section_key_from_filename(filename: str) -> str | None:
    """Infer the section key from a filename (``sec3-2.md`` -> ``"3.2"``).

    Returns ``"Problems"`` for ``secN-problems.md`` and ``None`` for files that
    are not sections (e.g. ``00_chapter.md``).
    """
    match = re.match(r"sec(\d+)-(\d+)\.md$", filename)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    if re.match(r"sec\d+-problems\.md$", filename):
        return "Problems"
    return None


def _known_dangling(book: Book) -> set[str]:
    """Load the whitelist of intentionally dangling anchor ids."""
    path = book.root / "known_dangling.txt"
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }


def _all_md(chapter_dir: Path):
    """All Markdown files in a chapter directory, in natural order."""
    return sorted(chapter_dir.glob("*.md"), key=lambda p: natural_key(p.name))


def check_book(book: Book, expected: dict | None = None) -> CheckReport:
    """Validate the whole manuscript and return a report.

    ``expected`` optionally maps ``{chapter: {filename: last_number_or_sequence}}``
    to assert exact equation numbering against an external expectation.
    """
    report = CheckReport()
    expected = expected or {}
    known_dangling = _known_dangling(book)

    # Collect every manuscript Markdown file once.
    all_text: dict[Path, str] = {}
    for chapter in book.chapters():
        directory = book.manuscript_dir / chapter
        if directory.exists():
            for path in _all_md(directory):
                all_text[path] = path.read_text(encoding="utf-8")
    references = book.manuscript_dir / "references"
    if references.exists():
        for path in sorted(references.glob("*.md"), key=lambda item: natural_key(item.name)):
            all_text[path] = path.read_text(encoding="utf-8")

    # Global: anchor ids, footnote pairing.
    anchors = set()
    for text in all_text.values():
        anchors |= set(ANCHOR_RE.findall(text))
    combined = "".join(all_text.values())
    defined = set(FN_DEF_RE.findall(combined))
    used = set(FN_USE_RE.findall(combined))
    if defined != used:
        report.add(
            "footnote", "manuscript",
            f"footnotes are unbalanced: undefined={sorted(used - defined)} "
            f"unused={sorted(defined - used)}")
    report.footnotes = len(defined)

    for chapter in book.chapters():
        directory = book.manuscript_dir / chapter
        if not directory.exists():
            continue
        registry = book.state.load("formulas")
        chapter_num = str(int(re.search(r"(\d+)", chapter).group(1)))
        registered_per_section: dict[str, int] = {}
        for formula_id, meta in registry["formulas"].items():
            if not formula_id.startswith(f"eq{chapter_num}-"):
                continue
            section = meta.get("section")
            registered_per_section[section] = registered_per_section.get(section, 0) + 1

        for path in _all_md(directory):
            text = all_text[path]
            location = f"{chapter}/{path.name}"

            if path.name.startswith("sec"):
                numbers = [int(n) for n in EQ_NUM_RE.findall(text)]
                if numbers:
                    report.eq_ranges[location] = (numbers[0], numbers[-1])
                    if numbers != list(range(numbers[0], numbers[-1] + 1)):
                        report.add(
                            "eq_gap", location,
                            f"non-contiguous numbering: {numbers}")
                    if numbers[0] != 1:
                        report.add(
                            "eq_start", location,
                            f"numbering does not start at (1): {numbers[0]}",
                            "warning")
                expected_for_file = expected.get(chapter, {}).get(path.name)
                if isinstance(expected_for_file, list) and numbers != expected_for_file:
                    report.add(
                        "eq_expected", location,
                        f"expected sequence {expected_for_file}, got {numbers}")
                elif isinstance(expected_for_file, int) and numbers \
                        and numbers[-1] != expected_for_file:
                    report.add(
                        "eq_expected", location,
                        f"expected last number {expected_for_file}, got {numbers[-1]}")

                # Registry consistency: every display block should be registered.
                section_key = section_key_from_filename(path.name)
                if section_key is not None:
                    blocks = count_display_equations(text)
                    registered = registered_per_section.get(section_key, 0)
                    if blocks != registered:
                        report.add(
                            "registry", location,
                            f"{blocks} display equations but {registered} registered")

                # Table placeholders must resolve to a table Markdown file.
                for table_id in TABLE_PH_RE.findall(text):
                    table_file = directory / f"tab{table_id.replace('.', '-')}.md"
                    if not table_file.exists():
                        report.add(
                            "table", location,
                            f"table placeholder {table_id} has no file "
                            f"{table_file.name}")

                # Page anchors must not survive into the manuscript.
                if PAGE_RE.search(text):
                    report.add("page_anchor", location,
                               "page anchor left in the manuscript")

            # Figure references must resolve to a file on disk.
            for image in IMG_RE.findall(text):
                if not (book.root / image).exists():
                    report.add("figure", location, f"missing figure file: {image}")

            # Link targets must exist (or be explicitly whitelisted).
            for target in set(LINK_RE.findall(text)):
                if target not in anchors and target not in known_dangling:
                    report.add("link", location, f"dangling link #{target}")

            # Residue checks.
            if "\\tag" in text:
                report.add("residue", location, "\\tag residue (use \\qquad (N))")
            if "\\mathcal" in text:
                report.add("residue", location, "\\mathcal residue (use \\mathscr)")
            if "■" in text:
                report.add(
                    "residue", location,
                    "bare ■ (use <span class=\"qed\">∎</span>)")
            for line in text.splitlines():
                if HEADING_LINE_RE.match(line) and "{#" not in line:
                    report.add(
                        "anchor", location,
                        f"heading without anchor: {line[:60]}")

    return report
