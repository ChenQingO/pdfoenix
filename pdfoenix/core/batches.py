"""Deterministic integrity checks for transcription batches.

A batch is the unit the model writes, so it is where corrupted output must be
stopped: missing page anchors, duplicated restart fragments, inline anchors and
unbalanced math all originate here and then poison merge, verify and build.
This module is model-free and shared by ``pdfoenix transcribe`` (write gate) and
``pdfoenix doctor`` (legacy/global audit).
"""
import re
from collections import Counter
from dataclasses import dataclass

from pdfoenix.core import mathlint
from pdfoenix.core.pages import PAGE_ANCHOR_RE, PAGE_LINE_RE

PROSE_RE = re.compile(r"[A-Za-z]{3,}[^$\\|<>]*\s+[A-Za-z]{3,}")

#: A repeated run at least this long is suspicious even without prose (it is
#: usually a restarted page rather than the same matrix printed twice).
_MIN_REPEAT_LINES = 8


@dataclass(frozen=True)
class BatchIssue:
    """One deterministic finding in a batch or chapter stream."""

    severity: str          # "error" or "warning"
    kind: str
    location: str
    message: str


def _math_issues(text: str, location: str, *,
                 allow_incomplete: bool) -> list[BatchIssue]:
    """Run the shared math gate on already-normalized text."""
    return [
        BatchIssue("error", "math", f"{location}:{issue.line}", issue.message)
        for issue in mathlint.lint(text, allow_incomplete=allow_incomplete)
    ]


def validate_batch_text(text: str, first: int, last: int, *,
                        location: str = "batch",
                        require_all_pages: bool = True) -> list[BatchIssue]:
    """Validate one batch against the page window it was asked to transcribe."""
    issues: list[BatchIssue] = []
    # Safe dialect rewrites are applied in memory; the batch file itself is
    # written by the caller, while merge/check materialize the same rewrites.
    text, _ = mathlint.normalize(text)
    issues.extend(_math_issues(text, location, allow_incomplete=True))
    lines = text.splitlines()

    strict = [(index + 1, int(match.group(1)))
              for index, line in enumerate(lines)
              if (match := PAGE_LINE_RE.match(line))]
    loose = [(index + 1, int(match.group(1)))
             for index, line in enumerate(lines)
             for match in PAGE_ANCHOR_RE.finditer(line)]
    strict_pages = [page for _, page in strict]
    strict_set = set(strict_pages)

    for line_no, page in loose:
        if not PAGE_LINE_RE.match(lines[line_no - 1]):
            issues.append(BatchIssue(
                "error", "anchor-inline", f"{location}:{line_no}",
                f"page anchor {page} must sit on its own line"))
    for page, count in Counter(strict_pages).items():
        if count > 1:
            issues.append(BatchIssue(
                "error", "anchor-duplicate", location,
                f"page {page} has {count} anchors"))
    if require_all_pages:
        for page in range(first, last + 1):
            if page not in strict_set:
                issues.append(BatchIssue(
                    "error", "anchor-missing", location,
                    f"page {page} has no anchor (batch claims {first}-{last})"))
    for page in sorted(strict_set):
        if page < first or page > last:
            issues.append(BatchIssue(
                "warning", "anchor-out-of-range", location,
                f"page {page} is outside the batch range {first}-{last}"))

    for start, second, length in _repeated_runs(lines):
        issues.append(BatchIssue(
            "error", "duplicate-block", f"{location}:{second}",
            f"{length}-line block repeats lines {start}-{start + length - 1}; "
            "the model probably restarted mid-output"))

    seen_prose: dict[str, int] = {}
    for line_no, line in enumerate(lines, 1):
        stripped = line.strip()
        if len(stripped) < 80 or not PROSE_RE.search(stripped):
            continue
        if stripped.startswith("<!--") or re.match(
                r"^\*{1,2}(Table|Figure|Significance|Source|Note|From)\b",
                stripped, re.IGNORECASE):
            continue
        if stripped in seen_prose:
            distance = line_no - seen_prose[stripped]
            # Publisher blurbs legitimately repeat hundreds of lines apart;
            # a restart repeats the same paragraph within the same window.
            if distance <= 80:
                issues.append(BatchIssue(
                    "error", "duplicate-line", f"{location}:{line_no}",
                    f"long prose line repeats line {seen_prose[stripped]}"))
        else:
            seen_prose[stripped] = line_no
    return issues


def validate_stream(texts: list[str], *, location: str = "chapter") -> list[BatchIssue]:
    """Validate invariants that only make sense across a whole chapter stream.

    Math delimiter, brace, environment and texmath checks come from the shared
    :mod:`pdfoenix.core.mathlint` gate, so a batch and the merged manuscript
    cannot disagree about what is broken.
    """
    joined, _ = mathlint.normalize("\n\n".join(texts))
    return _math_issues(joined, location, allow_incomplete=False)


def chapter_issues(book, chapter: str) -> list[BatchIssue]:
    """Audit every batch of one chapter plus the assembled stream."""
    from pdfoenix.core.pages import batch_stem

    issues: list[BatchIssue] = []
    batches = book.batches(chapter)
    texts = []
    page_batches: dict[int, list[str]] = {}
    for path in batches:
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        bounds = batch_stem(path.stem)
        location = f"{chapter}/{path.name}"
        if bounds is not None:
            issues.extend(validate_batch_text(text, bounds[0], bounds[1],
                                              location=location,
                                              require_all_pages=False))
        for match in PAGE_ANCHOR_RE.finditer(text):
            page_batches.setdefault(int(match.group(1)), []).append(path.name)
    for page, names in sorted(page_batches.items()):
        if len(names) > 1:
            issues.append(BatchIssue(
                "warning", "anchor-overlap", chapter,
                f"page {page} appears in {', '.join(sorted(names))}"))
    if texts:
        issues.extend(validate_stream(texts, location=chapter))
    span = book.chapter_ranges().get(chapter)
    if span is not None:
        missing = [page for page in range(span[0], span[1] + 1)
                   if page not in page_batches]
        if missing:
            issues.append(BatchIssue(
                "warning", "coverage", chapter,
                f"pages not transcribed: {missing}"))
    return issues


def _repeated_runs(lines: list[str]) -> list[tuple[int, int, int]]:
    """Return ``(first_line, second_line, length)`` for suspicious repeats.

    Runs made only of matrix rows repeat legitimately in this book family, so a
    run is reported only when it contains a prose line or is unusually long.
    """
    positions = [(index, line.strip()) for index, line in enumerate(lines)
                 if line.strip()]
    texts = [text for _, text in positions]
    window = 4
    seen: dict[tuple[str, ...], int] = {}
    hits: list[tuple[int, int, int]] = []
    for index in range(len(texts) - window + 1):
        key = tuple(texts[index:index + window])
        if key in seen:
            start = seen[key]
            length = window
            while (index + length < len(texts) and start + length < len(texts)
                   and texts[start + length] == texts[index + length]):
                length += 1
            first_line = positions[start][0] + 1
            second_line = positions[index][0] + 1
            body = [texts[start + offset] for offset in range(length)]
            prose = any(PROSE_RE.search(line) and len(line) >= 60 for line in body)
            line_no = positions[index][0]
            boundary = any(PAGE_LINE_RE.match(lines[j])
                           for j in range(max(0, line_no - 3), line_no))
            if prose or (length >= _MIN_REPEAT_LINES and boundary):
                hits.append((first_line, second_line, length))
        else:
            seen[key] = index
    # Keep the longest non-overlapping hits to avoid reporting every sub-run.
    hits.sort(key=lambda hit: (-hit[2], hit[0]))
    kept: list[tuple[int, int, int]] = []
    for hit in hits:
        if all(abs(hit[0] - other[0]) > hit[2] and abs(hit[1] - other[1]) > hit[2]
               for other in kept):
            kept.append(hit)
    return sorted(kept)
