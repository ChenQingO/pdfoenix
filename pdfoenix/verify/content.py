"""Extract page-anchored transcription text from ``working/batches``.

Verification needs the transcribed content that belongs to each selected page.
The merged manuscript has page anchors stripped, so the batches remain the only
place where page -> text is still explicit.  Legacy batches without anchors fall
back to their filename's first page, matching ``core.merge``'s behaviour.
"""
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.pages import PAGE_LINE_RE, batch_stem


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
