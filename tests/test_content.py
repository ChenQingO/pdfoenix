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
