"""Page-content recovery tests for the verifier."""
from pdfoenix.core.book import Book
from pdfoenix.verify.content import content_for_pages


def test_all_pages_recovered_from_one_batch(tmp_path):
    directory = tmp_path / "working" / "batches" / "ch01"
    directory.mkdir(parents=True)
    (directory / "p19-21.md").write_text(
        "<!-- page:19 -->\nfirst\n\n"
        "<!-- page:20 -->\nsecond\n\n"
        "<!-- page:21 -->\nthird\n",
        encoding="utf-8",
    )
    book = Book(tmp_path)
    content, kept = content_for_pages(book, [19, 20, 21])
    assert kept == [19, 20, 21]
    assert "<!-- page:20 -->\nsecond" in content
    assert "<!-- page:21 -->\nthird" in content


def test_printed_page_anchors_are_renumbered_positionally(tmp_path):
    # Seen in a real run: the model copied the printed page numbers from the
    # scan (202-206) instead of the PDF pages in the batch name (220-224).
    directory = tmp_path / "working" / "batches" / "ch05"
    directory.mkdir(parents=True)
    (directory / "p220-224.md").write_text(
        "<!-- page:202 -->\nfirst\n\n"
        "<!-- page:203 -->\nsecond\n",
        encoding="utf-8",
    )
    book = Book(tmp_path)
    content, kept = content_for_pages(book, [220, 221, 222])
    assert kept == [220, 221]
    assert "<!-- page:220 -->\nfirst" in content
    assert "<!-- page:221 -->\nsecond" in content


def test_in_range_anchors_are_kept_verbatim(tmp_path):
    directory = tmp_path / "working" / "batches" / "ch05"
    directory.mkdir(parents=True)
    (directory / "p220-224.md").write_text(
        "<!-- page:220 -->\nfirst\n\n"
        "<!-- page:203 -->\nsecond\n",
        encoding="utf-8",
    )
    book = Book(tmp_path)
    content, kept = content_for_pages(book, [220, 203])
    assert kept == [220, 203]
    assert "<!-- page:220 -->\nfirst" in content
    assert "<!-- page:203 -->\nsecond" in content
