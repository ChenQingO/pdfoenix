"""Page rendering tests (TDD)."""
import fitz
import pytest

from pdfoenix.core.render import (
    RenderError,
    render_pages,
    render_region_from_doc,
)


def make_pdf(tmp_path, n_pages=2, width=400, height=500):
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page(width=width, height=height)
        page.insert_text((50, 50), f"Page {i + 1}")
    p = tmp_path / "t.pdf"
    doc.save(p)
    doc.close()
    return p


def test_render_pages_creates_files(tmp_path):
    pdf = make_pdf(tmp_path)
    out = render_pages(pdf, [1], 72, tmp_path / "out")
    assert [p.name for p in out] == ["pdfpage_001.png"]
    assert out[0].exists()


def test_render_pages_dpi_scales_size(tmp_path):
    pdf = make_pdf(tmp_path)
    a = render_pages(pdf, [1], 72, tmp_path / "a")[0]
    b = render_pages(pdf, [1], 144, tmp_path / "b")[0]
    wa = fitz.Pixmap(str(a)).width
    wb = fitz.Pixmap(str(b)).width
    assert abs(wb - 2 * wa) <= 2


def test_render_pages_out_of_range(tmp_path):
    pdf = make_pdf(tmp_path, n_pages=1)
    with pytest.raises(RenderError):
        render_pages(pdf, [2], 72, tmp_path / "x")


def test_render_pages_naming(tmp_path):
    pdf = make_pdf(tmp_path, n_pages=3)
    out = render_pages(pdf, [1, 3], 72, tmp_path / "pages")
    assert [p.name for p in out] == ["pdfpage_001.png", "pdfpage_003.png"]
    assert all(p.exists() for p in out)


def test_render_region_half_page(tmp_path):
    pdf = make_pdf(tmp_path)
    full = render_pages(pdf, [1], 72, tmp_path / "full")[0]
    doc = fitz.open(pdf)
    try:
        half = render_region_from_doc(
            doc, 1, (0, 0, 0.5, 0.5), 72, tmp_path / "half.png")
    finally:
        doc.close()
    wf = fitz.Pixmap(str(full)).width
    wh = fitz.Pixmap(str(half)).width
    assert abs(wh - wf / 2) <= 2
