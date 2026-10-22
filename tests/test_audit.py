"""Global audit tests (TDD)."""
from pdfoenix.core.book import Book
from pdfoenix.transcribe.protocol import Correction
from pdfoenix.verify.audit import (
    apply_hits,
    global_audit,
    load_corrections,
    parse_pattern,
    pattern_regex,
)


def make_book(tmp_path, text, corrections=None):
    book = Book(tmp_path)
    chapter = book.manuscript_dir / "ch01"
    chapter.mkdir(parents=True)
    (chapter / "sec1-1.md").write_text(text, encoding="utf-8")
    if corrections is not None:
        book.state.save("corrections", {"corrections": corrections})
    return book


def test_parse_pattern_supports_both_arrows():
    assert parse_pattern("sed→seb") == ("sed", "seb")
    assert parse_pattern("sed -> seb") == ("sed", "seb")
    assert parse_pattern("plain text") is None
    assert parse_pattern("x→x") is None


def test_pattern_regex_allows_flexible_whitespace():
    regex = pattern_regex("a b")
    assert regex.search("a   b")
    assert not regex.search("ab")


def test_global_audit_finds_all_occurrences(tmp_path):
    book = make_book(
        tmp_path,
        "The sed appears.\nAnother sed and seb.\nNo match here.\n",
    )
    report = global_audit(book, [
        Correction(unit_id="p1", severity="introduced", pattern="sed→seb",
                   source="ocr"),
    ])
    assert report.hit_count == 2
    assert report.patterns == ["sed"]
    assert report.files_scanned == 1
    assert [hit.line_no for hit in report.hits] == [1, 2]
    assert report.hits[1].replacement == "seb"
    assert report.hits[1].path == "manuscript/ch01/sec1-1.md"


def test_global_audit_loads_state_registry(tmp_path):
    book = make_book(
        tmp_path,
        "bad word here\n",
        corrections=[{
            "unit_id": "p1", "severity": "introduced", "pattern": "bad→good",
            "source": "ocr",
        }],
    )
    assert load_corrections(book)[0].pattern == "bad→good"
    report = global_audit(book)
    assert report.hit_count == 1


def test_invalid_patterns_are_skipped(tmp_path):
    book = make_book(tmp_path, "text\n")
    report = global_audit(book, [
        Correction(unit_id="p1", pattern="no arrow here"),
    ])
    assert report.hits == []
    assert report.skipped_patterns == ["no arrow here"]


def test_apply_hits_dry_run_then_real(tmp_path):
    book = make_book(tmp_path, "one sed two\nsed again\n")
    report = global_audit(book, [
        Correction(unit_id="p1", pattern="sed→seb"),
    ])
    path = tmp_path / "manuscript" / "ch01" / "sec1-1.md"

    assert apply_hits(book, report.hits) == ["manuscript/ch01/sec1-1.md"]
    assert path.read_text(encoding="utf-8") == "one sed two\nsed again\n"

    assert apply_hits(book, report.hits, dry_run=False) == [
        "manuscript/ch01/sec1-1.md"]
    assert path.read_text(encoding="utf-8") == "one seb two\nseb again\n"


def test_apply_hits_ignores_stale_hits(tmp_path):
    book = make_book(tmp_path, "already fixed\n")
    report = global_audit(book, [
        Correction(unit_id="p1", pattern="sed→seb"),
    ])
    assert apply_hits(book, report.hits, dry_run=False) == []
