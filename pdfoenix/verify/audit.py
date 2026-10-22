"""Global audit: turn one verified correction into a whole-book error sweep.

A verifier reports a correction as an ``X→Y`` pattern.  The same mistake often
recurs dozens of times, so this module greps the manuscript for ``X`` (allowing
flexible whitespace) and returns every hit.  Hits are deliberately *not*
rewritten automatically by default: they are cheap locations for a targeted
re-verification, and a human or a later batch pass decides whether to apply the
replacement.

The unicode arrow ``→`` and ASCII ``->`` are both accepted in patterns.  Only
manuscript files are scanned; legacy archives and build output are ignored.
"""
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.transcribe.protocol import Correction

_ARROWS = ("→", "->")


class AuditError(ValueError):
    """Raised when the correction registry is malformed."""


@dataclass(frozen=True)
class AuditHit:
    """One occurrence of a suspicious pattern in the manuscript."""

    path: str
    line_no: int
    column: int
    line: str
    matched: str
    pattern: str
    replacement: str
    severity: str = ""
    source: str = ""
    formula_id: str | None = None


@dataclass
class AuditReport:
    """All hits found for a set of corrections."""

    hits: list[AuditHit] = field(default_factory=list)
    patterns: list[str] = field(default_factory=list)
    files_scanned: int = 0
    skipped_patterns: list[str] = field(default_factory=list)

    @property
    def hit_count(self) -> int:
        return len(self.hits)

    def by_pattern(self) -> dict[str, list[AuditHit]]:
        """Group hits by their left-hand-side pattern."""
        grouped: dict[str, list[AuditHit]] = {}
        for hit in self.hits:
            grouped.setdefault(hit.pattern, []).append(hit)
        return grouped


def parse_pattern(pattern: str) -> tuple[str, str] | None:
    """Split ``X→Y`` (or ``X->Y``) into ``(X, Y)``; return ``None`` if invalid."""
    text = str(pattern or "").strip()
    for arrow in _ARROWS:
        if arrow in text:
            left, right = text.split(arrow, 1)
            left, right = left.strip(), right.strip()
            if left and left != right:
                return left, right
            return None
    return None


def pattern_regex(pattern_x: str) -> re.Pattern[str]:
    """Compile ``pattern_x`` with flexible whitespace between its tokens.

    The literal text is escaped, so LaTeX backslashes and braces are safe.  A
    run of spaces in the pattern matches any whitespace, which catches errors
    that moved across a line break.
    """
    tokens = [re.escape(token) for token in pattern_x.split()]
    if not tokens:
        raise AuditError("empty audit pattern")
    return re.compile(r"\s+".join(tokens))


def load_corrections(book: Book) -> list[Correction]:
    """Read ``state/corrections.json`` into :class:`Correction` objects."""
    registry = book.state.load("corrections")
    entries = registry.get("corrections", [])
    if not isinstance(entries, list):
        raise AuditError("state/corrections.json 'corrections' must be a list")
    corrections = []
    for entry in entries:
        if isinstance(entry, Correction):
            corrections.append(entry)
        elif isinstance(entry, Mapping):
            corrections.append(Correction.from_mapping(entry))
        else:
            raise AuditError(f"invalid correction entry: {entry!r}")
    return corrections


def global_audit(
    book: Book,
    corrections: Iterable[Correction | Mapping] | None = None,
) -> AuditReport:
    """Find every manuscript occurrence of every correction's ``X`` pattern."""
    if corrections is None:
        corrections = load_corrections(book)
    files = sorted(book.manuscript_dir.rglob("*.md"))
    report = AuditReport(files_scanned=len(files))
    seen_patterns: set[str] = set()

    for correction in corrections:
        item = (correction if isinstance(correction, Correction)
                else Correction.from_mapping(correction))
        parts = parse_pattern(item.pattern)
        if parts is None:
            if item.pattern:
                report.skipped_patterns.append(item.pattern)
            continue
        pattern_x, replacement = parts
        if pattern_x not in seen_patterns:
            seen_patterns.add(pattern_x)
            report.patterns.append(pattern_x)
        regex = pattern_regex(pattern_x)
        for path in files:
            text = path.read_text(encoding="utf-8")
            for line_no, line in enumerate(text.splitlines(), start=1):
                for match in regex.finditer(line):
                    report.hits.append(AuditHit(
                        path=path.relative_to(book.root).as_posix(),
                        line_no=line_no,
                        column=match.start() + 1,
                        line=line,
                        matched=match.group(0),
                        pattern=pattern_x,
                        replacement=replacement,
                        severity=item.severity,
                        source=item.source,
                        formula_id=item.formula_id,
                    ))
    return report


def apply_hits(book: Book, hits: Iterable[AuditHit],
               *, dry_run: bool = True) -> list[str]:
    """Apply replacement ``Y`` at each hit, writing files atomically.

    The default is a dry run, which only returns the relative paths that would
    change.  Hits whose matched text is no longer present are ignored, so a
    stale report cannot clobber edited prose.
    """
    by_path: dict[str, list[AuditHit]] = {}
    for hit in hits:
        by_path.setdefault(hit.path, []).append(hit)

    changed = []
    for relative, file_hits in sorted(by_path.items()):
        path = book.root / relative
        if not path.exists():
            continue
        original = path.read_text(encoding="utf-8")
        lines = original.splitlines()
        touched = False
        for hit in file_hits:
            index = hit.line_no - 1
            if not 0 <= index < len(lines):
                continue
            if hit.matched not in lines[index]:
                continue
            lines[index] = lines[index].replace(hit.matched, hit.replacement)
            touched = True
        if not touched:
            continue
        changed.append(relative)
        if not dry_run:
            _write_atomic(path, "\n".join(lines) + ("\n" if original.endswith("\n") else ""))
    return changed


def _write_atomic(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
