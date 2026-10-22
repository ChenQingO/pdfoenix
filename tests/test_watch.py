"""Dashboard tests (TDD)."""
import fitz

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.watch import dashboard_data, render_dashboard


def make_book(tmp_path):
    pdf = tmp_path / "source.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "book.pdf").write_bytes(pdf.read_bytes())
    book = Book(tmp_path, config={"book": {"title": "Dashboard Book",
                                           "pdf": "source/book.pdf"}})
    book.state.save("scout", {
        "page_offset": 0,
        "chapters": [{"number": "1", "title": "One", "pdf_page": 1,
                      "printed_page": 1}],
        "sections": [], "figures": [], "tables": [], "equations": [],
    })
    book.state.update("progress", lambda data: data["chapters"].__setitem__(
        "ch01", {"merged": True, "formulas": 3, "figures": 2, "tables": 0}))
    return book


def test_dashboard_data_and_text(tmp_path):
    book = make_book(tmp_path)
    telemetry = Telemetry(tmp_path)
    telemetry.record_call(stage="transcribe", role="transcribe", model="m",
                          input_text_tokens=1000, output_tokens=100)
    telemetry.emit_event("agent_start", agent="ch01/p1-5")
    telemetry.close()

    data = dashboard_data(book)
    assert data["title"] == "Dashboard Book"
    assert data["chapters"][0]["name"] == "ch01"
    assert data["chapters"][0]["status"] == "merged"
    assert data["chapters"][0]["formulas"] == 3
    assert data["totals"]["calls"] == 1
    assert data["totals"]["tokens"] == 1100
    assert data["totals"]["agents"] == 1

    text = render_dashboard(book)
    assert "Dashboard Book" in text
    assert "ch01" in text
    assert "3 formulas" in text
