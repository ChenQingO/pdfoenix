"""Page selection and batch planning (pure logic, shared by CLI and ``expt``).

A *page selection* is a spec string such as ``"220-224,507-511"``; a *batch
range* is the ``(first, last)`` pair of one transcription call.  Batches never
span unselected pages: a gap in the selection starts a new run, so every batch
is contiguous and its file name (``p220-224.md``) still identifies its pages.
"""
import re

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


def page_number(path) -> int | None:
    """Extract the PDF page number from a ``pdfpage_NNN.png`` file name."""
    match = _PAGE_IMAGE_RE.match(str(path).rsplit("/", 1)[-1])
    return int(match.group(1)) if match else None
