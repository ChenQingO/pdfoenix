"""CLI orchestration tests (TDD)."""
import zipfile

import fitz

from pdfoenix.cli import main
from pdfoenix.core.book import Book
from pdfoenix.core.pages import batch_ranges
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.transcribe.protocol import Correction


def make_pdf(path):
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "CHAPTER 1")
    page.insert_text((72, 100), "1.1 Introduction")
    page = doc.new_page()
    page.insert_text((72, 72), "1.2 Methods")
    page = doc.new_page()
    page.insert_text((72, 72), "plain text")
    doc.save(path)
    doc.close()


FAKE_MARKDOWN = """# Chapter 1. Test Chapter {#ch-1}

<!-- page:1 -->
## 1.1 Introduction {#sec-1-1}

Text.

$$
x^2 \\qquad (1)
$$

<!-- page:2 -->
## 1.2 Methods {#sec-1-2}

More text.

$$
\\frac{a}{b} \\qquad (1)
$$

<!-- page:3 -->
## 1.3 End {#sec-1-3}

Done.
"""


class FakeTranscriber:
    def __init__(self):
        self.transcribe_calls = []
        self.verify_calls = []

    def transcribe(self, images, task):
        self.transcribe_calls.append((images, task))
        return FAKE_MARKDOWN

    def verify(self, images, content):
        self.verify_calls.append((images, content))
        return [Correction(unit_id="p1-2", severity="introduced",
                           pattern="Text→Texts", source="ocr")]

    def close(self):
        pass


def make_epub(path):
    opf = """<package><manifest>
<item id="nav" href="nav.xhtml" properties="nav"/>
<item id="ch1" href="EPUB/text/ch1.xhtml"/>
</manifest><spine><itemref idref="ch1"/></spine></package>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip",
                         compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml",
                         '<container><rootfile full-path="content.opf"/></container>')
        archive.writestr("content.opf", opf)
        archive.writestr("nav.xhtml", "<html><body><nav/></body></html>")
        archive.writestr("EPUB/text/ch1.xhtml",
                         "<html><body><math><mi>x</mi></math></body></html>")


def init_book(tmp_path):
    pdf = tmp_path / "source.pdf"
    make_pdf(pdf)
    root = tmp_path / "book"
    assert main(["init", str(root), "--pdf", str(pdf), "--title", "CLI Book"]) == 0
    return root


def test_cli_smoke_commands(tmp_path, monkeypatch, capsys):
    root = init_book(tmp_path)
    assert main(["-C", str(root), "scout"]) == 0
    assert main(["-C", str(root), "render", "--pages", "1-3"]) == 0
    assert main(["-C", str(root), "calibrate"]) == 0
    assert main(["-C", str(root), "report"]) == 0
    assert main(["-C", str(root), "assets"]) == 0
    assert main(["-C", str(root), "check"]) == 0
    assert main(["-C", str(root), "watch", "--once"]) == 0

    epub = Book(root).build_dir / "book.epub"
    make_epub(epub)
    assert main(["-C", str(root), "inspect"]) == 0

    built = root / "build" / "fake.epub"
    built.write_bytes(b"x")
    monkeypatch.setattr("pdfoenix.cli.build_book", lambda _book, **_: built)
    assert main(["-C", str(root), "build"]) == 0
    assert "built" in capsys.readouterr().out


def test_cli_transcribe_merge_verify(tmp_path, monkeypatch):
    root = init_book(tmp_path)
    assert main(["-C", str(root), "scout"]) == 0
    fake = FakeTranscriber()
    monkeypatch.setattr("pdfoenix.cli._make_transcriber",
                        lambda _book, _telemetry: fake)

    assert main(["-C", str(root), "transcribe"]) == 0
    batch = root / "working" / "batches" / "ch01" / "p1-3.md"
    assert batch.exists()
    assert fake.transcribe_calls

    assert main(["-C", str(root), "merge"]) == 0
    assert (root / "manuscript" / "ch01" / "sec1-1.md").exists()
    assert (root / "manuscript" / "ch01" / "sec1-2.md").exists()
    assert main(["-C", str(root), "check"]) == 0

    assert main(["-C", str(root), "verify", "--full"]) == 0
    assert fake.verify_calls
    corrections = Book(root).state.load("corrections")["corrections"]
    assert len(corrections) == 1
    assert corrections[0]["pattern"] == "Text→Texts"
    assert (root / "working" / "verify" / "pdfpage_001.png").exists()

    telemetry = Telemetry(root)
    try:
        assert len(telemetry.fetch_corrections()) == 1
    finally:
        telemetry.close()


class RecordingTranscriber(FakeTranscriber):
    """Fake that also writes its calls to the ledger, like the real adapter."""

    def __init__(self, telemetry):
        super().__init__()
        self.telemetry = telemetry

    def transcribe(self, images, task):
        self.telemetry.record_call(stage="transcribe", role="transcribe",
                                   model="vendor/writer", unit_id="p2-3")
        return super().transcribe(images, task)

    def verify(self, images, content):
        self.telemetry.record_call(stage="verify", role="verify",
                                   model="vendor/checker", unit_id="p1")
        return super().verify(images, content)


def test_batch_ranges_honours_page_selection():
    assert batch_ranges(1, 10, 5, None) == [(1, 5), (6, 10)]
    assert batch_ranges(1, 3, 5, {2, 3}) == [(2, 3)]
    # A gap splits runs so no batch ever spans unselected pages.
    assert batch_ranges(1, 12, 2, {2, 3, 7, 8, 9}) == [(2, 3), (7, 8), (9, 9)]
    assert batch_ranges(1, 10, 5, set()) == []


def test_cli_transcribe_pages_selection(tmp_path, monkeypatch):
    root = init_book(tmp_path)
    assert main(["-C", str(root), "scout"]) == 0

    def factory(_book, telemetry):
        return RecordingTranscriber(telemetry)

    monkeypatch.setattr("pdfoenix.cli._make_transcriber", factory)

    assert main(["-C", str(root), "transcribe", "--pages", "2-3"]) == 0
    batches = root / "working" / "batches" / "ch01"
    assert (batches / "p2-3.md").exists()
    assert not (batches / "p1-3.md").exists()


def test_cli_verify_pages(tmp_path, monkeypatch):
    root = init_book(tmp_path)
    assert main(["-C", str(root), "scout"]) == 0

    def factory(_book, telemetry):
        return RecordingTranscriber(telemetry)

    monkeypatch.setattr("pdfoenix.cli._make_transcriber", factory)

    assert main(["-C", str(root), "transcribe"]) == 0
    assert main(["-C", str(root), "verify", "--pages", "1"]) == 0

    telemetry = Telemetry(root)
    try:
        verify_calls = [call for call in telemetry.fetch_calls()
                        if call["stage"] == "verify"]
    finally:
        telemetry.close()
    assert verify_calls
