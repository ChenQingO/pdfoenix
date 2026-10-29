"""Page rendering primitives built on PyMuPDF.

Two callers share these primitives: the pipeline stages render full pages
(transcription at 150 dpi, verification at 300 dpi) and figure cropping renders
high-DPI regions from an open document.  Rendering is fully local and
deterministic; it never calls a model.
"""
from pathlib import Path

import pymupdf as fitz


class RenderError(ValueError):
    """Raised for an out-of-range page number."""


def _check_page(doc, page_no: int) -> None:
    if not (1 <= page_no <= doc.page_count):
        raise RenderError(
            f"page {page_no} out of range (1..{doc.page_count})")


def render_pages(pdf, pages, dpi: int, out_dir) -> list[Path]:
    """Render several full pages, each as ``pdfpage_NNN.png``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(Path(pdf))
    outputs = []
    try:
        for page_no in pages:
            _check_page(doc, page_no)
            path = out_dir / f"pdfpage_{page_no:03d}.png"
            doc[page_no - 1].get_pixmap(dpi=dpi).save(str(path))
            outputs.append(path)
    finally:
        doc.close()
    return outputs


def render_region_from_doc(doc, page_no: int, box, dpi: int, out_path) -> Path:
    """Render a sub-region from an already-open document.

    Keeping the document open lets one crop pass reuse a single PDF handle for
    every figure.  ``box`` is ``(x0, y0, x1, y1)`` in page-relative fractions
    (0..1), with ``(0, 0)`` in the top-left corner.
    """
    _check_page(doc, page_no)
    page = doc[page_no - 1]
    rect = page.rect
    x0, y0, x1, y1 = box
    clip = fitz.Rect(
        rect.x0 + x0 * rect.width, rect.y0 + y0 * rect.height,
        rect.x0 + x1 * rect.width, rect.y0 + y1 * rect.height)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    page.get_pixmap(
        matrix=fitz.Matrix(dpi / 72, dpi / 72), clip=clip).save(str(out_path))
    return out_path
