"""Cost/experiment report tests (TDD)."""
import pytest

from pdfoenix.core.book import Book
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.report import (
    chapter_for_unit,
    experiment_summary,
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
    scout = {"chapters": [
        {"number": "1", "pdf_page": 1},
        {"number": "2", "pdf_page": 100},
    ]}
    assert chapter_for_unit("p50-55", scout) == "ch01"
    assert chapter_for_unit("p100-104", scout) == "ch02"
    assert chapter_for_unit("weird", scout) == "unknown"


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


def test_experiment_summary_joins_corrections(tmp_path):
    book = make_book(tmp_path)
    tel = Telemetry(tmp_path, experiment_id="exp-a")
    call_id = tel.record_call(
        stage="verify", role="verify", model="m1", n_images=5,
        input_text_tokens=1000, output_tokens=100)
    tel.record_correction(
        verify_call_id=call_id, unit_id="p50-55", severity="introduced",
        pattern="sed→seb", source="ocr")
    tel.close()

    rows = experiment_summary(book, by="model", pricing={
        "m1": {"input_text": 1.0, "input_vision": 0.0, "output": 1.0}})
    assert len(rows) == 1
    row = rows[0]
    assert row["experiment_id"] == "exp-a"
    assert row["corrections"] == 1
    assert row["verify_pages"] == 5
    assert row["error_rate_per_100_pages"] == pytest.approx(20.0)
    assert row["cost_usd"] == pytest.approx(0.0011)


def test_missing_pricing_yields_zero_cost(tmp_path):
    book = make_book(tmp_path)
    tel = Telemetry(tmp_path)
    tel.record_call(stage="transcribe", role="transcribe", model="unknown",
                    input_text_tokens=1_000_000)
    tel.close()
    assert summarize_costs(book, pricing={})[0]["cost_usd"] == 0.0
