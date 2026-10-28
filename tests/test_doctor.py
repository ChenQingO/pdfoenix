"""Whole-pipeline audit tests."""
from pdfoenix.core.book import Book
from pdfoenix.doctor import collect_findings, run_doctor


def make_book(tmp_path, files):
    directory = tmp_path / "manuscript" / "ch01"
    directory.mkdir(parents=True)
    for name, text in files.items():
        (directory / name).write_text(text, encoding="utf-8")
    return Book(tmp_path)


def messages(findings):
    return "\n".join(f"{item.area}: {item.message}" for item in findings)


def test_doctor_reports_duplicate_anchor_and_reference_candidate(tmp_path):
    book = make_book(tmp_path, {
        "sec1-1.md": (
            "## 1.1 Intro {#dup}\n\n"
            "<span id=\"dup\"></span>text\n\n"
            "See [Elfving](#ref-elfvig-1947).\n"),
        "sec1-2.md": (
            "# References {#references}\n\n"
            "Elfving, G. (1947), A title.\n"),
    })
    text = messages(collect_findings(book))
    assert "duplicate anchor id 'dup'" in text
    assert "dangling #ref-elfvig-1947" in text
    assert "ref-elfving-1947" in text


def test_doctor_flags_unresolved_figure_placeholder(tmp_path):
    book = make_book(tmp_path, {
        "sec1-1.md": "## 1.1 Intro {#sec-1-1}\n\n<!-- FIGURE 1.1 -->\n",
    })
    text = messages(collect_findings(book))
    assert "unresolved FIGURE placeholder" in text


def test_doctor_returns_zero_for_clean_manuscript(tmp_path, capsys):
    book = make_book(tmp_path, {
        "sec1-1.md": "## 1.1 Intro {#sec-1-1}\n\nPlain text.\n",
    })
    assert run_doctor(book) == 0
    assert "doctor: 0 error(s), 0 warning(s)" in capsys.readouterr().out
