"""Batch merge tests (TDD): page anchors, section routing, registration,
footnotes, seams, table extraction, idempotency."""

import pytest

from pdfoenix.core.book import Book
from pdfoenix.core.merge import MergeError, merge_chapter


def make_book(tmp_path, batches: dict[str, str]) -> Book:
    d = tmp_path / "working" / "batches" / "ch03"
    d.mkdir(parents=True)
    for name, text in batches.items():
        (d / f"{name}.md").write_text(text, encoding="utf-8")
    return Book(tmp_path)


def sec(book, name):
    return (book.manuscript_dir / "ch03" / name).read_text(encoding="utf-8")


BATCH = r"""# Chapter 3. Test Chapter {#ch-3}

## 3.1 Introduction {#sec-3-1}

<!-- page:100 -->
Some prose.

<!-- page:101 -->
$$
x = 1 \qquad (1)
$$

## 3.2 Methods {#sec-3-2}

<!-- page:102 -->
$$
\hat{\boldsymbol{\beta}} = 2 \qquad (2)
$$

$$
y = 3
$$
"""


def test_chapter_title(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    merge_chapter(book, "ch03")
    assert sec(book, "00_chapter.md").strip() == "# Chapter 3. Test Chapter {#ch-3}"


def test_sections_written(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    merge_chapter(book, "ch03")
    assert "## 3.1 Introduction" in sec(book, "sec3-1.md")
    assert "## 3.2 Methods" in sec(book, "sec3-2.md")


def test_page_anchors_stripped(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    merge_chapter(book, "ch03")
    for f in ("sec3-1.md", "sec3-2.md"):
        assert "<!-- page:" not in sec(book, f)


def test_equation_registration(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    s = merge_chapter(book, "ch03")
    reg = book.state.load("formulas")
    assert s.formulas == 3
    ids = sorted(reg["formulas"])
    assert ids == ["eq3-3-1-1", "eq3-3-2-1", "eq3-3-2-2"]
    assert reg["formulas"]["eq3-3-1-1"]["number"] == "(1)"
    assert reg["formulas"]["eq3-3-1-1"]["section"] == "3.1"
    assert reg["formulas"]["eq3-3-2-1"]["number"] == "(2)"
    assert reg["formulas"]["eq3-3-2-2"]["number"] is None


def test_merge_materializes_safe_math_rewrites(tmp_path):
    batch = r"""# Chapter 3. Test Chapter {#ch-3}

## 3.1 Introduction {#sec-3-1}

<!-- page:100 -->
$$
a \Bigm| b \qquad (1)
$$

$$
\Gamma(\\frac{1}{2}p) \qquad (2)
$$
"""
    book = make_book(tmp_path, {"p100-100": batch})
    merge_chapter(book, "ch03")
    text = sec(book, "sec3-1.md")
    assert r"\Big|" in text
    assert r"\frac{1}{2}" in text
    assert r"\Bigm" not in text
    reg = book.state.load("formulas")
    assert r"\Bigm" not in reg["formulas"]["eq3-3-1-1"]["latex"]
    assert r"\\frac" not in reg["formulas"]["eq3-3-1-2"]["latex"]


def test_equation_pages_and_risk(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    merge_chapter(book, "ch03")
    reg = book.state.load("formulas")
    assert reg["formula_to_page"]["eq3-3-1-1"] == "working/pages/pdfpage_101.png"
    assert reg["formula_to_page"]["eq3-3-2-1"] == "working/pages/pdfpage_102.png"
    assert reg["formulas"]["eq3-3-1-1"]["risk"] == 0
    assert reg["formulas"]["eq3-3-2-1"]["risk"] == 5   # hat3 + bold2


def test_bidirectional_index_consistent(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    merge_chapter(book, "ch03")
    reg = book.state.load("formulas")
    for eq_id, page in reg["formula_to_page"].items():
        assert eq_id in reg["page_to_formulas"][page]


def test_figure_placeholder(tmp_path):
    text = r"""# Chapter 3. T {#ch-3}

## 3.1 Intro {#sec-3-1}

<!-- page:100 -->
<!-- FIGURE 3.1. Caption: A demo. Content: axes 1 and 2 -->
"""
    book = make_book(tmp_path, {"p100-100": text})
    merge_chapter(book, "ch03")
    body = sec(book, "sec3-1.md")
    assert "![Figure 3.1. A demo.](figures/fig3-1.png)" in body
    reg = book.state.load("figures")
    assert reg["figures"]["fig3-1"]["caption"] == "Figure 3.1. A demo."
    assert "axes 1 and 2" in reg["figures"]["fig3-1"]["description"]


def test_existing_image_ref_registered(tmp_path):
    text = r"""# Chapter 3. T {#ch-3}

## 3.1 Intro {#sec-3-1}

<!-- page:100 -->
![Figure 3.2. Redrawn.](figures/fig3-2-1.png)
"""
    book = make_book(tmp_path, {"p100-100": text})
    merge_chapter(book, "ch03")
    reg = book.state.load("figures")
    assert reg["figures"]["fig3-2-1"]["file"] == "figures/fig3-2-1.png"


def test_table_extraction(tmp_path):
    text = r"""# Chapter 3. T {#ch-3}

## 3.1 Intro {#sec-3-1}

<!-- page:100 -->
**Table 3.2**. Demo table.

| a | b |
|---|---|
| 1 | 2 |

*† From somewhere.*
"""
    book = make_book(tmp_path, {"p100-100": text})
    merge_chapter(book, "ch03")
    body = sec(book, "sec3-1.md")
    assert "<!-- TABLE 3.2 -->" in body
    assert "*† From somewhere.*" in body
    tab = (book.manuscript_dir / "ch03" / "tab3-2.md").read_text(encoding="utf-8")
    assert "**Table 3.2**. Demo table." in tab
    assert "| a | b |" in tab
    reg = book.state.load("tables")
    assert reg["tables"]["3.2"]["md_file"] == "manuscript/ch03/tab3-2.md"


def test_uncertain_kept_and_other_comment_removed(tmp_path):
    text = r"""# Chapter 3. T {#ch-3}

## 3.1 Intro {#sec-3-1}

<!-- page:100 -->
Text <!-- uncertain: printed thus --> more.
<!-- note: internal only -->
"""
    book = make_book(tmp_path, {"p100-100": text})
    merge_chapter(book, "ch03")
    body = sec(book, "sec3-1.md")
    assert "<!-- uncertain: printed thus -->" in body
    assert "internal only" not in body


def test_mathcal_normalized(tmp_path):
    text = r"""# Chapter 3. T {#ch-3}

## 3.1 Intro {#sec-3-1}

<!-- page:100 -->
$\mathcal{E}(X)$
"""
    book = make_book(tmp_path, {"p100-100": text})
    merge_chapter(book, "ch03")
    assert r"\mathscr{E}" in sec(book, "sec3-1.md")


def test_legacy_batch_synthetic_page_anchor(tmp_path):
    text = r"""## 3.1 Intro {#sec-3-1}

Some text.

$$
z = 9 \qquad (5)
$$
"""
    book = make_book(tmp_path, {"p100-102": text})
    merge_chapter(book, "ch03")
    reg = book.state.load("formulas")
    assert reg["formula_to_page"]["eq3-3-1-1"] == "working/pages/pdfpage_100.png"
    assert reg["formulas"]["eq3-3-1-1"]["number"] == "(5)"


def test_progress_updated(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    merge_chapter(book, "ch03")
    prog = book.state.load("progress")
    assert prog["chapters"]["ch03"]["merged"] is True
    assert prog["chapters"]["ch03"]["formulas"] == 3


def test_subsection_routed_to_parent(tmp_path):
    text = r"""# Chapter 3. T {#ch-3}

## 3.2 Methods {#sec-3-2}

<!-- page:100 -->
Body.

## 3.2.1 Sub {#sec-3-2-1}

<!-- page:101 -->
Sub body.
"""
    book = make_book(tmp_path, {"p100-101": text})
    merge_chapter(book, "ch03")
    body = sec(book, "sec3-2.md")
    assert "## 3.2 Methods" in body and "## 3.2.1 Sub" in body
    assert not (book.manuscript_dir / "ch03" / "sec3-2-1.md").exists()


def test_idempotent(tmp_path):
    book = make_book(tmp_path, {"p100-102": BATCH})
    s1 = merge_chapter(book, "ch03")
    f1 = sec(book, "sec3-1.md")
    s2 = merge_chapter(book, "ch03")
    f2 = sec(book, "sec3-1.md")
    assert s1.formulas == s2.formulas == 3
    assert f1 == f2
    reg = book.state.load("formulas")
    assert len(reg["formulas"]) == 3


def test_page_anchor_carries_across_section(tmp_path):
    # The page anchor sits before the next heading (ending the previous block),
    # so the next section's formula must inherit that page.
    text = r"""# Chapter 3. T {#ch-3}

## 3.1 Intro {#sec-3-1}

<!-- page:100 -->
body

<!-- page:101 -->
## 3.2 Methods {#sec-3-2}

$$
q = 2 \qquad (1)
$$
"""
    book = make_book(tmp_path, {"p100-101": text})
    merge_chapter(book, "ch03")
    reg = book.state.load("formulas")
    assert reg["formula_to_page"]["eq3-3-2-1"] == "working/pages/pdfpage_101.png"


def test_no_section_raises(tmp_path):
    book = make_book(tmp_path, {"p100-100": "# Chapter 3. T {#ch-3}\n\nprose\n"})
    with pytest.raises(MergeError):
        merge_chapter(book, "ch03")


def test_chapter_title_normalized(tmp_path):
    batch = BATCH.replace(
        "# Chapter 3. Test Chapter {#ch-3}", "# CHAPTER 3 Test Chapter")
    book = make_book(tmp_path, {"p100-102": batch})
    merge_chapter(book, "ch03")
    assert sec(book, "00_chapter.md").strip() == \
        "# Chapter 3. Test Chapter {#ch-3}"


def test_uppercase_problems_heading_uses_canonical_section_key(tmp_path):
    batch = r"""# Chapter 3. Test Chapter {#ch-3}

## PROBLEMS

<!-- page:110 -->
$$
x = 1 \qquad (1)
$$
"""
    book = make_book(tmp_path, {"p110-110": batch})
    merge_chapter(book, "ch03")

    assert (book.manuscript_dir / "ch03" / "sec3-problems.md").exists()
    reg = book.state.load("formulas")["formulas"]
    assert sorted(reg) == ["eq3-Problems-1"]
    assert reg["eq3-Problems-1"]["section"] == "Problems"


def test_remerge_preserves_manual_crop_box(tmp_path):
    batch = r"""# Chapter 3. Test Chapter {#ch-3}

## 3.1 Introduction {#sec-3-1}

<!-- page:100 -->
<!-- FIGURE 3.1. Caption: A plot. Content: A simple plot. -->
"""
    book = make_book(tmp_path, {"p100-100": batch})
    merge_chapter(book, "ch03")
    crop = [0.1, 0.2, 0.9, 0.8]

    def mutate(data):
        data["figures"]["fig3-1"]["crop"] = crop
        data["figures"]["fig3-1"]["crop_source"] = "manual"

    book.state.update("figures", mutate)
    merge_chapter(book, "ch03")
    entry = book.state.load("figures")["figures"]["fig3-1"]
    assert entry["crop"] == crop
    assert entry["crop_source"] == "manual"
