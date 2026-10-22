"""Page rendering tests (TDD)."""
import fitz
import pytest

from pdfoenix.core.render import (
    RenderError,
    render_page,
    render_pages,
    render_region,
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


def test_render_page_creates_file(tmp_path):
    pdf = make_pdf(tmp_path)
    out = tmp_path / "out" / "p1.png"
    got = render_page(pdf, 1, 72, out)
    assert got == out and out.exists()


def test_render_page_dpi_scales_size(tmp_path):
    pdf = make_pdf(tmp_path)
    a = render_page(pdf, 1, 72, tmp_path / "a.png")
    b = render_page(pdf, 1, 144, tmp_path / "b.png")
    wa = fitz.Pixmap(str(a)).width
    wb = fitz.Pixmap(str(b)).width
    assert abs(wb - 2 * wa) <= 2


def test_render_page_out_of_range(tmp_path):
    pdf = make_pdf(tmp_path, n_pages=1)
    with pytest.raises(RenderError):
        render_page(pdf, 2, 72, tmp_path / "x.png")


def test_render_pages_naming(tmp_path):
    pdf = make_pdf(tmp_path, n_pages=3)
    out = render_pages(pdf, [1, 3], 72, tmp_path / "pages")
    assert [p.name for p in out] == ["pdfpage_001.png", "pdfpage_003.png"]
    assert all(p.exists() for p in out)


def test_render_region_half_page(tmp_path):
    pdf = make_pdf(tmp_path)
    full = render_page(pdf, 1, 72, tmp_path / "full.png")
    half = render_region(pdf, 1, (0, 0, 0.5, 0.5), 72, tmp_path / "half.png")
    wf = fitz.Pixmap(str(full)).width
    wh = fitz.Pixmap(str(half)).width
    assert abs(wh - wf / 2) <= 2
