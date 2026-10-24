"""Cost report tests (TDD)."""
import pytest

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.report import (
    chapter_for_unit,
    load_pricing,
    summarize_costs,
)


def make_book(tmp_path):
    book = Book(tmp_path)
    book.state.save("scout", {
        "page_offset": 0,
        "chapters": [
            {"number": "1", "title": "One", "pdf_page": 1, "printed_page": 1},
            {"number": "2", "title": "Two", "pdf_page": 100, "printed_page": 100},
        ],
        "sections": [],
        "figures": [],
        "tables": [],
        "equations": [],
    })
    return book


def test_chapter_for_unit():
    ranges = {"ch01": (1, 99), "ch02": (100, 512)}
    assert chapter_for_unit("p50-55", ranges) == "ch01"
    assert chapter_for_unit("p100-104", ranges) == "ch02"
    assert chapter_for_unit("weird", ranges) == "unknown"


def test_load_pricing(tmp_path):
    path = tmp_path / "pricing.toml"
    path.write_text(
        '[pricing."m1"]\ninput_text = 1.0\ninput_vision = 2.0\noutput = 3.0\n',
        encoding="utf-8")
    pricing = load_pricing(path)
    assert pricing["m1"]["output"] == 3.0


def test_summarize_costs_by_model(tmp_path):
    book = make_book(tmp_path)
    tel = Telemetry(tmp_path)
    tel.record_call(
        stage="transcribe", role="transcribe", model="m1", provider="p",
        input_text_tokens=1_000_000, input_vision_tokens=0, output_tokens=1_000_000)
    tel.record_call(
        stage="verify", role="verify", model="m2", provider="p",
        input_text_tokens=500_000, input_vision_tokens=0, output_tokens=100_000)
    tel.close()

    pricing = {
        "m1": {"input_text": 1.0, "input_vision": 2.0, "output": 5.0},
        "m2": {"input_text": 2.0, "input_vision": 4.0, "output": 10.0},
    }
    rows = summarize_costs(book, by="model", pricing=pricing)
    assert [row["key"] for row in rows] == ["m1", "m2"]
    assert rows[0]["cost_usd"] == pytest.approx(6.0)
    assert rows[1]["cost_usd"] == pytest.approx(1.0 + 1.0)


def test_summarize_costs_by_chapter(tmp_path):
    book = make_book(tmp_path)
    tel = Telemetry(tmp_path)
    tel.record_call(
        stage="transcribe", role="transcribe", model="m1",
        unit_id="p50-55", input_text_tokens=1000)
    tel.record_call(
        stage="transcribe", role="transcribe", model="m1",
        unit_id="p100-104", input_text_tokens=2000)
    tel.close()

    rows = summarize_costs(book, by="chapter", pricing={})
    assert [row["key"] for row in rows] == ["ch01", "ch02"]
    assert [row["input_text_tokens"] for row in rows] == [1000, 2000]


def test_missing_pricing_yields_zero_cost(tmp_path):
    book = make_book(tmp_path)
    tel = Telemetry(tmp_path)
    tel.record_call(stage="transcribe", role="transcribe", model="unknown",
                    input_text_tokens=1_000_000)
    tel.close()
    assert summarize_costs(book, pricing={})[0]["cost_usd"] == 0.0


# ── Risk calibration report ─────────────────────────────────────────────
def make_risk_book(tmp_path):
    book = make_book(tmp_path)
    chapter = book.manuscript_dir / "ch01"
    chapter.mkdir(parents=True)
    (chapter / "sec1-1.md").write_text(
        "## 1.1 Intro {#sec-1-1}\n\n$$\nx^2 \\qquad (1)\n$$\n\n"
        "$$\n\\frac{a}{b} \\qquad (2)\n$$\n",
        encoding="utf-8")
    book.pages_dir.mkdir(parents=True)
    for page in (1, 2, 3):
        (book.pages_dir / f"pdfpage_{page:03d}.png").write_bytes(b"png")
    book.state.save("formulas", {
        "formula_to_page": {},
        "page_to_formulas": {
            "working/pages/pdfpage_001.png": ["eq1"],
            "working/pages/pdfpage_002.png": ["eq2"],
        },
        "formulas": {"eq1": {"risk": 2}, "eq2": {"risk": 3}},
    })
    return book


def test_formula_and_page_risk_scores(tmp_path):
    from pdfoenix.report import formula_scores, risk_report

    book = make_risk_book(tmp_path)
    # x^2 has one subscript/superscript (2); a/b has one \frac (3).
    assert formula_scores(book) == [2, 3]
    report = risk_report(book)
    assert report["formulas"]["n"] == 2
    assert report["formulas"]["max"] == 3
    assert report["pages"]["n"] == 3
    assert report["bands"] == {"low": 3, "medium": 0, "high": 0}


def test_formula_scores_use_configured_weights(tmp_path):
    from pdfoenix.report import formula_scores

    book = make_risk_book(tmp_path)
    config = book.config
    config["sampling"]["risk_weights"] = {"frac": 10}
    weighted = Book(book.root, config=config)
    assert formula_scores(weighted) == [2, 10]


def test_format_risk_report_mentions_bands(tmp_path):
    from pdfoenix.report import format_risk_report, risk_report

    text = format_risk_report(risk_report(make_risk_book(tmp_path)))
    assert "formula risk" in text
    assert "page risk" in text
    assert "low <= 50" in text
