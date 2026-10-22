"""EPUB inspection tests (TDD)."""
import zipfile

from pdfoenix.inspect import inspect_epub


def make_epub(path, *, math=True, nav_in_spine=False, stored_mimetype=True):
    opf = """<?xml version="1.0"?>
<package version="3.0">
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="ch1" href="EPUB/text/ch1.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    {nav}
  </spine>
</package>""".format(nav='<itemref idref="nav"/>' if nav_in_spine else "")
    chapter = """<html><body>
<p><a href="#fn1">1</a></p>
<math><mi>x</mi></math>
<aside class="footnote-end" id="fn1">note
<a class="footnote-back" href="#fnref1">↩</a></aside>
</body></html>"""
    if not math:
        chapter = chapter.replace("<math><mi>x</mi></math>", "")
    with zipfile.ZipFile(path, "w") as archive:
        compression = (zipfile.ZIP_STORED if stored_mimetype
                       else zipfile.ZIP_DEFLATED)
        archive.writestr("mimetype", "application/epub+zip",
                         compress_type=compression)
        archive.writestr("META-INF/container.xml",
                         '<container><rootfile full-path="content.opf"/></container>')
        archive.writestr("content.opf", opf)
        archive.writestr("nav.xhtml", "<html><body><nav/></body></html>")
        archive.writestr("EPUB/text/ch1.xhtml", chapter)


def test_valid_epub_passes(tmp_path):
    path = tmp_path / "book.epub"
    make_epub(path)
    report = inspect_epub(path)
    assert report.ok
    assert report.counts["xhtml"] == 2
    assert report.counts["math"] == 1
    assert report.counts["notes"] == 1
    assert report.counts["spine"] == 1


def test_nav_in_spine_is_an_issue(tmp_path):
    path = tmp_path / "book.epub"
    make_epub(path, nav_in_spine=True)
    report = inspect_epub(path)
    assert not report.ok
    assert any("nav document is still in the spine" in issue for issue in report.issues)


def test_missing_math_is_an_issue(tmp_path):
    path = tmp_path / "book.epub"
    make_epub(path, math=False)
    report = inspect_epub(path)
    assert any("no MathML" in issue for issue in report.issues)


def test_compressed_mimetype_is_an_issue(tmp_path):
    path = tmp_path / "book.epub"
    make_epub(path, stored_mimetype=False)
    report = inspect_epub(path)
    assert any("stored uncompressed" in issue for issue in report.issues)
