"""Figure cropping and on-demand verification page rendering.

Why this module exists
----------------------
``merge`` registers figures in ``state/figures.json`` but leaves the actual
picture as a crop task: the scanned page contains the figure, yet the EPUB needs
a standalone ``figures/figX-Y.png``.  This module materializes those files at
300 dpi, including the printed caption and excluding running heads and folios.

Crop boxes come from one of two places:

* ``state/figures.json`` entry ``crop``: ``[x0, y0, x1, y1]`` page-relative
  fractions.  This is authoritative and lets a human fix a bad automatic box
  without touching code.
* Automatic detection: the OCR text layer locates the ``Figure X.Y`` caption;
  a row-ink profile finds the whitespace band immediately above the figure, and
  a column-ink profile tightens the horizontal bounds.  The resolved box and
  ``crop_source: "auto"`` are written back to the registry, so the decision is
  auditable and editable.  When detection is not confident, nothing is cropped
  and a warning is returned instead of writing a bad image.

The module is deterministic and local: no model calls, no network.  It also
provides the 300 dpi verification-page renderer used by the sampling layer.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz

from pdfoenix.core.book import Book
from pdfoenix.core.render import render_pages, render_region_from_doc

#: Figure crops are always rendered at this DPI unless the caller overrides it.
DEFAULT_FIGURE_DPI = 300

# Page fractions excluded on every side when profiling ink.  This drops scan
# borders and stray marks near the paper edge.
_MARGIN = 0.04
# Gray value below which a pixel counts as ink (0 = black, 255 = white).
_INK_LEVEL = 200
# A row/column is "blank" when less than this fraction of it is ink.
_BLANK_FRACTION = 0.003
# Columns use a lower threshold so faint figure strokes are not trimmed.
_COLUMN_FRACTION = 0.0015
# The whitespace band above a figure must be at least this tall.
_MIN_GAP_FRACTION = 0.02
# Padding added around the detected content box.
_PAD_X = 0.03
_PAD_Y = 0.015
# A crop smaller than this (in either dimension) is considered a failed detect.
_MIN_SIDE_FRACTION = 0.03
# Reject implausibly tall detections (often body text, not a figure).
_MAX_HEIGHT_FRACTION = 0.62


class CropError(ValueError):
    """Raised when a crop box or figure registry entry is unusable."""


@dataclass
class CropSummary:
    """Result of one figure-cropping pass."""

    cropped: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def warn(self, figure_id: str, message: str) -> None:
        """Record a non-fatal problem for one figure."""
        self.warnings.append(f"{figure_id}: {message}")


def crop_figures(
    book: Book,
    *,
    dpi: int = DEFAULT_FIGURE_DPI,
    force: bool = False,
    auto: bool = True,
    chapter: str | None = None,
) -> CropSummary:
    """Crop every registered figure that does not have an image yet.

    Existing files are skipped unless ``force`` is true.  ``auto=False`` makes a
    missing ``crop`` entry a warning instead of triggering detection.
    ``chapter`` restricts the pass to that chapter's figures, so
    ``merge --chapter`` never touches another chapter's assets.
    """
    registry = book.state.load("figures")
    entries = registry.get("figures", {})
    summary = CropSummary()
    if not entries:
        return summary

    prefix = None
    if chapter:
        match = re.search(r"(\d+)", chapter)
        prefix = f"fig{int(match.group(1))}-" if match else None

    changed = False
    doc = fitz.open(book.pdf())
    try:
        for figure_id, entry in entries.items():
            if prefix and not figure_id.startswith(prefix):
                continue
            if not isinstance(entry, dict):
                summary.warn(figure_id, "registry entry is not an object")
                continue
            try:
                action = _crop_entry(
                    book, entry, figure_id, doc, dpi=dpi, force=force, auto=auto)
            except CropError as exc:
                summary.warn(figure_id, str(exc))
                continue
            if action == "skipped":
                summary.skipped.append(figure_id)
            else:
                summary.cropped.append(figure_id)
                changed = changed or action == "detected"
    finally:
        doc.close()

    if changed:
        book.state.save("figures", registry)
    return summary


def crop_figure(
    book: Book,
    figure_id: str,
    *,
    dpi: int = DEFAULT_FIGURE_DPI,
    force: bool = False,
    auto: bool = True,
) -> Path:
    """Crop one registered figure and return its output path."""
    registry = book.state.load("figures")
    entries = registry.get("figures", {})
    if figure_id not in entries:
        raise CropError(f"figure {figure_id!r} is not registered")
    entry = entries[figure_id]
    if not isinstance(entry, dict):
        raise CropError(f"figure {figure_id!r} registry entry is not an object")

    doc = fitz.open(book.pdf())
    try:
        action = _crop_entry(
            book, entry, figure_id, doc, dpi=dpi, force=force, auto=auto)
    finally:
        doc.close()
    if action == "detected":
        book.state.save("figures", registry)
    return _figure_path(book, entry, figure_id)


def detect_crop_box(
    pdf,
    page_no: int,
    label: str,
    *,
    analysis_dpi: int = 100,
) -> list[float] | None:
    """Detect a figure's page-relative crop box, or ``None`` when unsure.

    ``label`` is the printed figure number without the word "Figure", e.g.
    ``"8.1"``.  The caller should fall back to a manual box when this returns
    ``None``.
    """
    doc = fitz.open(Path(pdf))
    try:
        return _detect_crop_box(doc, page_no, label, analysis_dpi=analysis_dpi)
    finally:
        doc.close()


def render_verify_pages(
    book: Book,
    pages,
    *,
    dpi: int | None = None,
    force: bool = False,
) -> list[Path]:
    """Ensure 300 dpi verification images exist for ``pages``.

    Returns one path per requested page, in the requested order.  Existing
    images are reused unless ``force`` is true.  Rendering is local and costs no
    tokens.
    """
    page_numbers = list(dict.fromkeys(int(page) for page in pages))
    dpi = int(dpi if dpi is not None else book.config["pipeline"]["verify_dpi"])
    targets = {page: book.verify_dir / f"pdfpage_{page:03d}.png" for page in page_numbers}
    missing = [page for page in page_numbers
               if force or not targets[page].exists()]
    if missing:
        render_pages(book.pdf(), missing, dpi, book.verify_dir)
    return [targets[page] for page in page_numbers]


# ── Registry and file helpers ───────────────────────────────────────────
def _crop_entry(
    book: Book,
    entry: dict,
    figure_id: str,
    doc,
    *,
    dpi: int,
    force: bool,
    auto: bool,
) -> str:
    """Crop one registry entry; return ``"skipped"``, ``"cropped"`` or ``"detected"``."""
    out_path = _figure_path(book, entry, figure_id)
    if out_path.exists() and not force:
        return "skipped"

    page_no = _require_page(entry, figure_id)
    raw_box = entry.get("crop")
    if raw_box is not None:
        box = _validate_box(raw_box, figure_id)
        action = "cropped"
    elif not auto:
        raise CropError("no crop box (add 'crop' or enable automatic detection)")
    else:
        label = _figure_label(figure_id, entry)
        if label is None:
            raise CropError("cannot determine figure number for automatic detection")
        box = _detect_crop_box(doc, page_no, label)
        if box is None:
            raise CropError(
                "automatic crop detection failed; add 'crop' to state/figures.json")
        entry["crop"] = box
        entry["crop_source"] = "auto"
        action = "detected"

    _render_atomic(doc, page_no, box, dpi, out_path)
    return action


def _figure_path(book: Book, entry: dict, figure_id: str) -> Path:
    """Resolve the output path for a figure registry entry."""
    raw = entry.get("file") or f"figures/{figure_id}.png"
    path = Path(str(raw))
    return path if path.is_absolute() else book.root / path


def _require_page(entry: dict, figure_id: str) -> int:
    page = entry.get("page")
    if not isinstance(page, int) or isinstance(page, bool) or page <= 0:
        raise CropError(f"figure {figure_id!r} has no valid PDF page")
    return page


def _validate_box(raw, figure_id: str) -> list[float]:
    """Validate and normalize a page-relative crop box."""
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        raise CropError(f"figure {figure_id!r} crop must be [x0, y0, x1, y1]")
    values = []
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CropError(f"figure {figure_id!r} crop values must be numbers")
        values.append(float(value))
    x0, y0, x1, y1 = values
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise CropError(f"figure {figure_id!r} crop is outside the page or inverted")
    return values


def _figure_label(figure_id: str, entry: dict) -> str | None:
    """Printed label ("8.1") from the id or caption, if available."""
    match = re.fullmatch(r"fig(\d+)-(\d+)", figure_id)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    caption = str(entry.get("caption") or "")
    match = re.search(r"Figure\s+(\d+\.\d+)", caption)
    return match.group(1) if match else None


def _render_atomic(doc, page_no: int, box, dpi: int, out_path: Path) -> None:
    """Render to a sibling temp file, then atomically replace the target."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = out_path.with_name(out_path.name + ".tmp.png")
    try:
        render_region_from_doc(doc, page_no, box, dpi, temp)
        os.replace(temp, out_path)
    finally:
        if temp.exists():
            temp.unlink()


# ── Automatic detection ─────────────────────────────────────────────────
def _detect_crop_box(doc, page_no: int, label: str,
                     *, analysis_dpi: int = 100) -> list[float] | None:
    """Detect the crop box for one figure caption, or ``None`` when unsure."""
    if not (1 <= page_no <= doc.page_count):
        raise CropError(f"page {page_no} out of range (1..{doc.page_count})")
    page = doc[page_no - 1]
    rect = page.rect
    caption = _caption_bbox(page, label)
    if caption is None:
        return None

    caption_top = (caption[1] - rect.y0) / rect.height
    caption_bottom = (caption[3] - rect.y0) / rect.height
    if caption_top < 0.12:
        # A caption at the very top would require searching below it; this book
        # family always prints captions under figures, so require a manual box.
        return None

    pix = page.get_pixmap(dpi=analysis_dpi, colorspace=fitz.csGRAY)
    width, height, stride = pix.width, pix.height, pix.stride
    samples = pix.samples
    x_start = int(width * _MARGIN)
    x_end = int(width * (1 - _MARGIN))
    caption_limit = int(height * caption_top)

    row_ink = _row_ink(samples, stride, x_start, x_end, 0, caption_limit)
    blank = [value <= _BLANK_FRACTION for value in row_ink]
    min_gap = max(4, int(height * _MIN_GAP_FRACTION))
    gap_end = _last_blank_run_end(blank, min_gap)
    if gap_end is None or gap_end >= caption_limit:
        return None
    # The band between the whitespace and the caption must contain some ink;
    # otherwise this is an empty page area, not a figure.
    if not any(not flag for flag in blank[gap_end:caption_limit]):
        return None

    top = max(_MARGIN, gap_end / height - _PAD_Y)
    bottom = min(1 - _MARGIN, caption_bottom + _PAD_Y)
    if not 0 < top < bottom or bottom - top < _MIN_SIDE_FRACTION:
        return None
    if bottom - top > _MAX_HEIGHT_FRACTION:
        return None

    y_start = int(top * height)
    y_end = int(bottom * height)
    column_ink = _column_ink(samples, stride, x_start, x_end, y_start, y_end)
    columns = [index for index, value in enumerate(column_ink)
               if value > _COLUMN_FRACTION]
    if not columns:
        return None
    first, last = _content_span(columns, width)
    left = max(_MARGIN, (x_start + first) / width - _PAD_X)
    right = min(1 - _MARGIN, (x_start + last + 1) / width + _PAD_X)
    if right - left < _MIN_SIDE_FRACTION:
        return None

    return [round(left, 4), round(top, 4), round(right, 4), round(bottom, 4)]


def _caption_bbox(page, label: str):
    """Smallest OCR caption block that begins with the target figure number.

    Prose often mentions "Figure 8.1" too.  A real caption starts with the
    label and is usually shorter than a sentence that merely cites it, so the
    shortest beginning-with-label block wins.  Blocks that only mention the
    label mid-sentence are a last-resort fallback.
    """
    start_pattern = re.compile(
        rf"^\s*[\"'“]?\s*(?:Figure|FIGURE)\s*{re.escape(label)}\b")
    mention_pattern = re.compile(rf"\b(?:Figure|FIGURE)\s*{re.escape(label)}\b")
    starts, mentions = [], []
    for block in page.get_text("blocks"):
        if len(block) < 5:
            continue
        if len(block) > 6 and block[6] != 0:   # skip image blocks
            continue
        text = str(block[4]).strip()
        if start_pattern.search(text):
            starts.append((len(text), block[:4]))
        elif mention_pattern.search(text):
            mentions.append((len(text), block[:4]))
    candidates = starts or mentions
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[0])[1]


def _content_span(columns: list[int], width: int) -> tuple[int, int]:
    """Return the widest column group, ignoring isolated scan specks.

    Captions and figures can leave a few stray inked columns near the page
    edge.  Group columns that are close together, then keep the widest group.
    """
    max_gap = max(2, int(width * 0.06))
    groups = []
    start = previous = columns[0]
    for column in columns[1:]:
        if column - previous > max_gap:
            groups.append((start, previous))
            start = column
        previous = column
    groups.append((start, previous))
    return max(groups, key=lambda group: group[1] - group[0])


def _last_blank_run_end(blank: list[bool], min_length: int) -> int | None:
    """End index of the lowest blank run at least ``min_length`` rows tall."""
    best = None
    start = None
    for index, is_blank in enumerate(blank):
        if is_blank and start is None:
            start = index
        elif not is_blank and start is not None:
            if index - start >= min_length:
                best = index
            start = None
    if start is not None and len(blank) - start >= min_length:
        best = len(blank)
    return best


def _row_ink(samples, stride: int, x_start: int, x_end: int,
             y_start: int, y_end: int) -> list[float]:
    """Fraction of ink pixels for every row in the requested band."""
    width = max(1, x_end - x_start)
    fractions = []
    for y in range(y_start, y_end):
        base = y * stride
        row = samples[base + x_start:base + x_end]
        fractions.append(sum(1 for value in row if value < _INK_LEVEL) / width)
    return fractions


def _column_ink(samples, stride: int, x_start: int, x_end: int,
                y_start: int, y_end: int) -> list[float]:
    """Fraction of ink pixels for every column in the requested band."""
    height = max(1, y_end - y_start)
    width = max(1, x_end - x_start)
    counts = [0] * width
    for y in range(y_start, y_end):
        base = y * stride + x_start
        row = samples[base:base + width]
        for index, value in enumerate(row):
            if value < _INK_LEVEL:
                counts[index] += 1
    return [count / height for count in counts]
