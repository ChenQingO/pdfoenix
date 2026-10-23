"""Create a new book directory (``pdfoenix init``).

The scaffold is deliberately boring: directories, a full ``config.toml`` derived
from the built-in defaults, a copy of every preset, and empty state registries.
It is idempotent: rerunning fills missing files and never clobbers an existing
configuration unless ``force`` is set.
"""
import copy
import shutil
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.config import DEFAULT_CONFIG, load_config, write_config
from pdfoenix.core.state import StateStore

_PRESET_FILES = (
    "opencode.jsonc",
    "providers.toml",
    "transcribe.md",
    "verify.md",
    "pricing.toml",
    "config.schema.toml",
    "epub.css",
)
_DIRECTORIES = (
    "source",
    "manuscript",
    "figures",
    "working/pages",
    "working/verify",
    "working/zoom",
    "working/batches",
    "state",
    "logs",
    "build",
    "styles",
    "presets",
)


class ScaffoldError(ValueError):
    """Raised when the source PDF or target directory is unusable."""


def scaffold_book(
    root,
    *,
    pdf=None,
    title: str | None = None,
    author: str | None = None,
    page_offset: int = 0,
    force: bool = False,
) -> Book:
    """Create or update one book directory and return its :class:`Book`."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for relative in _DIRECTORIES:
        (root / relative).mkdir(parents=True, exist_ok=True)

    presets_src = Path(__file__).resolve().parent / "presets"
    presets_dst = root / "presets"
    for name in _PRESET_FILES:
        source = presets_src / name
        target = presets_dst / name
        if source.exists() and (force or not target.exists()):
            shutil.copy2(source, target)
    css_source = presets_src / "epub.css"
    css_target = root / "styles" / "epub.css"
    if css_source.exists() and (force or not css_target.exists()):
        shutil.copy2(css_source, css_target)

    source_pdf = _copy_pdf(root, pdf, force=force)
    config_path = root / "config.toml"
    if config_path.exists() and not force:
        config = load_config(config_path)
    else:
        config = copy.deepcopy(DEFAULT_CONFIG)
    if source_pdf is not None:
        config["book"]["pdf"] = source_pdf.relative_to(root).as_posix()
        config["book"]["title"] = (title or config["book"].get("title")
                                   or source_pdf.stem)
    elif title:
        config["book"]["title"] = title
    if author:
        config["book"]["author"] = author
    if page_offset:
        config["book"]["page_offset"] = page_offset
    if not config["book"].get("title"):
        config["book"]["title"] = title or root.name.replace("_", " ").title()
    write_config(config_path, config)

    state = StateStore(root)
    for name in ("scout", "formulas", "figures", "tables", "corrections", "progress"):
        if force or not state.path(name).exists():
            state.save(name, state.load(name))

    dangling = root / "known_dangling.txt"
    if force or not dangling.exists():
        dangling.write_text(
            "# Anchor ids that are allowed to dangle.\n", encoding="utf-8")
    return Book(root)


def _copy_pdf(root: Path, pdf, *, force: bool) -> Path | None:
    if pdf is None:
        found = sorted((root / "source").glob("*.pdf"))
        return found[0] if found else None
    source = Path(pdf).expanduser()
    if not source.exists():
        raise ScaffoldError(f"source PDF does not exist: {source}")
    target = root / "source" / source.name
    if source.resolve() != target.resolve() and (force or not target.exists()):
        shutil.copy2(source, target)
    return target
