"""Cost-ledger and event-log tests (TDD)."""
import pytest

from pdfoenix.core.telemetry import Telemetry, compute_cost


@pytest.fixture
def tel(tmp_path):
    t = Telemetry(tmp_path, run_id="run1", combo_id=1, batch_no=2)
    yield t
    t.close()


def test_creates_logs_dir_and_db(tmp_path):
    t = Telemetry(tmp_path)
    assert (tmp_path / "logs" / "pipeline.db").exists()
    t.close()


def test_record_call_roundtrip(tel):
    cid = tel.record_call(
        stage="transcribe", role="transcribe", model="m1", provider="oc",
        thinking_mode="high", prompt_version="v1", unit_id="ch01/p001-005",
        n_images=5, input_text_tokens=100, input_vision_tokens=9000,
        output_tokens=500, latency_ms=1234, status="ok")
    rows = tel.fetch_calls()
    assert len(rows) == 1
    r = rows[0]
    assert r["call_id"] == cid
    assert r["run_id"] == "run1"
    assert r["combo_id"] == "1"
    assert r["batch_no"] == 2
    assert r["input_vision_tokens"] == 9000
    assert r["status"] == "ok"
    assert r["ts"]


def test_unique_call_ids(tel):
    a = tel.record_call(stage="transcribe", role="transcribe", model="m")
    b = tel.record_call(stage="transcribe", role="transcribe", model="m")
    assert a != b


def test_record_correction(tel):
    cid = tel.record_correction(
        verify_call_id="vc1", unit_id="ch01/p001-005", formula_id="eq1-1-1",
        severity="introduced", pattern="sed→seb", source="hallucination")
    rows = tel.fetch_corrections()
    assert len(rows) == 1
    assert rows[0]["correction_id"] == cid
    assert rows[0]["severity"] == "introduced"
    assert rows[0]["pattern"] == "sed→seb"


def test_emit_event(tel):
    tel.emit_event("dispatch", chapter="ch01", workers=3)
    evs = tel.fetch_events()
    assert evs[-1]["type"] == "dispatch"
    assert '"workers": 3' in evs[-1]["payload"]


def test_human_log(tel):
    tel.log("starting merge", level="info")
    tel.log("oops", level="error")
    text = (tel.book_root / "logs" / "pipeline.log").read_text(encoding="utf-8")
    assert "starting merge" in text
    assert "[error] oops" in text


def test_persist_across_reopen(tmp_path):
    t = Telemetry(tmp_path, run_id="r")
    t.record_call(stage="verify", role="verify", model="m")
    t.close()
    t2 = Telemetry(tmp_path, run_id="r")
    assert len(t2.fetch_calls()) == 1
    t2.close()


def test_failed_status_and_error(tel):
    tel.record_call(stage="transcribe", role="transcribe", model="m",
                    status="failed", error="timeout")
    r = tel.fetch_calls()[0]
    assert r["status"] == "failed"
    assert r["error"] == "timeout"


def test_compute_cost():
    price = {"input_text": 1.0, "input_vision": 3.0, "output": 5.0}
    cost = compute_cost(input_text_tokens=1_000_000,
                        input_vision_tokens=1_000_000,
                        output_tokens=1_000_000, pricing=price)
    assert cost == pytest.approx(9.0)


def test_compute_cost_missing_keys_default_zero():
    assert compute_cost(1_000_000, 0, 0, pricing={}) == 0
