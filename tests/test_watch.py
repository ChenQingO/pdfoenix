"""Dashboard tests (TDD)."""
import io

import fitz
import pytest

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.watch import _watch_rich, dashboard_data, render_dashboard, watch


def make_book(tmp_path, *, batch_pages=5):
    pdf = tmp_path / "source.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "book.pdf").write_bytes(pdf.read_bytes())
    book = Book(tmp_path, config={"book": {"title": "Dashboard Book",
                                           "pdf": "source/book.pdf"},
                                  "pipeline": {"batch_pages": batch_pages}})
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
    row = data["chapters"][0]
    assert row["name"] == "ch01"
    assert row["stage"] == "merged"
    assert row["formulas"] == 3
    assert data["totals"]["calls"] == 1
    assert data["totals"]["tokens"] == 1100
    assert data["totals"]["agents"] == 1

    text = render_dashboard(book)
    assert "Dashboard Book" in text
    assert "ch01" in text
    assert "3 eq" in text
    assert "1 agent active" in text


def test_frame_shows_batch_progress_running_and_failures(tmp_path):
    book = make_book(tmp_path, batch_pages=1)   # pages 1 and 2 -> 2 batches
    book.state.update("progress", lambda data: data["chapters"].__setitem__(
        "ch01", {"merged": False}))
    (book.batches_dir / "ch01").mkdir(parents=True)
    (book.batches_dir / "ch01" / "p1-1.md").write_text("# one\n", encoding="utf-8")
    book.state.update("progress", lambda data: data["units"].update({
        "transcribe/ch01/p2-2": {"status": "failed"},
    }))
    telemetry = Telemetry(tmp_path)
    telemetry.emit_event("agent_start", agent="ch01/p2-2")
    telemetry.close()

    row = dashboard_data(book)["chapters"][0]
    assert row["batches_done"] == 1
    assert row["batches_total"] == 2
    assert row["stage"] == "transcribe"
    assert row["running"] == 1
    assert row["failed"] == 1

    text = render_dashboard(book)
    assert "1/2" in text
    assert "1 run" in text
    assert "1 fail" in text


def test_verified_pages_and_corrections_map_to_chapters(tmp_path):
    book = make_book(tmp_path)
    book.state.update("progress", lambda data: data["units"].update({
        "verify/p1": {"status": "done"},
    }))
    book.state.update("corrections", lambda data: data["corrections"].append(
        {"unit_id": "p1-2", "severity": "introduced", "pattern": "a→b"}))

    data = dashboard_data(book)
    row = data["chapters"][0]
    assert row["verified"] == 1
    assert row["corrections"] == 1
    assert row["stage"] == "verify"
    assert data["totals"]["verified"] == 1
    assert data["totals"]["corrections"] == 1


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


def test_verify_chunk_plan_shows_page_progress_and_failures(tmp_path):
    book = make_book(tmp_path)
    book.state.update("progress", lambda data: data["units"].update({
        "verify/ch01/p1-2": {"status": "done", "pages": [1, 2]},
        "verify/ch01/p5-6": {"status": "running", "pages": [5, 6]},
        "verify/ch01/p7": {"status": "failed", "pages": [7]},
    }))

    data = dashboard_data(book)
    row = data["chapters"][0]
    assert row["verify_total"] == 5
    assert row["verify_done"] == 2
    assert row["verify_running"] == 1
    assert row["verify_failed"] == 1
    assert row["stage"] == "verify"
    assert data["totals"]["verify_total"] == 5
    assert data["totals"]["failed"] == 1

    text = render_dashboard(book)
    assert "2/5 pages" in text
    assert "1 run" in text
    assert "1 fail" in text
