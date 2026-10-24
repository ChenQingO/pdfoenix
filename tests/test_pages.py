"""Page selection, batch planning and naming-convention tests (TDD)."""
from pdfoenix.core.pages import (
    batch_ranges,
    batch_stem,
    chapter_spans,
    chunk_run,
    page_number,
    parse_pages,
)


def test_parse_pages_expands_ranges_and_clamps():
    assert parse_pages("19-30,40", 100) == [*range(19, 31), 40]
    assert parse_pages("95-110", 100) == list(range(95, 101))
    assert parse_pages("3,1", 10) == [1, 3]
    assert parse_pages("-5", 10) == [1, 2, 3, 4, 5]
    assert parse_pages("", 10) == []


def test_batch_ranges_honours_page_selection():
    assert batch_ranges(1, 10, 5, None) == [(1, 5), (6, 10)]
    assert batch_ranges(1, 3, 5, {2, 3}) == [(2, 3)]
    # A gap splits runs so no batch ever spans unselected pages.
    assert batch_ranges(1, 12, 2, {2, 3, 7, 8, 9}) == [(2, 3), (7, 8), (9, 9)]
    assert batch_ranges(1, 10, 5, set()) == []


def test_chunk_run_splits_evenly():
    assert chunk_run([1, 2, 3, 4, 5], 2) == [(1, 2), (3, 4), (5, 5)]
    assert chunk_run([], 3) == []


def test_batch_stem_parses_bounds():
    assert batch_stem("p220-224") == (220, 224)
    assert batch_stem("p220") == (220, 220)
    assert batch_stem("p1-3") == (1, 3)
    assert batch_stem("TASK") is None


def test_page_number_extracts_pdf_page():
    assert page_number("working/pages/pdfpage_007.png") == 7
    assert page_number("pdfpage_123.png") == 123
    assert page_number("cover.png") is None


def test_chapter_spans_close_gaps_at_next_start():
    chapters = [
        {"number": "1", "pdf_page": 1},
        {"number": "10", "pdf_page": 100},
        {"number": "2", "pdf_page": 50},
    ]
    assert chapter_spans(chapters, 500) == {
        "ch01": (1, 49), "ch02": (50, 99), "ch10": (100, 500)}
