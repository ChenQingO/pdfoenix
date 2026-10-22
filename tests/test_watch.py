"""Dashboard tests (TDD)."""
import io

import fitz
import pytest

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.watch import _watch_rich, dashboard_data, render_dashboard, watch


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


class FakeTTY(io.StringIO):
    """StringIO that claims to be a terminal."""

    def isatty(self):
        return True


def test_watch_plain_redraws_in_place_on_tty(tmp_path, monkeypatch):
    book = make_book(tmp_path)
    stream = FakeTTY()
    monkeypatch.setattr("sys.stdout", stream)
    monkeypatch.setattr("pdfoenix.watch._rich_available", lambda: False)
    watch(book, interval=0, iterations=2)
    output = stream.getvalue()
    assert output.count("Dashboard Book") == 2
    assert output.count("\033[H\033[J") == 2


def test_watch_plain_dedupes_when_not_a_tty(tmp_path, monkeypatch, capsys):
    book = make_book(tmp_path)
    monkeypatch.setattr("pdfoenix.watch._rich_available", lambda: False)
    watch(book, interval=0, iterations=3)
    out = capsys.readouterr().out
    assert out.count("Dashboard Book") == 1
    assert "\033[" not in out


def test_watch_plain_prints_frames_that_changed(tmp_path, monkeypatch, capsys):
    book = make_book(tmp_path)
    monkeypatch.setattr("pdfoenix.watch._rich_available", lambda: False)
    pending = [True]

    def fake_sleep(_interval):
        if pending and pending.pop():
            telemetry = Telemetry(tmp_path)
            telemetry.record_call(stage="transcribe", role="transcribe",
                                  model="m", input_text_tokens=1)
            telemetry.close()

    monkeypatch.setattr("pdfoenix.watch.time.sleep", fake_sleep)
    watch(book, interval=0, iterations=3)
    out = capsys.readouterr().out
    assert out.count("Dashboard Book") == 2


def test_watch_rich_renders_frames_in_place(tmp_path):
    pytest.importorskip("rich")
    from rich.console import Console

    book = make_book(tmp_path)
    stream = io.StringIO()
    _watch_rich(book, 0, 2, console=Console(file=stream, force_terminal=True,
                                            width=80))
    assert stream.getvalue().count("Dashboard Book") == 2
