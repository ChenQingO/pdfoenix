"""Deterministic batch merge: page anchors, section routing, object registration.

Input:  ``working/batches/chNN/pXXX-YYY.md`` transcription batches.
Output: ``manuscript/chNN/{00_chapter,secX-Y,tabX-Y}.md`` plus updates to the
        ``state/`` registries (formulas / figures / tables / progress).

Everything here is deterministic and idempotent; re-running a merge overwrites a
chapter's contributions instead of duplicating them.

Key conventions
---------------
* Page anchors ``<!-- page:N -->`` are the single source of the "page" concept.
  They are stripped from the final Markdown; the formula <-> page mapping is
  persisted in ``state/formulas.json`` instead.
* A formula is a standalone display block (``$$ ... $$``, numbered or not).  Its
  LaTeX stays inline in the prose (so the prose remains readable and reviewable);
  the registry stores only metadata.
* A table is an inline Markdown table with a caption.  It is extracted to
  ``tabX-Y.md`` and replaced by a ``<!-- TABLE X.Y -->`` placeholder that the
  build step inlines.  This keeps a single source of truth and a clean section.
* A figure placeholder ``<!-- FIGURE X.Y. ... -->`` becomes
  ``![...](figures/figX-Y.png)``; the structured description is kept in state so
  a future redraw does not need to re-read the source page.
"""
import re
from dataclasses import dataclass, field

from pdfoenix.core.book import Book
from pdfoenix.core.config import risk_weights
from pdfoenix.core.risk import risk_score


class MergeError(ValueError):
    """Raised when the batch structure cannot be merged."""


# A page anchor occupies its own line.
PAGE_RE = re.compile(r"^\s*<!--\s*page\s*:\s*(\d+)\s*-->\s*$")
# The chapter title line (a h1 starting with "Chapter").
H1_RE = re.compile(r"^#\s+Chapter\b.*$")
# A section heading; group 1 is its first token, e.g. "3.2" or "Problems".
H2_RE = re.compile(r"(?m)^##\s+(\S+)")
# A printed equation number written as "\qquad (N)".
NUMBER_RE = re.compile(r"\\qquad\s*\((\d+)\)")
# A single-line footnote definition.
FOOTNOTE_DEF_RE = re.compile(r"^\[\^([A-Za-z0-9_-]+)]:(.*)$")
# A figure placeholder, e.g. "<!-- FIGURE 8.1. Caption: ... Content: ... -->".
FIGURE_RE = re.compile(
    r"^\s*<!--\s*FIGURE\s+([0-9]+(?:\.[0-9]+)+)\.?\s*(.*?)-->\s*$", re.DOTALL)
# A table caption line, e.g. "**Table 3.2**. ...".
CAPTION_RE = re.compile(r"^\s*\*\*Table\s+([0-9A-Za-z.\-]+)\*\*")
# A three-part number such as "8.6.4" (subsection) that must not become its own file.
SUBSECTION_KEY_RE = re.compile(r"^(\d+\.\d+)\.\d+$")
# An existing image reference, e.g. "![Figure 3.1.](figures/fig3-1.png)".
IMAGE_RE = re.compile(r"^!\[([^]]*)]\((figures/[^)]+)\)\s*$")
FIGURE_ID_RE = re.compile(r"figures/([^/]+)\.png$")


@dataclass
class MergeOverrides:
    """Per-chapter corrections applied during merge (all optional)."""

    title: str | None = None
    seams: list = field(default_factory=list)          # [(regex, replacement), ...]
    fixes: list = field(default_factory=list)          # [(old, new), ...]
    figs: list = field(default_factory=list)           # [(regex, replacement), ...]
    footnote_rename: dict = field(default_factory=dict)  # batch-local id -> chapter id
    strip_extra: list = field(default_factory=list)    # extra comment-stripping regexes


@dataclass
class MergeSummary:
    """Human-readable result of merging one chapter."""

    chapter: str
    sections: list
    formulas: int = 0
    figures: int = 0
    tables: int = 0
    footnotes: int = 0
    warnings: list = field(default_factory=list)


# ── Text primitives ─────────────────────────────────────────────────────
def strip_comments(text: str, extra=()) -> str:
    """Remove HTML comments but keep the four meaningful kinds.

    ``uncertain`` (fidelity notes), ``page`` (anchors), ``FIGURE`` and ``TABLE``
    (placeholders) must survive.  Processing-marker lines are dropped entirely.
    """
    text = re.sub(
        r"<!--(?!\s*(?:uncertain|page|FIGURE|TABLE)\b).*?-->",
        "", text, flags=re.DOTALL)
    for pattern in extra:
        text = re.sub(pattern, "", text, flags=re.DOTALL)
    kept = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("<!-- SEAM", "<!-- NEXT", "<!-- END OF BATCH",
                                "<!-- batch")):
            continue
        kept.append(line)
    return "\n".join(kept)


def sub_re(pattern, replacement, text, flags=0) -> str:
    """``re.sub`` with safe replacement semantics.

    ``\\1``..``\\9`` expand to capture groups, while every other backslash is
    kept literally.  This is important because replacements are full of LaTeX
    (``\\boldsymbol`` etc.), which ``re.sub`` would otherwise treat as escapes.
    """
    def expand(match):
        out = replacement
        for index in range(9, 0, -1):
            token = f"\\{index}"
            if token in out:
                group = match.group(index) if index <= (match.re.groups or 0) else None
                out = out.replace(token, group if group is not None else token)
        return out

    return re.sub(pattern, expand, text, flags=flags)


def extract_footnote_defs(text: str):
    """Split single-line footnote definitions out of the text.

    Returns ``(remaining_text, {id: definition_line})``.  Definitions are later
    appended to the section file that references them.
    """
    definitions, kept = {}, []
    for line in text.splitlines():
        match = FOOTNOTE_DEF_RE.match(line)
        if match:
            definitions[match.group(1)] = line
        else:
            kept.append(line)
    return "\n".join(kept), definitions


def normalize(text: str) -> str:
    """Apply notation normalization and whitespace cleanup."""
    text = text.replace(r"\mathcal{", r"\mathscr{")
    text = re.sub(r"[ \t]+$", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def chapter_number(chapter: str) -> str:
    """Extract the numeric chapter number from a directory name ("ch03" -> "3")."""
    match = re.search(r"(\d+)", chapter)
    if not match:
        raise MergeError(f"cannot parse a chapter number from {chapter!r}")
    return str(int(match.group(1)))


def section_filename(chapter: str, key: str) -> str:
    """Map a section heading token to its output filename.

    Examples: ``"3.2" -> sec3-2.md``, ``"Problems" -> sec3-problems.md``,
    and a subsection key ``"3.2.1"`` is routed to its parent ``sec3-2.md``.
    """
    number = chapter_number(chapter)
    subsection = SUBSECTION_KEY_RE.match(key)
    if subsection:
        key = subsection.group(1)   # attach a subsection to its parent section
    if key == "Problems":
        return f"sec{number}-problems.md"
    if re.fullmatch(r"\d+\.\d+", key):
        return f"sec{key.replace('.', '-')}.md"
    slug = re.sub(r"[^A-Za-z0-9]+", "-", key).strip("-").lower() or "other"
    return f"sec{number}-{slug}.md"


def _first_key(text: str) -> str:
    """Return the first section-heading token in a block (raises if absent)."""
    match = H2_RE.search(text)
    if not match:
        raise MergeError("section block has no '## ' heading")
    return match.group(1)


# ── Object registration ─────────────────────────────────────────────────
def _register_formula(registry, eq_id, number, latex, page, section, weights):
    """Store formula metadata and maintain the formula <-> page index."""
    registry["formulas"][eq_id] = {
        "number": number,
        "latex": latex,
        "risk": risk_score(latex, weights),
        "section": section,
    }
    if page is not None:
        page_key = f"working/pages/pdfpage_{page:03d}.png"
        registry["formula_to_page"][eq_id] = page_key
        registry["page_to_formulas"].setdefault(page_key, []).append(eq_id)


def _figure_alt(label: str, rest: str) -> str:
    """Build the visible caption for a figure from a placeholder's payload."""
    rest = rest.strip()
    if "Caption:" in rest:
        caption = rest.split("Caption:", 1)[1].split("Content:", 1)[0].strip()
    else:
        caption = rest.split("Content:", 1)[0].strip()
    return f"Figure {label}. {caption}".strip() if caption else f"Figure {label}."


def _figure_id(label: str) -> str:
    """Figure id derived from its label ("8.1" -> "fig8-1")."""
    return "fig" + label.replace(".", "-")


def _detect_table(lines, i):
    """Detect a table block anchored by a caption line at index ``i``.

    Returns ``(caption_index, end_index, label, caption_line, rows)`` or ``None``.
    """
    match = CAPTION_RE.match(lines[i])
    if not match:
        return None
    label = match.group(1).rstrip(".")
    cursor = i + 1
    while cursor < len(lines) and lines[cursor].strip() == "":
        cursor += 1
    if cursor >= len(lines) or not lines[cursor].strip().startswith("|"):
        return None
    end = cursor
    while end < len(lines) and lines[end].strip().startswith("|"):
        end += 1
    if end - cursor < 2:          # a valid Markdown table has header + separator
        return None
    return i, end, label, lines[i], lines[cursor:end]


def _table_filename(table_id: str) -> str:
    """Markdown filename for a table id ("3.2" -> "tab3-2.md")."""
    return f"tab{table_id.replace('.', '-')}.md"


def _write_table(book: Book, chapter: str, filename: str, caption, rows) -> None:
    """Write an extracted table (caption + Markdown rows) to the manuscript dir."""
    directory = book.manuscript_dir / chapter
    directory.mkdir(parents=True, exist_ok=True)
    parts = []
    if caption:
        parts.append(caption.strip())
    parts.append("\n".join(row.rstrip() for row in rows))
    (directory / filename).write_text(
        "\n\n".join(parts).strip() + "\n", encoding="utf-8")


def _register_figure(figure_reg, figure_id, image, caption, description, page):
    """Store one figure registry entry."""
    figure_reg["figures"][figure_id] = {
        "file": image,
        "caption": caption,
        "description": description,
        "page": page,
    }


def _emit_table(book, chapter, table_reg, table_id, caption, rows, page) -> str:
    """Write and register a table, returning the placeholder for the section."""
    filename = _table_filename(table_id)
    _write_table(book, chapter, filename, caption, rows)
    table_reg["tables"][table_id] = {
        "caption": caption.strip() if caption else None,
        "md_file": f"manuscript/{chapter}/{filename}",
        "page": page,
    }
    return f"<!-- TABLE {table_id} -->"


# ── Section processing (page tracking + object extraction) ──────────────
def _process_section(text, chapter, chapter_num, key, book, weights,
                     formula_reg, figure_reg, table_reg, seams=()):
    """Clean one section, register its objects, and return its final Markdown.

    Page anchors are tracked per line so each object can record its page before
    the anchors are stripped.  Seam repairs are applied afterward, because a
    page anchor sitting between two halves of a sentence would block ``\\s+``
    matching.
    """
    lines = text.splitlines()

    # page_for_line[i] is the page in effect for line i (None for anchor lines).
    page_for_line, current_page = [], None
    for line in lines:
        match = PAGE_RE.match(line)
        if match:
            current_page = int(match.group(1))
            page_for_line.append(None)
        else:
            page_for_line.append(current_page)

    output, eq_seq, tab_seq = [], 0, 0
    i = 0
    while i < len(lines):
        line = lines[i]
        if PAGE_RE.match(line):
            i += 1
            continue
        page = page_for_line[i]
        stripped = line.strip()

        # Display equation block: "$$" on its own line, until the next "$$".
        if stripped == "$$":
            end = i + 1
            while end < len(lines) and lines[end].strip() != "$$":
                end += 1
            if end < len(lines):
                latex = "\n".join(lines[i + 1:end])
                eq_seq += 1
                number_match = NUMBER_RE.search(latex)
                eq_id = f"eq{chapter_num}-{key.replace('.', '-')}-{eq_seq}"
                if eq_id in formula_reg["formulas"]:      # idempotent overwrite
                    formula_reg["formulas"].pop(eq_id, None)
                _register_formula(
                    formula_reg, eq_id,
                    f"({number_match.group(1)})" if number_match else None,
                    latex, page, key, weights)
                output.extend(lines[i:end + 1])
                i = end + 1
                continue

        # Table with a caption.
        table = _detect_table(lines, i)
        if table:
            _, end, label, caption, rows = table
            tab_seq += 1
            table_id = label or f"{chapter_num}-{tab_seq}"
            output.append(
                _emit_table(book, chapter, table_reg, table_id, caption, rows, page))
            i = end
            continue

        # Table without a caption: assign a sequential id.
        if stripped.startswith("|"):
            end = i
            while end < len(lines) and lines[end].strip().startswith("|"):
                end += 1
            if end - i >= 2:
                tab_seq += 1
                table_id = f"{chapter_num}-{tab_seq}"
                output.append(
                    _emit_table(book, chapter, table_reg, table_id, None,
                                lines[i:end], page))
                i = end
                continue

        # Figure placeholder -> image reference + registry entry.
        figure_match = FIGURE_RE.match(line)
        if figure_match:
            label = figure_match.group(1)
            rest = figure_match.group(2).strip()
            figure_id = _figure_id(label)
            image = f"figures/{figure_id}.png"
            alt = _figure_alt(label, rest)
            output.append(f"![{alt}]({image})")
            _register_figure(figure_reg, figure_id, image, alt, rest, page)
            i += 1
            continue

        # An existing image reference (from an override or earlier content).
        image_match = IMAGE_RE.match(line)
        if image_match:
            alt, image = image_match.group(1), image_match.group(2)
            id_match = FIGURE_ID_RE.search(image)
            figure_id = id_match.group(1) if id_match else image
            _register_figure(figure_reg, figure_id, image, alt, "", page)

        output.append(line)
        i += 1

    joined = "\n".join(output)
    for pattern, replacement in seams:
        joined = sub_re(pattern, replacement, joined)
    return normalize(joined)


# ── Merge entry points ──────────────────────────────────────────────────
def _is_chapter_table_id(table_id: str, chapter_num: str) -> bool:
    """Whether a table id belongs to the given chapter (e.g. "3.2" / "3-1")."""
    return re.fullmatch(rf"{chapter_num}(?:[.\-][0-9A-Za-z\-]*)?", table_id) is not None


def _read_chapter_stream(book: Book, chapter: str) -> str:
    """Concatenate a chapter's batches, adding a fallback page anchor if needed.

    New transcriptions already contain ``<!-- page:N -->`` anchors.  Legacy
    batches may not, so we prepend an anchor at the batch's first page; per-section
    page tracking then has a sensible default.
    """
    batches = book.batches(chapter)
    if not batches:
        raise MergeError(f"{chapter} has no batch files")
    parts = []
    for batch_path in batches:
        text = batch_path.read_text(encoding="utf-8")
        match = re.match(r"p(\d+)-", batch_path.stem)
        if match and not PAGE_RE.search(text):
            text = f"<!-- page:{int(match.group(1))} -->\n\n" + text
        parts.append(text)
    return "\n\n".join(parts)


def _drop_chapter_entries(registry, chapter_num):
    """Remove a chapter's old formula entries (idempotency helper)."""
    prefix = f"eq{chapter_num}-"
    for eq_id in [key for key in registry["formulas"] if key.startswith(prefix)]:
        page_key = registry["formula_to_page"].pop(eq_id, None)
        registry["formulas"].pop(eq_id, None)
        if page_key and page_key in registry["page_to_formulas"]:
            registry["page_to_formulas"][page_key] = [
                item for item in registry["page_to_formulas"][page_key]
                if item != eq_id]
            if not registry["page_to_formulas"][page_key]:
                registry["page_to_formulas"].pop(page_key)


def merge_chapter(book: Book, chapter: str, overrides: MergeOverrides | None = None,
                  telemetry=None) -> MergeSummary:
    """Merge one chapter and return a summary of what was produced."""
    overrides = overrides or MergeOverrides()
    chapter_num = chapter_number(chapter)
    weights = risk_weights(book.config)

    stream = _read_chapter_stream(book, chapter)
    stream = strip_comments(stream, overrides.strip_extra)
    for old, new in overrides.footnote_rename.items():
        stream = stream.replace(old, new)
    stream, footnote_defs = extract_footnote_defs(stream)
    for old, new in overrides.fixes:
        stream = stream.replace(old, new)
    for pattern, replacement in overrides.figs:
        stream = sub_re(pattern, replacement, stream, flags=re.DOTALL)

    # Pull the chapter title out of the stream (first "# Chapter" line).
    title = overrides.title
    kept = []
    for line in stream.splitlines():
        if title is None and H1_RE.match(line.strip()):
            title = line.strip()
            continue
        kept.append(line)
    stream = "\n".join(kept)

    # Split into sections by line index, injecting the page in effect at each
    # section's start.  Page anchors often sit at the end of the previous
    # section's block (right before the next heading), so carrying the page
    # forward is required or the next section's first formulas lose their page.
    lines = stream.splitlines()
    heading_index = [i for i, line in enumerate(lines) if re.match(r"^##\s+\S+", line)]
    if not heading_index:
        raise MergeError(f"{chapter} has no '## ' section headings")
    page_of_line, current_page = [], None
    for line in lines:
        match = PAGE_RE.match(line)
        if match:
            current_page = int(match.group(1))
            page_of_line.append(None)
        else:
            page_of_line.append(current_page)

    def with_page(segment_lines, start_page):
        prefix = [f"<!-- page:{start_page} -->"] if start_page is not None else []
        return "\n".join(prefix + segment_lines)

    chunks = []
    for index, start in enumerate(heading_index):
        end = heading_index[index + 1] if index + 1 < len(heading_index) else len(lines)
        segment = lines[start:end]
        page = page_of_line[start]
        if index == 0:
            leading = lines[:start]
            if any(line.strip() for line in leading):
                segment = leading + segment
                page = page_of_line[0]
        chunks.append(with_page(segment, page))

    # Route chunks to section files.  Subsection keys map to their parent file,
    # so multiple chunks may accumulate into the same output file.
    routed: dict[str, list[str]] = {}
    for chunk in chunks:
        key = _first_key(chunk)
        filename = section_filename(chapter, key)
        routed.setdefault(filename, []).append(chunk)

    formula_reg = book.state.load("formulas")
    figure_reg = book.state.load("figures")
    table_reg = book.state.load("tables")

    # Idempotency: drop this chapter's previous contributions before re-adding.
    _drop_chapter_entries(formula_reg, chapter_num)
    for figure_id in [key for key in figure_reg["figures"]
                      if key.startswith(f"fig{chapter_num}-")]:
        figure_reg["figures"].pop(figure_id, None)
    for table_id in [key for key in table_reg["tables"]
                     if _is_chapter_table_id(key, chapter_num)]:
        table_reg["tables"].pop(table_id, None)

    section_texts: dict[str, str] = {}
    warnings = []
    for filename, chunk_list in routed.items():
        text = "\n\n".join(chunk_list)
        key = _first_key(text)
        section_texts[filename] = _process_section(
            text, chapter, chapter_num, key, book, weights,
            formula_reg, figure_reg, table_reg, overrides.seams)

    # Append each footnote definition to the section that references it.
    for footnote_id, definition in footnote_defs.items():
        target = next(
            (name for name, body in section_texts.items() if footnote_id in body),
            None)
        if target is None:
            warnings.append(f"footnote {footnote_id} is never referenced")
            continue
        section_texts[target] = (
            section_texts[target].rstrip() + "\n\n" + definition + "\n")

    # Write the manuscript files.
    output_dir = book.manuscript_dir / chapter
    output_dir.mkdir(parents=True, exist_ok=True)
    if title:
        (output_dir / "00_chapter.md").write_text(title + "\n", encoding="utf-8")
    else:
        warnings.append(
            "no chapter title found (no '# Chapter' line and no overrides.title)")
    for filename, text in section_texts.items():
        (output_dir / filename).write_text(text, encoding="utf-8")

    book.state.save("formulas", formula_reg)
    book.state.save("figures", figure_reg)
    book.state.save("tables", table_reg)
    book.state.update("progress", lambda data: data["chapters"].__setitem__(chapter, {
        "merged": True,
        "sections": sorted(section_texts),
        "formulas": len(formula_reg["formulas"]),
        "figures": len(figure_reg["figures"]),
        "tables": len(table_reg["tables"]),
    }))

    summary = MergeSummary(
        chapter=chapter,
        sections=sorted(section_texts),
        formulas=sum(1 for key in formula_reg["formulas"]
                     if key.startswith(f"eq{chapter_num}-")),
        figures=sum(1 for key in figure_reg["figures"]
                    if key.startswith(f"fig{chapter_num}-")),
        tables=sum(1 for key in table_reg["tables"]
                   if _is_chapter_table_id(key, chapter_num)),
        footnotes=len(footnote_defs),
        warnings=warnings,
    )
    if telemetry is not None:
        telemetry.emit_event(
            "merge", chapter=chapter, sections=len(summary.sections),
            formulas=summary.formulas, figures=summary.figures,
            tables=summary.tables)
    return summary


def merge_book(book: Book, overrides=None, telemetry=None) -> dict[str, MergeSummary]:
    """Merge every chapter.  ``overrides`` may be one object or a per-chapter map."""
    summaries = {}
    for chapter in book.chapters():
        chapter_overrides = overrides
        if isinstance(overrides, dict):
            chapter_overrides = overrides.get(chapter)
        summaries[chapter] = merge_chapter(
            book, chapter, chapter_overrides, telemetry)
    return summaries
