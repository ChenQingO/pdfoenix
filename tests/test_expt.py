"""Model-combination experiment tests (TDD)."""
import copy
import re
import shutil
from typing import ClassVar

import pymupdf as fitz
import pytest

from pdfoenix import combo, expt
from pdfoenix.core.book import Book
from pdfoenix.core.config import (
    DEFAULT_CONFIG,
    ConfigError,
    load_config,
    parse_model_spec,
    write_config,
)
from pdfoenix.core.telemetry import Telemetry
from pdfoenix.transcribe.protocol import Correction

ANSWER_MODEL = "acme/writer#high"


def make_book(tmp_path, combos=None, provider="acme"):
    pdf = tmp_path / "book.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page()
    doc.save(pdf)
    doc.close()
    (tmp_path / "source").mkdir(exist_ok=True)
    shutil.copy2(pdf, tmp_path / "source" / "book.pdf")
    for page in (1, 2, 3):
        for directory in ("working/pages", "working/verify"):
            target = tmp_path / directory / f"pdfpage_{page:03d}.png"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"png")
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["book"] = {"title": "Combo Book", "author": "",
                      "pdf": "source/book.pdf", "page_offset": 0}
    config["expt"]["pages"] = "1-3"
    config["expt"]["provider"] = [
        {"name": provider, "base_url": "http://acme.test/v1",
         "api_key_env": "ACME_API_KEY"}]
    config["expt"]["combo"] = combos if combos is not None else [
        {"id": 1, "transcribe": "acme/writer", "verify": "acme/checker"}]
    write_config(tmp_path / "config.toml", config)
    return Book(tmp_path)


class FakeAdapter:
    """Deterministic transcriber/verifier recording calls into the ledger."""

    made: ClassVar[list] = []

    def __init__(self, _book, telemetry, **_kwargs):
        self.telemetry = telemetry
        FakeAdapter.made.append(self)

    def transcribe(self, images, _task):
        self.telemetry.record_call(
            stage="transcribe", role="transcribe", model="writer",
            unit_id="p1-3", input_text_tokens=10, output_tokens=20)
        parts = []
        for image in images:
            page = int(re.findall(r"\d+", image.stem)[0])
            parts.append(f"<!-- page:{page} -->\ncontent {page}")
        return "\n\n".join(parts)

    def verify(self, images, _content):
        call_id = self.telemetry.record_call(
            stage="verify", role="verify", model="checker",
            unit_id="p1-3", n_images=len(images),
            input_text_tokens=5, output_tokens=5)
        return [Correction(unit_id="p1", severity="introduced",
                           pattern="a→b", source="ocr", call_id=call_id)]

    def close(self):
        pass


@pytest.fixture
def fake_adapter(monkeypatch):
    FakeAdapter.made = []
    monkeypatch.setattr("pdfoenix.pipeline.make_transcriber", FakeAdapter)
    return FakeAdapter


# ── Model specs and configuration ───────────────────────────────────────
def test_parse_model_spec_splits_provider_model_thinking():
    assert parse_model_spec("acme/writer#high") == ("acme", "writer", "high")
    assert parse_model_spec("acme/deep/model") == ("acme", "deep/model", "")
    with pytest.raises(ConfigError):
        parse_model_spec("no-slash")


def test_config_rejects_invalid_combos(tmp_path):
    bad = [
        [{"id": 0, "transcribe": "acme/a", "verify": "acme/b"}],
        [{"id": 1, "transcribe": "other/a", "verify": "acme/b"}],
        [{"id": 1, "transcribe": "acme/a", "verify": "acme/a"}],
    ]
    for combos in bad:
        book = make_book(tmp_path, combos=combos)
        with pytest.raises(ConfigError):
            load_config(book.root / "config.toml")


def test_combo_pair_resolves_thinking_default(tmp_path):
    book = make_book(tmp_path)
    pair = combo.combo_pair(combo.combos(book)[1])
    assert pair["transcribe"] == {"spec": "acme/writer", "model": "writer",
                                  "thinking": "default"}
    assert pair["verify"]["model"] == "checker"


def test_generate_opencode_config_from_providers(tmp_path):
    book = make_book(tmp_path)
    path = combo.generate_opencode_config(book)
    data = path.read_text(encoding="utf-8")
    assert '"baseURL": "http://acme.test/v1"' in data
    assert "{env:ACME_API_KEY}" in data
    assert '"writer"' in data and '"checker"' in data
    assert "pdfoenix-transcribe" in data   # agent skeleton kept


# ── Batch bookkeeping ───────────────────────────────────────────────────
def test_batches_are_created_and_replaced(tmp_path):
    book = make_book(tmp_path)
    expt.ensure_workspace(book)
    batch_no = expt.create_batch(book, ["1"], message="first round")
    assert batch_no == 1
    assert expt.batch_members(book, 1) == ["1"]
    expt.set_batch_combos(book, 1, ["1", "2"])
    assert expt.batch_members(book, 1) == ["1", "2"]
    assert expt.create_batch(book, ["1"]) == 2
    with pytest.raises(ConfigError):
        expt.set_batch_combos(book, 99, ["1"])


# ── Runner lifecycle ────────────────────────────────────────────────────
def test_run_expt_runs_cell_and_skips_on_rerun(tmp_path, fake_adapter):
    book = make_book(tmp_path)
    assert expt.run_expt(book, combo_ids=["1"], message="b1") == 0

    cell = expt.expt_dir(book) / "batch_1" / "combo_1"
    assert (cell / "batches" / "p1-3.md").exists()
    assert (cell / "progress.json").exists()

    rows = expt.report_rows(book)
    assert len(rows) == 1
    assert rows[0]["pages"] == "3/3"
    assert rows[0]["corrections"] == 1
    assert rows[0]["error_rate"] == pytest.approx(100 / 3, rel=0.01)
    assert rows[0]["status"] == "done"

    made = len(fake_adapter.made)
    assert expt.run_expt(book, batch_no=1) == 0       # completed: skip
    assert len(fake_adapter.made) == made
    assert expt.run_expt(book, batch_no=1, force=True) == 0  # --force: re-run
    assert len(fake_adapter.made) > made


def test_run_expt_requires_selection_or_known_combos(tmp_path, fake_adapter):
    book = make_book(tmp_path)
    with pytest.raises(ConfigError):
        expt.run_expt(book)
    with pytest.raises(ConfigError):
        expt.run_expt(book, combo_ids=["9"])
    assert fake_adapter.made == []      # nothing was executed


def test_batch_replace_semantics_on_run(tmp_path, fake_adapter):
    book = make_book(tmp_path, combos=[
        {"id": 1, "transcribe": "acme/writer", "verify": "acme/checker"},
        {"id": 2, "transcribe": "acme/checker", "verify": "acme/writer"},
    ])
    assert expt.run_expt(book, combo_ids=["1", "2"]) == 0
    assert expt.batch_members(book, 1) == ["1", "2"]
    # --batch 1 --combo 2 --run replaces the set and keeps old data on disk
    assert expt.run_expt(book, batch_no=1, combo_ids=["2"], force=True) == 0
    assert expt.batch_members(book, 1) == ["2"]
    assert (expt.expt_dir(book) / "batch_1" / "combo_1").exists()
    assert expt.report_rows(book)
    assert fake_adapter.made                # cells really executed


# ── Metrics and structural checks ───────────────────────────────────────
def test_structural_stats_detects_defects(tmp_path):
    root = tmp_path / "batches"
    root.mkdir()
    (root / "p220-224.md").write_text(
        "<!-- page:202 -->\nprinted label\n\n<!-- page:221 -->\nfine\n",
        encoding="utf-8")
    (root / "p507-511.md").write_text(
        "text first\n\n<function=execute>ls</function>\n", encoding="utf-8")
    stats = expt.structural_stats(root)
    assert stats["anchor_defects"] == 1      # page:202 outside p220-224
    assert stats["markup_leaks"] == 1
    assert stats["covered_pages"] == {221, 507}


def test_ledger_migrates_old_experiment_id(tmp_path):
    import sqlite3

    (tmp_path / "logs").mkdir()
    connection = sqlite3.connect(tmp_path / "logs" / "pipeline.db")
    connection.execute(
        "CREATE TABLE llm_calls (call_id TEXT PRIMARY KEY, run_id TEXT,"
        " experiment_id TEXT, stage TEXT, role TEXT, model TEXT, provider TEXT,"
        " thinking_mode TEXT, prompt_version TEXT, unit_id TEXT, n_images INT,"
        " input_text_tokens INT, input_vision_tokens INT, output_tokens INT,"
        " latency_ms INT, status TEXT, retry_of TEXT, error TEXT, ts TEXT)")
    connection.execute(
        "INSERT INTO llm_calls (call_id, run_id, experiment_id, stage, role,"
        " model) VALUES ('c1', 'r1', 'old-exp', 'transcribe', 'transcribe', 'm')")
    connection.commit()
    connection.close()

    ledger = Telemetry(tmp_path)
    try:
        rows = ledger.fetch_calls()
    finally:
        ledger.close()
    assert rows[0]["combo_id"] == "old-exp"
    assert rows[0]["batch_no"] is None


# ── CLI surface ─────────────────────────────────────────────────────────
def test_combos_are_declared_by_hand_and_displayed(tmp_path, capsys):
    """Combos are plain config.toml entries; init never asks anything."""
    pdf = tmp_path / "book.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf)
    doc.close()
    root = tmp_path / "the_book"

    from pdfoenix.cli import main

    assert main(["init", str(root), "--pdf", str(pdf)]) == 0
    config = load_config(root / "config.toml")
    # the value-sweet-spot default combo 1 ships with the defaults
    assert any(entry["id"] == 1 for entry in config["expt"]["combo"])

    # a new combo is added by editing config.toml — the only config file
    config["expt"]["provider"].append(
        {"name": "acme", "base_url": "http://acme.test/v1",
         "api_key_env": "ACME_API_KEY"})
    config["expt"]["combo"].append(
        {"id": 2, "transcribe": "acme/writer#high", "verify": "acme/checker"})
    config["expt"]["active"] = 2
    config["expt"]["pages"] = "1-1"
    write_config(root / "config.toml", config)

    book = Book(root)
    assert combo.active_combo(book)["id"] == 2
    assert combo.combos(book)[2]["verify"] == "acme/checker"

    assert main(["-C", str(root), "expt", "show"]) == 0
    out = capsys.readouterr().out
    assert "(no rows)" in out
