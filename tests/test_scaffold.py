"""Scaffolding/init tests (TDD)."""
import fitz
import pytest

from pdfoenix.core.book import Book
from pdfoenix.scaffold import ScaffoldError, scaffold_book


def make_pdf(path, pages=2):
    doc = fitz.open()
    for number in range(1, pages + 1):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {number}")
    doc.save(path)
    doc.close()


def test_scaffold_creates_full_layout(tmp_path):
    pdf = tmp_path / "source.pdf"
    make_pdf(pdf)
    book = scaffold_book(tmp_path / "demo", pdf=pdf, title="Demo",
                         author="Author", page_offset=18)
    root = book.root
    assert book.config["book"]["title"] == "Demo"
    assert book.config["book"]["author"] == "Author"
    assert book.config["book"]["page_offset"] == 18
    assert book.config["book"]["pdf"] == "source/source.pdf"
    for relative in ("manuscript", "figures", "working/pages", "working/verify",
                     "state", "logs", "build", "styles", "presets"):
        assert (root / relative).is_dir()
    assert (root / "source" / "source.pdf").exists()
    assert (root / "presets" / "opencode.jsonc").exists()
    assert (root / "presets" / "transcribe.md").exists()
    assert (root / "presets" / "pricing.toml").exists()
    assert (root / "styles" / "epub.css").exists()
    for name in ("scout", "formulas", "figures", "tables", "corrections", "progress"):
        assert (root / "state" / f"{name}.json").exists()
    assert Book(root).config["book"]["title"] == "Demo"


def test_scaffold_is_idempotent_and_preserves_config(tmp_path):
    pdf = tmp_path / "book.pdf"
    make_pdf(pdf)
    root = tmp_path / "demo"
    scaffold_book(root, pdf=pdf, title="First")
    config_path = root / "config.toml"
    text = config_path.read_text(encoding="utf-8")
    config_path.write_text(text.replace('title = "First"', 'title = "Edited"'),
                           encoding="utf-8")
    scaffold_book(root)
    assert Book(root).config["book"]["title"] == "Edited"


def test_scaffold_rejects_missing_pdf(tmp_path):
    with pytest.raises(ScaffoldError, match="does not exist"):
        scaffold_book(tmp_path / "demo", pdf=tmp_path / "missing.pdf")
