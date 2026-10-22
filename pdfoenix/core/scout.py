"""Structure reconnaissance: scan a PDF's text layer into a draft structure map.

The output is deliberately a *draft*: OCR text is often truncated or misread, so
titles must ultimately be confirmed against page images.  ``merge`` and ``check``
use this map as an initial expectation only, and a human may edit
``state/scout.json`` afterward.  Recognition patterns can be overridden per book.
"""
import re
from typing import TypedDict

import fitz

from pdfoenix.core.book import Book


class ScoutResult(TypedDict):
    """Structure map returned by :func:`scout_pdf`.

    Using a TypedDict (instead of a plain mixed-value dict) gives callers precise
    types for each key, which keeps static analysis happy.
    """

    page_offset: int
    chapters: list[dict]
    sections: list[dict]
    figures: list[dict]
    tables: list[dict]
    equations: list[dict]

# Default regular expressions for the recognition categories.  A book may
# override any of them via ``patterns`` (e.g. a book that uses "PART" instead of
# "CHAPTER" for top-level divisions).
DEFAULT_PATTERNS = {
    "chapter": r"^CHAPTER\s+(\d+)\b(.*)$",
    "subsection": r"^(\d+\.\d+\.\d+)\.?\s+(.+)$",
    "section": r"^(\d+\.\d+)\.?\s+([A-Z].*)$",
    "problems": r"^PROBLEMS$",
    "figure": r"^(Figure\s+\d+\.\d+)",
    "table": r"^(Table\s+\d+\.\d+)",
    "equation": r"^\((\d+)\)$",
}


def _printed_page(pdf_page: int, offset: int) -> int:
    """Convert a 1-based PDF page number to the printed page number."""
    return pdf_page - offset


def scout_pdf(pdf, patterns=None, page_offset: int = 0,
              first: int = 1, last: int | None = None) -> ScoutResult:
    """Return a structure map for pages ``first.last`` (1-based, inclusive)."""
    compiled = {
        key: re.compile(value)
        for key, value in {**DEFAULT_PATTERNS, **(patterns or {})}.items()
    }
    doc = fitz.open(str(pdf))
    # Use separate typed lists while scanning; assembling the result dict at the
    # end avoids a mixed-type dict that confuses static type inference.
    chapters: list[dict] = []
    sections: list[dict] = []
    figures: list[dict] = []
    tables: list[dict] = []
    equations: list[dict] = []
    end = min(last or doc.page_count, doc.page_count)
    try:
        for page_no in range(first, end + 1):
            for raw_line in doc[page_no - 1].get_text().splitlines():
                line = raw_line.strip()
                if not line:
                    continue

                # Headings are mutually exclusive, so check the most specific
                # pattern first (subsection before section).
                if match := compiled["chapter"].match(line):
                    title = match.group(2).lstrip(".").strip()
                    chapters.append({
                        "number": match.group(1), "title": title,
                        "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})
                elif match := compiled["subsection"].match(line):
                    sections.append({
                        "number": match.group(1), "title": match.group(2).strip(),
                        "level": 3, "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})
                elif match := compiled["section"].match(line):
                    sections.append({
                        "number": match.group(1), "title": match.group(2).strip(),
                        "level": 2, "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})
                elif compiled["problems"].match(line):
                    sections.append({
                        "number": "Problems", "title": "Problems", "level": 2,
                        "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})

                # Captions and equation labels are independent of the headings above.
                if match := compiled["figure"].match(line):
                    figures.append({
                        "label": match.group(1).strip(), "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})
                if match := compiled["table"].match(line):
                    tables.append({
                        "label": match.group(1).strip(), "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})
                if match := compiled["equation"].match(line):
                    equations.append({
                        "label": f"({match.group(1)})", "pdf_page": page_no,
                        "printed_page": _printed_page(page_no, page_offset)})
    finally:
        doc.close()
    return {
        "page_offset": page_offset,
        "chapters": chapters,
        "sections": sections,
        "figures": figures,
        "tables": tables,
        "equations": equations,
    }


def scout_book(book: Book, patterns=None) -> ScoutResult:
    """Reconnoiter the whole book and persist the result to ``state/scout.json``."""
    offset = book.config["book"].get("page_offset", 0)
    data = scout_pdf(book.pdf(), patterns=patterns, page_offset=offset)
    book.state.save("scout", data)
    return data
