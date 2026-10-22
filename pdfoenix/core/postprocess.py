"""EPUB post-processing: move footnotes to chapter-end notes, drop nav from the spine.

The two pure functions are kept separate so they can be unit-tested without
building a whole book; ``postprocess_epub`` applies them in place to a ``.epub``
zip file.
"""
import re
import zipfile
from pathlib import Path

REF_RE = re.compile(r'<a\b[^>]*href="#fn(\d+)"[^>]*>[^<]*</a>')
ASIDE_RE = re.compile(r'<aside\b[^>]*id="fn(\d+)"[^>]*>(.*?)</aside>', re.DOTALL)
HR_RE = re.compile(r'(<section id="footnotes"[^>]*>)\s*<hr\s*/>')


def renumber_footnotes(html: str) -> str:
    """Turn pandoc footnotes into chapter-end notes.

    * renumber per chapter (restart at 1 in reading order),
    * strip popup semantics so Apple Books shows a plain anchor jump,
    * add a leading note number and a "↩" back-link,
    * replace the bare ``<hr/>`` with a visible "Notes" heading.
    """
    order = []
    for reference in REF_RE.finditer(html):
        if reference.group(1) not in order:
            order.append(reference.group(1))
    mapping = {old: str(index + 1) for index, old in enumerate(order)}

    def replace_ref(ref_match):
        new = mapping[ref_match.group(1)]
        return f'<a href="#fn{new}" id="fnref{new}" class="footnote-ref">{new}</a>'

    html = REF_RE.sub(replace_ref, html)

    def replace_aside(aside_match):
        old = aside_match.group(1)
        body = aside_match.group(2)
        new = mapping.get(old, old)
        body = body.strip()
        body = f'<span class="note-num">{new}</span> ' + body
        body = re.sub(
            r'</p>\s*$',
            f' <a class="footnote-back" href="#fnref{new}">&#8617;</a></p>',
            body)
        return f'<aside class="footnote-end" id="fn{new}">\n{body}\n</aside>'

    html = ASIDE_RE.sub(replace_aside, html)
    return HR_RE.sub(r'\1\n<h3 class="notes-heading">Notes</h3>', html)


def strip_nav_from_spine(opf: str) -> str:
    """Remove ``nav.xhtml`` from the reading order.

    The manifest entry with ``properties="nav"`` stays (required by EPUB 3 and
    used by Apple Books for the sidebar).
    """
    return re.sub(r'\s*<itemref idref="nav"\s*/>', "", opf)


def _transform(name: str, content: bytes) -> bytes:
    """Apply the right post-processing to a single EPUB entry."""
    if re.match(r"EPUB/text/ch\d+\.xhtml", name):
        return renumber_footnotes(content.decode("utf-8")).encode("utf-8")
    if name.endswith("content.opf"):
        return strip_nav_from_spine(content.decode("utf-8")).encode("utf-8")
    return content


def postprocess_epub(path) -> None:
    """Apply footnote renumbering and spine cleanup to an ``.epub`` in place."""
    path = Path(path)
    with zipfile.ZipFile(path) as zin:
        items = zin.infolist()
        data = {item.filename: zin.read(item.filename) for item in items}

    processed = {name: _transform(name, content) for name, content in data.items()}

    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in items:
            zout.writestr(item, processed[item.filename])
    tmp.replace(path)
