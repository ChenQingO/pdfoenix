"""Validation tests (TDD)."""
from pdfoenix.core.book import Book
from pdfoenix.core.check import (
    check_book,
    count_display_equations,
    section_key_from_filename,
)

CLEAN_FILES = {
    "00_chapter.md": "# Chapter 3. T {#ch-3}\n",
    "sec3-1.md": (
        "## 3.1 Intro {#sec-3-1}\n\n"
        "$$\nx = 1 \\qquad (1)\n$$\n\n"
        "See [Section 3.1](#sec-3-1).\n"),
}


def make(tmp_path, files, formulas=None, dangling=None):
    d = tmp_path / "manuscript" / "ch03"
    d.mkdir(parents=True)
    for name, text in files.items():
        (d / name).write_text(text, encoding="utf-8")
    book = Book(tmp_path)
    if formulas is not None:
        book.state.save("formulas", formulas)
    if dangling is not None:
        (tmp_path / "known_dangling.txt").write_text(dangling, encoding="utf-8")
    return book


def regs(*sections):
    return {
        "formula_to_page": {},
        "page_to_formulas": {},
        "formulas": {
            f"eq3-{s.replace('.', '-')}-{i}": {
                "number": f"({i})", "latex": "x", "risk": 0, "section": s}
            for s in sections for i in (1,)
        },
    }


def kinds(rep):
    return [i.kind for i in rep.issues]


def test_clean_ok(tmp_path):
    book = make(tmp_path, CLEAN_FILES, formulas=regs("3.1"))
    rep = check_book(book)
    assert rep.ok, rep.issues
    assert rep.footnotes == 0


def test_eq_gap(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = ("## 3.1 Intro {#sec-3-1}\n\n$$\na \\qquad (1)\n$$\n\n"
                          "$$\nb \\qquad (3)\n$$\n")
    book = make(tmp_path, files, formulas={
        "formula_to_page": {}, "page_to_formulas": {},
        "formulas": {
            "a": {"number": "(1)", "section": "3.1"},
            "b": {"number": "(3)", "section": "3.1"}}})
    rep = check_book(book)
    assert "eq_gap" in kinds(rep)


def test_eq_expected_mismatch(tmp_path):
    book = make(tmp_path, CLEAN_FILES, formulas=regs("3.1"))
    rep = check_book(book, expected={"ch03": {"sec3-1.md": 9}})
    assert "eq_expected" in kinds(rep)


def test_eq_start_warning(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = ("## 3.1 Intro {#sec-3-1}\n\n$$\na \\qquad (2)\n$$\n")
    book = make(tmp_path, files, formulas={
        "formula_to_page": {}, "page_to_formulas": {},
        "formulas": {"eq3-1-1": {"number": "(2)", "section": "3.1"}}})
    rep = check_book(book)
    assert "eq_start" in kinds(rep)
    assert rep.ok  # warning only


def test_footnote_imbalance(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\nText[^x].\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    rep = check_book(book)
    assert "footnote" in kinds(rep)


def test_dangling_link(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\nSee [x](#sec-9-9).\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    assert "link" in kinds(check_book(book))


def test_dangling_link_whitelisted(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\nSee [x](#sec-9-9).\n"
    book = make(tmp_path, files, formulas=regs("3.1"), dangling="sec-9-9\n")
    assert "link" not in kinds(check_book(book))


def test_residues(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = ("## 3.1 Intro {#sec-3-1}\n\n"
                          "a \\tag{1} b \\mathcal{E} c ■\n")
    book = make(tmp_path, files, formulas=regs("3.1"))
    rep = check_book(book)
    assert kinds(rep).count("residue") >= 3


def test_heading_without_anchor(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\n## Bad Heading\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    assert "anchor" in kinds(check_book(book))


def test_page_anchor_residue(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\n<!-- page:100 -->\ntext\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    assert "page_anchor" in kinds(check_book(book))


def test_table_placeholder_missing_file(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\n<!-- TABLE 3.2 -->\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    assert "table" in kinds(check_book(book))
    (tmp_path / "manuscript" / "ch03" / "tab3-2.md").write_text(
        "| a |\n", encoding="utf-8")
    assert "table" not in kinds(check_book(book))


def test_figure_missing_file(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\n![F](figures/fig3-2.png)\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    assert "figure" in kinds(check_book(book))
    (tmp_path / "figures").mkdir()
    (tmp_path / "figures" / "fig3-2.png").write_bytes(b"x")
    assert "figure" not in kinds(check_book(book))


def test_registry_mismatch(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\n$$\na\n$$\n\n$$\nb\n$$\n"
    book = make(tmp_path, files, formulas={"formula_to_page": {},
                                           "page_to_formulas": {}, "formulas": {}})
    assert "registry" in kinds(check_book(book))


def test_section_key_from_filename():
    assert section_key_from_filename("sec8-10.md") == "8.10"
    assert section_key_from_filename("sec3-1.md") == "3.1"
    assert section_key_from_filename("sec3-problems.md") == "Problems"
    assert section_key_from_filename("00_chapter.md") is None


def test_count_display_equations():
    assert count_display_equations("a\n$$\nx\n$$\nb\n$$\ny\n$$\n") == 2
    assert count_display_equations("no math") == 0


def test_reference_anchors_are_global(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = "## 3.1 Intro {#sec-3-1}\n\nSee [X (1999)](#ref-x-1999).\n"
    book = make(tmp_path, files, formulas=regs("3.1"))
    references = book.manuscript_dir / "references"
    references.mkdir()
    (references / "references.md").write_text(
        "::: {#ref-x-1999 .ref-entry}\nX (1999).\n:::\n", encoding="utf-8")
    assert "link" not in kinds(check_book(book))


def test_inline_span_anchors_resolve_links(tmp_path):
    files = {
        "00_chapter.md": "# Chapter 3. T {#ch-3}\n",
        "sec3-1.md": (
            "## 3.1 Intro {#sec-3-1}\n\n"
            "See [Theorem 3.1.1](#thm-3-1-1).\n\n"
            "<span id=\"thm-3-1-1\"></span>**Theorem 3.1.1.** *A statement.*\n"),
    }
    report = check_book(make(tmp_path, files))
    assert "link" not in kinds(report)


def test_duplicate_anchor_ids_are_reported(tmp_path):
    files = {
        "00_chapter.md": "# Chapter 3. T {#ch-3}\n",
        "sec3-1.md": "## 3.1 Intro {#sec-3-1}\n\n<span id=\"dup\"></span>x\n",
        "sec3-2.md": "## 3.2 Methods {#sec-3-2}\n\n<span id=\"dup\"></span>y\n",
    }
    report = check_book(make(tmp_path, files))
    assert "anchor_duplicate" in kinds(report)


def test_unresolved_figure_placeholder_is_reported(tmp_path):
    files = {
        "00_chapter.md": "# Chapter 3. T {#ch-3}\n",
        "sec3-1.md": "## 3.1 Intro {#sec-3-1}\n\n<!-- FIGURE 3.1 -->\n",
    }
    report = check_book(make(tmp_path, files))
    assert "object_placeholder" in kinds(report)


def test_safe_math_rewrite_is_applied_and_not_reported(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = (
        "## 3.1 Intro {#sec-3-1}\n\n"
        "$$\na \\Bigm| b \\qquad (1)\n$$\n")
    book = make(tmp_path, files, formulas=regs("3.1"))
    report = check_book(book)
    assert "math" not in kinds(report)
    assert report.normalized
    assert r"\Big|" in (book.manuscript_dir / "ch03" / "sec3-1.md").read_text()


def test_bad_math_is_a_check_error(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = (
        "## 3.1 Intro {#sec-3-1}\n\n"
        "A bare $\\boldsymbol$ command.\n\n"
        "$$\nx = 1 \\qquad (1)\n$$\n")
    report = check_book(make(tmp_path, files, formulas=regs("3.1")))
    assert "math" in kinds(report)
    assert any("argument" in issue.message for issue in report.issues)


def test_unbalanced_math_brace_is_a_check_error(tmp_path):
    files = dict(CLEAN_FILES)
    files["sec3-1.md"] = (
        "## 3.1 Intro {#sec-3-1}\n\n"
        "$$\nx{y{z} \\qquad (1)\n$$\n")
    report = check_book(make(tmp_path, files, formulas=regs("3.1")))
    assert "math" in kinds(report)
    assert any("unbalanced" in issue.message for issue in report.issues)
