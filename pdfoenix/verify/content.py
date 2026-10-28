"""Extract page-anchored transcription text from ``working/batches``.

Verification needs the transcribed content that belongs to each selected page.
The merged manuscript has page anchors stripped, so the batches remain the only
place where page -> text is still explicit.  Legacy batches without anchors fall
back to their filename's first page, matching ``core.merge``'s behaviour.

:func:`plan_verify` turns one verification selection into page-sized chunks that
pair images with exactly their own content, so the CLI can run, retry and persist
each chunk independently.
"""
import re
from dataclasses import dataclass
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.pages import PAGE_ANCHOR_RE, PAGE_LINE_RE, batch_stem


@dataclass(frozen=True)
class VerifyChunk:
    """One verification call: at most ``verify_chunk_size`` page images."""

    images: tuple[Path, ...]
    content: str
    unit_id: str
    pages: tuple[int, ...]


def plan_verify(images, content: str, chunk_size: int) -> list[VerifyChunk]:
    """Split one verification selection into aligned page chunks.

    Every chunk carries the page images and only the anchored content that
    belongs to those pages, so the verifier sees the same page set it did during
    transcription.  When the content cannot be split by page (legacy batches
    without anchors, or an image page missing from the content), the whole
    selection stays one chunk rather than risking mismatched evidence.
    """
    paths = [Path(image) for image in images]
    if not paths:
        return []
    groups = _chunked(paths, chunk_size)
    if len(groups) == 1:
        return [_chunk(paths, content)]

    sections = _page_sections(content)
    if sections is None:
        return [_chunk(paths, content)]
    preamble, by_page = sections
    if any(_image_page(image) not in by_page for image in paths):
        return [_chunk(paths, content)]

    chunks = []
    for index, group in enumerate(groups):
        parts = [preamble] if index == 0 else []
        for image in group:
            pages = _image_page(image)
            parts.append(by_page.get(pages, ""))
        chunks.append(_chunk(group, "\n".join(part for part in parts if part.strip())))
    return chunks


def collect_page_content(book: Book, batches_dir=None) -> dict[int, str]:
    """Return ``{pdf_page: markdown_text}`` from every batch below ``batches_dir``.

    ``batches_dir`` defaults to the book's shared ``working/batches``; experiment
    cells pass their own directory so combos never read each other's output.
    """
    root = Path(batches_dir) if batches_dir is not None else book.batches_dir
    pages: dict[int, str] = {}
    for batch in sorted(root.rglob("p*.md")):
        text = batch.read_text(encoding="utf-8")
        sections = _split_batch(text, batch)
        for page, body in sections.items():
            pages[page] = (pages.get(page, "") + "\n\n" + body).strip()
    return dict(sorted(pages.items()))


def content_for_pages(book: Book, pages, batches_dir=None) -> tuple[str, list[int]]:
    """Build anchored Markdown for ``pages``; return it plus the pages found."""
    available = collect_page_content(book, batches_dir=batches_dir)
    kept = [page for page in pages if page in available]
    parts = [f"<!-- page:{page} -->\n{available[page]}" for page in kept]
    return "\n\n".join(parts), kept


def anchor_labels(text: str, stem):
    """Classify a batch's page-anchor labels against its file-name bounds.

    Returns ``(matches, expected, positional)``: the page-anchor matches in
    order, the set of PDF pages the batch claims (empty when the stem carries
    no page range), and whether every label is wrong so the sections must be
    renumbered positionally.  Models sometimes copy the printed page numbers
    shown on the scan instead of the PDF page numbers; renumbering keeps the
    content recoverable and shrinks the defect to wrong labels.
    """
    matches = list(PAGE_LINE_RE.finditer(text))
    bounds = batch_stem(stem)
    expected = set(range(bounds[0], bounds[1] + 1)) if bounds else set()
    anchored = {int(match.group(1)) for match in matches}
    positional = bool(expected) and bool(matches) and not expected & anchored
    return matches, expected, positional


def _chunked(items, size: int) -> list[list]:
    """Split ``items`` into consecutive chunks of at most ``size`` items."""
    if size <= 0:
        raise ValueError("chunk size must be positive")
    return [list(items[start:start + size]) for start in range(0, len(items), size)]


def _image_page(image: Path) -> int | None:
    """Last run of digits in an image stem, e.g. ``pdfpage_399`` -> ``399``."""
    matches = _DIGITS_RE.findall(image.stem)
    return int(matches[-1]) if matches else None


def _unit_id(pages, images) -> str:
    """Human-readable unit id derived from page numbers.

    Contiguous pages keep the familiar ``p399-403`` form; sampled verification
    chunks are usually sparse, so they list every page (``p28+35+49``) instead
    of pretending a 39-page range was verified in one call.
    """
    ordered = sorted(page for page in pages if page is not None)
    if not ordered:
        return "+".join(image.stem for image in images)
    if len(ordered) == 1:
        return f"p{ordered[0]}"
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"p{ordered[0]}-{ordered[-1]}"
    return "p" + "+".join(str(page) for page in ordered)


def _chunk(images, content: str) -> VerifyChunk:
    pages = tuple(sorted(page for page in (_image_page(image) for image in images)
                         if page is not None))
    return VerifyChunk(tuple(images), content,
                       _unit_id(pages, images), pages)


def _page_sections(content: str) -> tuple[str, dict[int, str]] | None:
    """Split anchored content into ``(preamble, {page: section_text})``.

    Returns ``None`` when there are no page anchors, which tells the caller to
    fall back to a single verification call for the whole unit.
    """
    matches = list(PAGE_ANCHOR_RE.finditer(content))
    if not matches:
        return None
    preamble = content[:matches[0].start()]
    sections: dict[int, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        page = int(match.group(1))
        sections[page] = sections.get(page, "") + content[match.start():end]
    return preamble, sections


_DIGITS_RE = re.compile(r"\d+")


def _split_batch(text: str, batch: Path) -> dict[int, str]:
    """Map a batch's page sections to PDF page numbers.

    Anchors normally carry the PDF page number.  When they are wholly
    mislabeled (printed page labels), the sections are renumbered positionally
    per :func:`anchor_labels`.
    """
    matches, expected, positional = anchor_labels(text, batch.stem)
    start = min(expected) if expected else None
    if not matches:
        return {start: text.strip()} if start is not None else {}

    sections: dict[int, str] = {}
    preamble = text[:matches[0].start()].strip()
    if preamble and preamble.startswith("<!--"):
        preamble = ""
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        page = start + index if positional else int(match.group(1))
        body = text[match.end():end].strip()
        if index == 0 and preamble:
            body = preamble + "\n\n" + body
        sections[page] = (sections.get(page, "") + "\n\n" + body).strip()
    return sections
