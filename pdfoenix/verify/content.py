"""Extract page-anchored transcription text from ``working/batches``.

Verification needs the transcribed content that belongs to each selected page.
The merged manuscript has page anchors stripped, so the batches remain the only
place where page -> text is still explicit.  Legacy batches without anchors fall
back to their filename's first page, matching ``core.merge``'s behaviour.
"""
import re
from pathlib import Path

from pdfoenix.core.book import Book

PAGE_RE = re.compile(r"^\s*<!--\s*page\s*:\s*(\d+)\s*-->\s*$", re.MULTILINE)
_BATCH_RE = re.compile(r"p(\d+)-")


def collect_page_content(book: Book) -> dict[int, str]:
    """Return ``{pdf_page: markdown_text}`` from every batch in the book."""
    pages: dict[int, str] = {}
    for chapter in book.chapters():
        for batch in book.batches(chapter):
            text = batch.read_text(encoding="utf-8")
            sections = _split_batch(text, batch)
            for page, body in sections.items():
                pages[page] = (pages.get(page, "") + "\n\n" + body).strip()
    return dict(sorted(pages.items()))


def content_for_pages(book: Book, pages) -> tuple[str, list[int]]:
    """Build anchored Markdown for ``pages``; return it plus the pages found."""
    available = collect_page_content(book)
    kept = [page for page in pages if page in available]
    parts = [f"<!-- page:{page} -->\n{available[page]}" for page in kept]
    return "\n\n".join(parts), kept


def _split_batch(text: str, batch: Path) -> dict[int, str]:
    matches = list(PAGE_RE.finditer(text))
    if not matches:
        match = _BATCH_RE.match(batch.stem)
        if not match:
            return {}
        return {int(match.group(1)): text.strip()}

    sections: dict[int, str] = {}
    preamble = text[:matches[0].start()].strip()
    if preamble and preamble.startswith("<!--"):
        preamble = ""
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        page = int(match.group(1))
        body = text[match.end():end].strip()
        if index == 0 and preamble:
            body = preamble + "\n\n" + body
        sections[page] = (sections.get(page, "") + "\n\n" + body).strip()
    return sections
