"""EPUB post-processing tests (TDD)."""
from pdfoenix.core.postprocess import (
    renumber_footnotes,
    strip_nav_from_spine,
)


def test_renumber_and_backlink():
    html = (
        '<p>a<a href="#fn2" role="doc-noteref">2</a>'
        'b<a href="#fn5" role="doc-noteref">5</a></p>'
        '<section id="footnotes"><hr />'
        '<aside id="fn2"><p>second</p></aside>'
        '<aside id="fn5"><p>fifth</p></aside></section>')
    out = renumber_footnotes(html)
    assert 'href="#fn1"' in out and 'href="#fn2"' in out
    assert 'class="footnote-end" id="fn1"' in out
    assert 'class="footnote-back" href="#fnref1"' in out
    assert 'class="notes-heading">Notes' in out
    assert "<hr />" not in out


def test_renumber_single():
    html = '<a href="#fn0">0</a><aside id="fn0"><p>note</p></aside>'
    out = renumber_footnotes(html)
    assert 'id="fnref1"' in out and 'id="fn1"' in out


def test_strip_nav_from_spine():
    opf = ('<spine><itemref idref="nav" />'
           '<itemref idref="ch001"/></spine>')
    out = strip_nav_from_spine(opf)
    assert "idref=\"nav\"" not in out
    assert 'idref="ch001"' in out
