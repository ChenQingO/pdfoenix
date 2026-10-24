"""Page selection, batch planning and page/anchor naming (pure logic).

A *page selection* is a spec string such as ``"220-224,507-511"``; a *batch
range* is the ``(first, last)`` pair of one transcription call.  Batches never
span unselected pages: a gap in the selection starts a new run, so every batch
is contiguous and its file name (``p220-224.md``) still identifies its pages.

This module is also the single home of the naming conventions that link the
pipeline's artifacts together:

* page anchors (``<!-- page:N -->``) written by transcription,
* batch file stems (``p220-224``),
* rendered page images (``pdfpage_220.png``),
* chapter page spans derived from the scout state.

Every module that needs to parse or emit these forms imports them from here, so
the conventions can never drift apart.
"""
import re

# A page anchor, matched leniently: model output is not always pretty, so the
# anchor may sit inline.  Use PAGE_LINE_RE when the anchor must own its line.
PAGE_ANCHOR_RE = re.compile(r"<!--\s*page\s*:\s*(\d+)\s*-->")
# A page anchor occupying its own line (the form the task book demands).
PAGE_LINE_RE = re.compile(r"^\s*<!--\s*page\s*:\s*(\d+)\s*-->\s*$", re.MULTILINE)
# A batch file stem: "p220" or "p220-224".
BATCH_STEM_RE = re.compile(r"^p(\d+)(?:-(\d+))?$")
_PAGE_IMAGE_RE = re.compile(r"pdfpage_(\d+)\.png$")


def parse_pages(spec: str, total: int) -> list[int]:
    """Expand ``"19-30,40"`` into a sorted list of pages within ``1..total``."""
    pages: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left) if left else 1
            end = int(right) if right else total
            pages.update(range(start, end + 1))
        else:
            pages.add(int(part))
    return sorted(page for page in pages if 1 <= page <= total)


def batch_ranges(start: int, end: int, size: int,
                 wanted: set[int] | None) -> list[tuple[int, int]]:
    """Split a chapter into batch ranges, honouring an explicit page selection.

    With ``wanted`` unset the chapter is chunked from its first page; with a
    selection only the chosen pages are kept, gaps start a new run so a batch
    never spans unselected pages.
    """
    pages = [page for page in range(start, end + 1)
             if wanted is None or page in wanted]
    batches: list[tuple[int, int]] = []
    run: list[int] = []
    for page in pages:
        if run and page != run[-1] + 1:
            batches.extend(chunk_run(run, size))
            run = []
        run.append(page)
    batches.extend(chunk_run(run, size))
    return batches


def chunk_run(run: list[int], size: int) -> list[tuple[int, int]]:
    """Split one contiguous run into batches of at most ``size`` pages."""
    return [(chunk[0], chunk[-1])
            for chunk in (run[i:i + size] for i in range(0, len(run), size))]


def batch_stem(stem) -> tuple[int, int] | None:
    """Parse a batch file stem (``p220-224``) into its inclusive page bounds.

    A single-page stem (``p220``) yields ``(220, 220)``; anything else is
    ``None``.
    """
    match = BATCH_STEM_RE.match(str(stem))
    if not match:
        return None
    first = int(match.group(1))
    return first, int(match.group(2) or first)


def page_number(path) -> int | None:
    """Extract the PDF page number from a ``pdfpage_NNN.png`` file name."""
    match = _PAGE_IMAGE_RE.match(str(path).rsplit("/", 1)[-1])
    return int(match.group(1)) if match else None


def chapter_spans(chapters, last_page: int) -> dict[str, tuple[int, int]]:
    """Map scout chapter entries to ``chNN`` -> inclusive PDF page spans.

    ``chapters`` is the ``chapters`` list from ``state/scout.json``; each entry
    carries its first ``pdf_page`` and chapter ``number``.  The last chapter
    extends to ``last_page`` (the PDF's page count).
    """
    ordered = sorted(chapters, key=lambda item: item["pdf_page"])
    spans: dict[str, tuple[int, int]] = {}
    for index, chapter in enumerate(ordered):
        start = int(chapter["pdf_page"])
        end = (int(ordered[index + 1]["pdf_page"]) - 1
               if index + 1 < len(ordered) else last_page)
        spans[f"ch{int(chapter['number']):02d}"] = (start, max(start, end))
    return spans
