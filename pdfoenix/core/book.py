"""Book context: path resolution, chapter/batch/section enumeration, natural sort.

A ``Book`` wraps a single book directory and is injected into every core stage.
All paths are derived from the book root, so no book title or layout is ever
hard-coded.  This is also where the "natural" ordering of numbered names
(``ch2`` before ``ch10``, ``sec9-2`` before ``sec9-10``) is defined.
"""
import re
from pathlib import Path

from pdfoenix.core.config import DEFAULT_CONFIG, deep_merge, load_config
from pdfoenix.core.pages import chapter_spans
from pdfoenix.core.state import StateStore


class BookError(ValueError):
    """Raised when the book directory or its source PDF is unusable."""


def natural_key(value):
    """Sort key that orders embedded numbers numerically.

    Text is lower-cased and digits are compared as integers.  Each part is
    tagged (0, int) or (1, str) so that mixing text and numbers in the same
    position never raises ``TypeError``.
    """
    parts = re.split(r"(\d+)", str(value))
    return [(0, int(t)) if t.isdigit() else (1, t.lower()) for t in parts]


class Book:
    """A book directory plus its configuration and state store."""

    def __init__(self, root, config: dict | None = None):
        self.root = Path(root).expanduser().resolve()
        # An injected config is merged over the defaults so tests (and callers)
        # can pass a partial override without losing the rest of the defaults.
        if config is not None:
            self._config = deep_merge(DEFAULT_CONFIG, config)
        else:
            self._config = None

    # ── Configuration and state ─────────────────────────────────────────
    @property
    def config(self) -> dict:
        """Configuration from ``config.toml`` (or defaults), loaded lazily."""
        if self._config is None:
            cfg_path = self.root / "config.toml"
            self._config = load_config(cfg_path if cfg_path.exists() else None)
        return self._config

    @property
    def state(self) -> StateStore:
        """State registry store rooted at ``<book>/state``."""
        return StateStore(self.root)

    # ── Directories ─────────────────────────────────────────────────────
    @property
    def source_dir(self) -> Path:
        return self.root / "source"

    @property
    def manuscript_dir(self) -> Path:
        return self.root / "manuscript"

    @property
    def figures_dir(self) -> Path:
        return self.root / "figures"

    @property
    def working_dir(self) -> Path:
        return self.root / "working"

    @property
    def pages_dir(self) -> Path:
        return self.working_dir / "pages"

    @property
    def batches_dir(self) -> Path:
        return self.working_dir / "batches"

    @property
    def verify_dir(self) -> Path:
        return self.working_dir / "verify"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def build_dir(self) -> Path:
        return self.root / "build"

    # ── Source PDF ──────────────────────────────────────────────────────
    def pdf(self) -> Path:
        """Resolve the source PDF.

        Preference order: ``config.book.pdf`` if set, otherwise the single
        ``source/*.pdf``.  Multiple PDFs without an explicit config entry is an
        error because guessing would be unsafe.
        """
        configured = self.config["book"].get("pdf", "")
        if configured:
            path = self.root / configured
            if not path.exists():
                raise BookError(f"configured PDF does not exist: {path}")
            return path
        found = sorted(self.source_dir.glob("*.pdf"))
        if len(found) == 1:
            return found[0]
        if not found:
            raise BookError(f"no PDF found in {self.source_dir}")
        raise BookError(
            f"multiple PDFs in {self.source_dir}; set [book].pdf in config.toml")

    # ── Enumeration ─────────────────────────────────────────────────────
    def chapters(self) -> list[str]:
        """All chapter directory names found in batches and/or manuscript."""
        names = set()
        for base in (self.batches_dir, self.manuscript_dir):
            if base.exists():
                for entry in base.iterdir():
                    if entry.is_dir() and re.fullmatch(r"ch\d+", entry.name):
                        names.add(entry.name)
        return sorted(names, key=natural_key)

    def batches(self, chapter: str) -> list[Path]:
        """Transcription batch files ``pXXX-YYY.md`` in page order."""
        directory = self.batches_dir / chapter
        if not directory.exists():
            return []
        return sorted(
            directory.glob("p*.md"), key=lambda p: natural_key(p.stem))

    def sections(self, chapter: str) -> list[Path]:
        """Section files ``sec*.md`` in natural order (``00_chapter.md`` excluded)."""
        directory = self.manuscript_dir / chapter
        if not directory.exists():
            return []
        return sorted(
            directory.glob("sec*.md"), key=lambda p: natural_key(p.name))

    def page_image(self, page_no: int) -> Path:
        """Path of the rendered page image for a 1-based PDF page number."""
        return self.pages_dir / f"pdfpage_{page_no:03d}.png"

    def pdf_page_count(self) -> int:
        """Number of pages in the source PDF."""
        import pymupdf as fitz

        with fitz.open(self.pdf()) as doc:
            return doc.page_count

    def chapter_ranges(self) -> dict[str, tuple[int, int]]:
        """Map ``chNN`` ids to inclusive PDF page ranges from the scout state.

        Returns an empty mapping before ``scout`` has run.  The last chapter
        extends to the end of the PDF; when the PDF cannot be opened (read-only
        views on a half-set-up book) the last chapter's start page is used as a
        conservative stand-in.
        """
        scout = self.state.load("scout")
        chapters = scout.get("chapters", [])
        if not chapters:
            return {}
        try:
            last_page = self.pdf_page_count()
        except Exception:
            last_page = max(int(item["pdf_page"]) for item in chapters)
        return chapter_spans(chapters, last_page)
