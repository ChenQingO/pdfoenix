"""Book context and path resolution tests (TDD)."""
import pytest

from pdfoenix.core.book import Book, BookError, natural_key


def test_natural_key_orders_numbers():
    assert sorted(["ch10", "ch2", "ch1"], key=natural_key) == ["ch1", "ch2", "ch10"]


def test_natural_key_section_numbers():
    assert sorted(["sec9-10", "sec9-2", "sec9-1"], key=natural_key) == [
        "sec9-1", "sec9-2", "sec9-10"]


def test_natural_key_mixed_prefix_safe():
    # Different prefixes must also not raise TypeError.
    out = sorted(["ch1", "appendix", "sec2"], key=natural_key)
    assert out  # only assert that no exception is raised


def test_derived_dirs(tmp_path):
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    assert b.manuscript_dir == tmp_path / "manuscript"
    assert b.working_dir == tmp_path / "working"
    assert b.pages_dir == tmp_path / "working" / "pages"
    assert b.figures_dir == tmp_path / "figures"
    assert b.state_dir == tmp_path / "state"
    assert b.build_dir == tmp_path / "build"


def test_config_from_file(tmp_path):
    (tmp_path / "config.toml").write_text(
        '[book]\ntitle = "T"\n', encoding="utf-8")
    b = Book(tmp_path)
    assert b.config["book"]["title"] == "T"
    assert b.config["pipeline"]["batch_pages"] == 5


def test_config_injected_overrides_file(tmp_path):
    (tmp_path / "config.toml").write_text('[book]\ntitle = "file"\n', encoding="utf-8")
    b = Book(tmp_path, config={"book": {"title": "inj"}})
    assert b.config["book"]["title"] == "inj"


def test_pdf_from_config(tmp_path):
    (tmp_path / "source").mkdir()
    pdf = tmp_path / "source" / "a.pdf"
    pdf.write_bytes(b"%PDF")
    b = Book(tmp_path, config={"book": {"pdf": "source/a.pdf"}})
    assert b.pdf() == pdf


def test_pdf_single_glob(tmp_path):
    (tmp_path / "source").mkdir()
    pdf = tmp_path / "source" / "book.pdf"
    pdf.write_bytes(b"%PDF")
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    assert b.pdf() == pdf


def test_pdf_missing_raises(tmp_path):
    (tmp_path / "source").mkdir()
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    with pytest.raises(BookError):
        b.pdf()


def test_pdf_multiple_raises(tmp_path):
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "a.pdf").write_bytes(b"%PDF")
    (tmp_path / "source" / "b.pdf").write_bytes(b"%PDF")
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    with pytest.raises(BookError):
        b.pdf()


def test_chapters_union_batches_and_manuscript(tmp_path):
    (tmp_path / "working" / "batches" / "ch02").mkdir(parents=True)
    (tmp_path / "manuscript" / "ch01").mkdir(parents=True)
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    assert b.chapters() == ["ch01", "ch02"]


def test_batches_sorted(tmp_path):
    d = tmp_path / "working" / "batches" / "ch01"
    d.mkdir(parents=True)
    for n in ("p010-012", "p001-005", "p006-009"):
        (d / f"{n}.md").write_text("x")
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    names = [p.stem for p in b.batches("ch01")]
    assert names == ["p001-005", "p006-009", "p010-012"]


def test_sections_sorted_excludes_00_chapter(tmp_path):
    d = tmp_path / "manuscript" / "ch01"
    d.mkdir(parents=True)
    for n in ("00_chapter.md", "sec1-10.md", "sec1-2.md"):
        (d / n).write_text("x")
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    assert [p.name for p in b.sections("ch01")] == ["sec1-2.md", "sec1-10.md"]


def test_page_image_naming(tmp_path):
    b = Book(tmp_path, config={"book": {"pdf": ""}})
    assert b.page_image(7) == tmp_path / "working" / "pages" / "pdfpage_007.png"
