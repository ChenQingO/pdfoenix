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


def test_plan_verify_splits_by_page_and_keeps_content_aligned(tmp_path):
    from pdfoenix.verify.content import plan_verify

    content = ("<!-- page:19 -->\nA\n"
               "<!-- page:20 -->\nB\n"
               "<!-- page:21 -->\nC\n")
    images = [tmp_path / f"pdfpage_{page:03d}.png" for page in (19, 20, 21)]
    chunks = plan_verify(images, content, 2)

    assert [chunk.unit_id for chunk in chunks] == ["p19-20", "p21"]
    assert [chunk.pages for chunk in chunks] == [(19, 20), (21,)]
    assert "B" in chunks[0].content and "C" not in chunks[0].content
    assert "C" in chunks[1].content and "A" not in chunks[1].content


def test_plan_verify_falls_back_to_one_chunk_without_anchors(tmp_path):
    from pdfoenix.verify.content import plan_verify

    images = [tmp_path / f"pdfpage_{page:03d}.png" for page in (1, 2, 3)]
    chunks = plan_verify(images, "unstructured", 1)
    assert len(chunks) == 1
    assert chunks[0].unit_id == "p1-3"
    assert chunks[0].pages == (1, 2, 3)


def test_plan_verify_lists_sparse_pages_instead_of_a_fake_range(tmp_path):
    from pdfoenix.verify.content import plan_verify

    content = ("<!-- page:28 -->\nA\n<!-- page:35 -->\nB\n"
               "<!-- page:49 -->\nC\n")
    images = [tmp_path / f"pdfpage_{page:03d}.png" for page in (28, 35, 49)]
    chunks = plan_verify(images, content, 5)
    assert len(chunks) == 1
    assert chunks[0].unit_id == "p28+35+49"
    assert chunks[0].pages == (28, 35, 49)
