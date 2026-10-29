"""Build tests (TDD): assembly, table inlining, end-to-end EPUB."""
import zipfile

import pytest

from pdfoenix.core.book import Book
from pdfoenix.core.build import (
    BuildError,
    assemble_markdown,
    build_book,
    inline_tables,
)


def make_book(tmp_path, files):
    d = tmp_path / "manuscript" / "ch01"
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    return Book(tmp_path)


def test_inline_tables(tmp_path):
    d = tmp_path / "manuscript" / "ch01"
    d.mkdir(parents=True)
    (d / "tab1-2.md").write_text(
        "**Table 1.2**. T\n\n| a |\n|---|\n| 1 |\n", encoding="utf-8")
    out = inline_tables("before\n\n<!-- TABLE 1.2 -->\n\nafter", d)
    assert "**Table 1.2**" in out and "| a |" in out
    assert "<!-- TABLE" not in out


def test_inline_missing_table_left(tmp_path):
    d = tmp_path / "manuscript" / "ch01"
    d.mkdir(parents=True)
    assert "<!-- TABLE 9.9 -->" in inline_tables("<!-- TABLE 9.9 -->", d)


def test_assemble_contains_contents_and_sections(tmp_path):
    book = make_book(tmp_path, {
        "00_chapter.md": "# Chapter 1. Intro {#ch-1}\n",
        "sec1-1.md": "## 1.1 Start {#sec-1-1}\n\nBody.\n",
    })
    md = assemble_markdown(book)
    assert "# Contents {#contents}" in md
    assert "Chapter 1. Intro" in md
    assert "## 1.1 Start" in md
    assert md.index("Chapter 1. Intro") < md.index("## 1.1 Start")


def test_build_epub_end_to_end(tmp_path):
    book = make_book(tmp_path, {
        "00_chapter.md": "# Chapter 1. Intro {#ch-1}\n",
        "sec1-1.md": (
            "## 1.1 Start {#sec-1-1}\n\n"
            "Text with note[^n1].\n\n"
            "$$\nx = 1 \\qquad (1)\n$$\n\n"
            "[^n1]: A note.\n"),
    })
    out = build_book(book)
    assert out.exists()
    z = zipfile.ZipFile(out)
    names = z.namelist()
    joined = "".join(
        z.read(n).decode("utf-8", "ignore")
        for n in names if n.endswith(".xhtml"))
    assert "1.1 Start" in joined
    assert "<math" in joined
    assert 'class="footnote-end"' in joined
    assert 'class="notes-heading"' in joined
    # nav must be removed from the spine
    opf = next(n for n in names if n.endswith(".opf"))
    assert 'itemref idref="nav"' not in z.read(opf).decode("utf-8")


@pytest.mark.parametrize(
    "out_name", ["../outside.epub", "/tmp/outside.epub", "."])
def test_build_rejects_invalid_output_path(tmp_path, out_name):
    book = make_book(tmp_path, {})

    with pytest.raises(BuildError, match="inside build"):
        build_book(book, out_name=out_name)

    assert not (book.build_dir / "book.md").exists()
