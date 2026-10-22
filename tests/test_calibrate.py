"""Calibration report tests (TDD)."""
from pdfoenix.calibrate import calibration_report, format_report, formula_scores
from pdfoenix.core.book import Book


def make_book(tmp_path):
    book = Book(tmp_path)
    chapter = book.manuscript_dir / "ch01"
    chapter.mkdir(parents=True)
    (chapter / "sec1-1.md").write_text(
        "## 1.1 Intro {#sec-1-1}\n\n$$\nx^2 \\qquad (1)\n$$\n\n"
        "$$\n\\frac{a}{b} \\qquad (2)\n$$\n",
        encoding="utf-8")
    book.pages_dir.mkdir(parents=True)
    for page in (1, 2, 3):
        (book.pages_dir / f"pdfpage_{page:03d}.png").write_bytes(b"png")
    book.state.save("formulas", {
        "formula_to_page": {},
        "page_to_formulas": {
            "working/pages/pdfpage_001.png": ["eq1"],
            "working/pages/pdfpage_002.png": ["eq2"],
        },
        "formulas": {"eq1": {"risk": 2}, "eq2": {"risk": 3}},
    })
    return book


def test_formula_and_page_scores(tmp_path):
    book = make_book(tmp_path)
    assert formula_scores(book) == [2, 3]
    report = calibration_report(book)
    assert report["formulas"]["n"] == 2
    assert report["formulas"]["max"] == 3
    assert report["pages"]["n"] == 3
    assert report["bands"] == {"low": 3, "medium": 0, "high": 0}


def test_format_report_mentions_bands(tmp_path):
    text = format_report(calibration_report(make_book(tmp_path)))
    assert "formula risk" in text
    assert "page risk" in text
    assert "low <= 50" in text
