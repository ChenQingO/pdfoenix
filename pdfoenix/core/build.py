"""Build: assemble Markdown, run pandoc, then post-process the EPUB.

The assembly step is pure and testable; ``build_book`` additionally shells out to
pandoc and then applies ``postprocess_epub``.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pdfoenix.core.book import Book
from pdfoenix.core.merge import table_filename
from pdfoenix.core.postprocess import postprocess_epub

TABLE_PH_RE = re.compile(r"<!--\s*TABLE\s+([0-9A-Za-z.\-]+)\s*-->")


class BuildError(RuntimeError):
    """Raised when pandoc is missing or fails."""


def _pandoc() -> str:
    """Locate the pandoc executable (PATH first, then next to this interpreter)."""
    found = shutil.which("pandoc")
    if found:
        return found
    candidate = Path(sys.executable).parent / "pandoc"
    if candidate.exists():
        return str(candidate)
    raise BuildError("pandoc not found (neither on PATH nor in env/bin)")


def inline_tables(text: str, chapter_dir: Path) -> str:
    """Replace ``<!-- TABLE X.Y -->`` placeholders with ``tabX-Y.md`` content."""
    def replace(match):
        table_id = match.group(1)
        table_file = chapter_dir / table_filename(table_id)
        if table_file.exists():
            return table_file.read_text(encoding="utf-8").strip()
        return match.group(0)      # leave the placeholder if the file is missing

    return TABLE_PH_RE.sub(replace, text)


def _heading_title_and_anchor(path: Path, marker: str) -> tuple[str, str] | None:
    """Return ``(title, anchor)`` from a heading's first line, or ``None``.

    ``marker`` is ``"#"`` for a chapter and ``"##"`` for a section.
    """
    first_line = path.read_text(encoding="utf-8").strip().splitlines()[0]
    match = re.match(rf"{marker}\s+(.+?)\s*\{{#([^}}]+)}}", first_line)
    if not match:
        return None
    return match.group(1), match.group(2)


def contents_md(book: Book) -> str:
    """Generate the in-book Contents page (also serves as a TOC entry)."""
    lines = ["# Contents {#contents}", "", "::: {.toc-page}", ""]
    for chapter in book.chapters():
        chapter_file = book.manuscript_dir / chapter / "00_chapter.md"
        if not chapter_file.exists():
            continue
        chapter_heading = _heading_title_and_anchor(chapter_file, "#")
        if chapter_heading is None:
            continue
        lines.append(f"- [{chapter_heading[0]}](#{chapter_heading[1]})")
        for section in book.sections(chapter):
            section_heading = _heading_title_and_anchor(section, "##")
            if section_heading:
                lines.append(
                    f"    - [{section_heading[0]}](#{section_heading[1]})")
    if (book.manuscript_dir / "references" / "references.md").exists():
        lines.append("- [References](#references)")
    lines += ["", ":::", ""]
    return "\n".join(lines)


def assemble_markdown(book: Book) -> str:
    """Assemble the single Markdown document handed to pandoc."""
    config = book.config["book"]
    title = config.get("title") or "Untitled"
    author = config.get("author") or ""
    metadata = [
        "---",
        f'title: "{title}"',
        f'author: "{author}"',
        "lang: en",
        "---",
        "",
        "",
    ]
    parts = ["\n".join(metadata), contents_md(book) + "\n"]

    for chapter in book.chapters():
        chapter_dir = book.manuscript_dir / chapter
        chapter_file = chapter_dir / "00_chapter.md"
        if chapter_file.exists():
            parts.append(chapter_file.read_text(encoding="utf-8").strip() + "\n\n")
        for section in book.sections(chapter):
            text = section.read_text(encoding="utf-8")
            parts.append(inline_tables(text, chapter_dir).strip() + "\n\n")

    references = book.manuscript_dir / "references" / "references.md"
    if references.exists():
        parts.append(references.read_text(encoding="utf-8").strip() + "\n\n")
    return "".join(parts)


def build_book(book: Book, out_name: str = "book.epub") -> Path:
    """Assemble, run pandoc, post-process, and return the EPUB path."""
    out_dir = book.build_dir.resolve()
    relative_out = Path(out_name)
    if relative_out.is_absolute():
        raise BuildError("--out must be a relative path inside build/")
    out = (out_dir / relative_out).resolve()
    if out == out_dir:
        raise BuildError("--out must name a file inside build/")
    try:
        out.relative_to(out_dir)
    except ValueError as exc:
        raise BuildError("--out must stay inside build/") from exc

    book.build_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = book.build_dir / "book.md"
    markdown_path.write_text(assemble_markdown(book), encoding="utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)

    command = [
        _pandoc(), str(markdown_path), "-o", str(out),
        "--toc", "--toc-depth=2",
        "--math-method=mathml",
        "--split-level=1",
        "-f", "markdown+footnotes",
        f"--resource-path={book.root}:{book.build_dir}",
    ]
    cover = next(iter(sorted(book.source_dir.glob("cover.*"))), None)
    if cover:
        command.append(f"--epub-cover-image={cover}")
    css = book.root / "styles" / "epub.css"
    if css.exists():
        command.append(f"--css={css}")

    # We check the return code ourselves to raise a rich BuildError with stderr.
    result = subprocess.run(
        command, cwd=book.root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise BuildError(f"pandoc failed:\n{result.stderr}")
    if result.stderr.strip():
        print(result.stderr.strip())      # pandoc warnings go to stderr
    postprocess_epub(out)
    return out
