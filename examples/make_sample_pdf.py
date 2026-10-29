"""Generate a tiny synthetic PDF for install/transcription smoke tests.

The result is intentionally plain and copyright-free: two pages, a text layer,
a chapter heading, a section heading, and a display equation. It is enough to
exercise ``init``, ``scout``, page rendering, and the deterministic structure
path without downloading a real book.

Usage:
    python examples/make_sample_pdf.py /tmp/pdfoenix-sample.pdf
"""
import sys
from pathlib import Path

import pymupdf


def build(path: Path) -> None:
    doc = pymupdf.open()
    first = doc.new_page()
    first.insert_text((72, 96), "A TINY SAMPLE BOOK", fontsize=22)
    first.insert_text((72, 132), "pdfoenix contributors", fontsize=12)
    first.insert_text((72, 180), "This book is synthetic and free to share.",
                      fontsize=11)

    second = doc.new_page()
    second.insert_text((72, 96), "CHAPTER 1", fontsize=16)
    second.insert_text((72, 124), "A Tiny Sample", fontsize=16)
    second.insert_text((72, 168), "1.1 A Formula", fontsize=13)
    second.insert_text((72, 198), "This is a short paragraph with a text layer.",
                       fontsize=11)
    second.insert_text((150, 240), "x^2 + y^2 = z^2", fontsize=14)
    second.insert_text((222, 268), "(1)", fontsize=11)

    doc.set_metadata({"title": "A Tiny Sample Book",
                      "author": "pdfoenix contributors"})
    doc.save(path)
    doc.close()


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 2
    path = Path(sys.argv[1]).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    build(path)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
