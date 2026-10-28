"""Deterministic anchor generation and link repair tests."""
from pdfoenix.core.anchors import (
    add_anchors,
    anchor_ids,
    match_reference,
    parse_reference_entries,
    resolve_links,
)


def test_statement_and_figure_anchors_are_added_once():
    text = ("**Theorem 8.10.3.** *A statement.*\n\n"
            "![Figure 8.2. A plot.](figures/fig8-2.png)\n")
    out, entries = add_anchors(text)
    assert '<span id="thm-8-10-3"></span>**Theorem 8.10.3.**' in out
    assert '<span id="fig-8-2"></span>\n![Figure 8.2.' in out
    assert entries == []
    assert anchor_ids(out) == {"thm-8-10-3", "fig-8-2"}

    # Re-running is idempotent.
    again, _ = add_anchors(out)
    assert again == out


def test_reference_anchors_prefer_self_link_and_clean_html():
    text = (
        "# References {#references}\n\n"
        "**[Pillai, K. C. S. (1954)](#ref-pillai-1954)**, A title.\n\n"
        "<span id=\"ref-anderson-1946a\"></span>Anderson, T. W. (1946a), "
        "Another title.\n")
    out, entries = add_anchors(text)
    assert '<span id="ref-pillai-1954"></span>**[Pillai' in out
    assert out.count('<span id="ref-anderson-1946a">') == 1
    assert "</span></span>" not in out
    assert {entry.id for entry in entries} == {
        "ref-pillai-1954", "ref-anderson-1946a"}


def test_reference_parsing_tolerates_damaged_year_and_accents():
    text = (
        "# References {#references}\n\n"
        "**Elfving, G. (1947)**, A title.\n\n"
        "**Reiersøl, Olav (1950)**, Another title.\n\n"
        "**[Thomson, Godfrey H. (193.)](#ref-thomson-193)**"
        "<!-- uncertain -->, Damaged year.\n")
    entries = parse_reference_entries(text)
    by_id = {entry.id: entry for entry in entries}
    assert by_id["ref-elfving-1947"].year == "1947"
    assert by_id["ref-reiersol-1950"].year == "1950"
    assert by_id["ref-thomson-193"].year == "193"


def test_link_resolution_handles_case_alias_and_parent_section():
    ids = {"sec-A-5", "lem-3-2-2", "sec-3-3"}
    text = "[a](#sec-a-5) [b](#lemma-3-2-2) [c](#sec-3-3-5)"
    out, fixed = resolve_links(text, ids, [])
    assert fixed == 3
    assert out == "[a](#sec-A-5) [b](#lem-3-2-2) [c](#sec-3-3)"


def test_reference_fuzzy_match_fixes_typo():
    entries = parse_reference_entries(
        "# References {#references}\n\nElfving, G. (1947), A title.\n")
    match = match_reference("ref-elfvig-1947", entries)
    assert match is not None and match.id == "ref-elfving-1947"
