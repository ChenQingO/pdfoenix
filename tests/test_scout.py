"""Structure reconnaissance tests (TDD)."""
import fitz

from pdfoenix.core.book import Book
from pdfoenix.core.scout import scout_book, scout_pdf


def make_book_pdf(tmp_path, pages):
    """pages: list[list[str]]; each inner list is one page's lines."""
    doc = fitz.open()
    for lines in pages:
        page = doc.new_page(width=400, height=600)
        y = 60
        for line in lines:
            page.insert_text((40, y), line)
            y += 24
    p = tmp_path / "source" / "b.pdf"
    p.parent.mkdir(parents=True, exist_ok=True)
    doc.save(p)
    doc.close()
    return p


PAGES = [
    ["CHAPTER 1. INTRODUCTION", "1.1 First Section"],
    ["1.2 Second Section", "Figure 1.1 A demo", "Table 1.1 Data"],
    ["(1)", "(2)", "PROBLEMS"],
]


def test_chapters(tmp_path):
    pdf = make_book_pdf(tmp_path, PAGES)
    data = scout_pdf(pdf)
    assert len(data["chapters"]) == 1
    c = data["chapters"][0]
    assert c["number"] == "1"
    assert c["title"] == "INTRODUCTION"
    assert c["pdf_page"] == 1


def test_sections_and_problems(tmp_path):
    pdf = make_book_pdf(tmp_path, PAGES)
    data = scout_pdf(pdf)
    nums = [s["number"] for s in data["sections"]]
    assert "1.1" in nums and "1.2" in nums and "Problems" in nums
    assert data["sections"][0]["title"] == "First Section"


def test_figures_tables(tmp_path):
    pdf = make_book_pdf(tmp_path, PAGES)
    data = scout_pdf(pdf)
    assert data["figures"][0]["label"] == "Figure 1.1"
    assert data["tables"][0]["label"] == "Table 1.1"
    assert data["figures"][0]["pdf_page"] == 2


def test_equations(tmp_path):
    pdf = make_book_pdf(tmp_path, PAGES)
    data = scout_pdf(pdf)
    labels = [e["label"] for e in data["equations"]]
    assert labels == ["(1)", "(2)"]
    assert data["equations"][0]["pdf_page"] == 3


def test_page_offset_printed_page(tmp_path):
    pdf = make_book_pdf(tmp_path, PAGES)
    data = scout_pdf(pdf, page_offset=10)
    assert data["chapters"][0]["printed_page"] == 1 - 10
    assert data["figures"][0]["printed_page"] == 2 - 10


def test_page_range(tmp_path):
    pdf = make_book_pdf(tmp_path, PAGES)
    data = scout_pdf(pdf, first=2, last=2)
    assert data["chapters"] == []
    assert data["figures"] and not data["equations"]


def test_pattern_override(tmp_path):
    pdf = make_book_pdf(tmp_path, [["PART 5 Overview"]])
    data = scout_pdf(pdf, patterns={"chapter": r"^PART\s+(\d+)\s+(.+)$"})
    assert data["chapters"][0]["number"] == "5"
    assert data["chapters"][0]["title"] == "Overview"


def test_scout_book_writes_state(tmp_path):
    make_book_pdf(tmp_path, PAGES)
    book = Book(tmp_path, config={"book": {"pdf": "source/b.pdf", "page_offset": 0}})
    data = scout_book(book)
    saved = book.state.load("scout")
    assert saved["chapters"] == data["chapters"]
    assert book.state.path("scout").exists()
