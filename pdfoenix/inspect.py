"""Static EPUB acceptance checks (``pdfoenix inspect``).

This is a deliberately shallow structural check: it catches the failures that
actually break Apple Books (bad mimetype, nav left in the spine, missing MathML,
footnotes not converted) without pretending to be an EPUB validator.
"""
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


class InspectError(ValueError):
    """Raised when the path is not a readable EPUB archive."""


@dataclass
class InspectReport:
    path: str
    counts: Counter = field(default_factory=Counter)
    issues: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def add(self, message: str) -> None:
        self.issues.append(message)


def inspect_epub(path) -> InspectReport:
    """Inspect one ``.epub`` and return structural counts plus problems."""
    path = Path(path)
    report = InspectReport(path=str(path))
    if not path.exists():
        raise InspectError(f"EPUB does not exist: {path}")
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            report.counts["entries"] = len(names)
            _check_mimetype(archive, names, report)
            container = _read(archive, "META-INF/container.xml")
            if container is None:
                report.add("missing META-INF/container.xml")
            opf_name = _find_opf(names)
            if opf_name is None:
                report.add("missing package document (.opf)")
                return report
            opf = _read(archive, opf_name) or ""
            nav_name = _check_opf(opf, report)
            if nav_name:
                resolved = (Path(opf_name).parent / nav_name).as_posix()
                if resolved in names:
                    nav_name = resolved
                else:
                    report.add(f"nav document listed in manifest is missing: {nav_name}")
            for name in names:
                if not name.endswith(".xhtml"):
                    continue
                text = _read(archive, name) or ""
                _count_markup(name, text, report)
            if not report.counts["math"]:
                report.add("no MathML found")
    except zipfile.BadZipFile as exc:
        raise InspectError(f"not a readable EPUB zip: {path}") from exc
    return report


def _check_mimetype(archive: zipfile.ZipFile, names, report: InspectReport) -> None:
    if not names or names[0] != "mimetype":
        report.add("'mimetype' is not the first archive entry")
        return
    info = archive.getinfo("mimetype")
    if info.compress_type != zipfile.ZIP_STORED:
        report.add("'mimetype' must be stored uncompressed")
    if (archive.read("mimetype") != b"application/epub+zip"):
        report.add("wrong mimetype content")


def _find_opf(names) -> str | None:
    for name in names:
        if name.endswith(".opf"):
            return name
    return None


def _check_opf(opf: str, report: InspectReport) -> str | None:
    spine = re.search(r"<spine\b[^>]*>(.*?)</spine>", opf, re.DOTALL)
    if spine is None:
        report.add("package document has no <spine>")
    else:
        itemrefs = re.findall(r"<itemref\b[^>]*idref=\"([^\"]+)\"", spine.group(1))
        report.counts["spine"] = len(itemrefs)
        if "nav" in itemrefs:
            report.add("nav document is still in the spine")
    manifest = re.search(r"<manifest\b[^>]*>(.*?)</manifest>", opf, re.DOTALL)
    nav_name = None
    if manifest is None:
        report.add("package document has no <manifest>")
    else:
        items = re.findall(r"<item\b[^>]*>", manifest.group(1))
        report.counts["manifest"] = len(items)
        for item in items:
            if 'properties="nav"' in item:
                match = re.search(r'href="([^"]+)"', item)
                if match:
                    nav_name = match.group(1)
    if nav_name is None:
        report.add("no EPUB navigation document declared")
    return nav_name


def _count_markup(name: str, text: str, report: InspectReport) -> None:
    report.counts["xhtml"] += 1
    report.counts["math"] += len(re.findall(r"<math\b", text))
    report.counts["notes"] += len(re.findall(r'class="footnote-end"', text))
    report.counts["images"] += len(re.findall(r"<img\b", text))
    if "footnote-end" in text and "footnote-back" not in text:
        report.add(f"{name}: end notes have no back-links")


def _read(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        return archive.read(name).decode("utf-8", errors="replace")
    except KeyError:
        return None
