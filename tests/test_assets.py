"""Figure-cropping and verification-page tests (TDD)."""
import fitz
import pytest

from pdfoenix.assets.crop import (
    CropError,
    crop_figure,
    crop_figures,
    detect_crop_box,
    render_verify_pages,
)
from pdfoenix.core.book import Book


def make_pdf(path, *, caption=True, pages=1):
    """Create a small scanned-style page with a header, a figure and a caption."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page(width=396, height=612)
        page.insert_text((20, 40), "RUNNING HEADER")
        page.draw_rect(fitz.Rect(100, 250, 300, 380),
                       color=(0, 0, 0), fill=(0, 0, 0))
        if caption:
            page.insert_text((120, 390), "Figure 1.1. Test caption.")
    doc.save(path)
    doc.close()


def make_book(tmp_path, *, caption=True, crop=None, pages=1):
    source = tmp_path / "source"
    source.mkdir(parents=True)
    pdf = source / "book.pdf"
    make_pdf(pdf, caption=caption, pages=pages)
    book = Book(tmp_path, config={
        "book": {"pdf": "source/book.pdf"},
        "pipeline": {"verify_dpi": 100},
    })
    entry = {"file": "figures/fig1-1.png", "caption": "Figure 1.1.",
             "description": "", "page": 1}
    if crop is not None:
        entry["crop"] = crop
    book.state.save("figures", {"figures": {"fig1-1": entry}})
    return book, pdf


def image_size(path):
    pix = fitz.Pixmap(path)
    return pix.width, pix.height


def test_explicit_crop_writes_png_and_is_idempotent(tmp_path):
    book, _ = make_book(tmp_path, crop=[0.2, 0.4, 0.8, 0.7])
    summary = crop_figures(book, dpi=150)
    assert summary.cropped == ["fig1-1"]
    assert summary.skipped == []
    assert summary.warnings == []

    target = tmp_path / "figures" / "fig1-1.png"
    assert target.exists()
    width, height = image_size(target)
    assert width == pytest.approx(396 * 0.6 * 150 / 72, abs=3)
    assert height == pytest.approx(612 * 0.3 * 150 / 72, abs=3)

    again = crop_figures(book, dpi=150)
    assert again.cropped == []
    assert again.skipped == ["fig1-1"]

    forced = crop_figures(book, dpi=150, force=True)
    assert forced.cropped == ["fig1-1"]


def test_auto_detection_excludes_header_and_includes_caption(tmp_path):
    book, _ = make_book(tmp_path)
    summary = crop_figures(book, dpi=150)
    assert summary.cropped == ["fig1-1"]
    assert summary.warnings == []

    entry = book.state.load("figures")["figures"]["fig1-1"]
    assert entry["crop_source"] == "auto"
    x0, y0, x1, y1 = entry["crop"]
    assert 50 / 612 < y0 < 250 / 612
    assert y1 > 390 / 612
    assert x0 < 100 / 396 and x1 > 300 / 396
    assert (tmp_path / "figures" / "fig1-1.png").exists()


def test_detect_crop_box_returns_none_without_caption(tmp_path):
    book, pdf = make_book(tmp_path, caption=False)
    assert detect_crop_box(pdf, 1, "1.1") is None

    summary = crop_figures(book)
    assert summary.cropped == []
    assert summary.warnings and "detection failed" in summary.warnings[0]


def test_auto_disabled_requires_an_explicit_box(tmp_path):
    book, _ = make_book(tmp_path)
    summary = crop_figures(book, auto=False)
    assert summary.cropped == []
    assert summary.warnings and "no crop box" in summary.warnings[0]


def test_invalid_crop_box_is_a_warning(tmp_path):
    book, _ = make_book(tmp_path, crop=[0.8, 0.4, 0.2, 0.7])
    summary = crop_figures(book)
    assert summary.cropped == []
    assert summary.warnings and "inverted" in summary.warnings[0]
    assert not (tmp_path / "figures" / "fig1-1.png").exists()


def test_crop_figure_returns_path_and_rejects_unknown_id(tmp_path):
    book, _ = make_book(tmp_path, crop=[0.2, 0.4, 0.8, 0.7])
    path = crop_figure(book, "fig1-1", dpi=150)
    assert path == tmp_path / "figures" / "fig1-1.png"
    assert path.exists()
    with pytest.raises(CropError, match="not registered"):
        crop_figure(book, "fig9-9")


def test_detect_crop_box_checks_page_range(tmp_path):
    _, pdf = make_book(tmp_path)
    with pytest.raises(CropError, match="out of range"):
        detect_crop_box(pdf, 99, "1.1")


def test_render_verify_pages_is_cached_and_ordered(tmp_path):
    book, _ = make_book(tmp_path, pages=2)
    paths = render_verify_pages(book, [2, 1])
    assert [path.name for path in paths] == ["pdfpage_002.png", "pdfpage_001.png"]
    assert all(path.exists() for path in paths)
    width, _ = image_size(paths[0])
    assert width == pytest.approx(396 * 100 / 72, abs=3)

    again = render_verify_pages(book, [2, 1])
    assert again == paths

    forced = render_verify_pages(book, [2], force=True)
    assert forced == [paths[0]]


def test_crop_figures_can_be_limited_to_one_chapter(tmp_path):
    book, _ = make_book(tmp_path, crop=[0.2, 0.4, 0.8, 0.7])

    def add_second(data):
        data["figures"]["fig2-1"] = {
            "file": "figures/fig2-1.png", "caption": "Figure 2.1.",
            "description": "", "page": 1, "crop": [0.2, 0.4, 0.8, 0.7]}

    book.state.update("figures", add_second)
    summary = crop_figures(book, dpi=100, chapter="ch01")
    assert summary.cropped == ["fig1-1"]
    assert (tmp_path / "figures" / "fig1-1.png").exists()
    assert not (tmp_path / "figures" / "fig2-1.png").exists()
