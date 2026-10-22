"""Cost ledger (SQLite) plus a human-readable run log.

Why this exists
---------------
Every LLM call is the only place where money is spent, so each call is recorded
with token counts (vision and text kept separate because they are priced
differently), model, thinking level and prompt version.  Currency is intentionally
NOT stored: cost is derived later from a pricing table, so a price change never
corrupts historical data.

The ``llm_calls`` and ``corrections`` tables can be joined on ``unit_id`` (and a
correction points at the verification call that found it), which is exactly what
the cost-vs-quality experiments need.
"""
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
  call_id TEXT PRIMARY KEY,
  run_id TEXT, experiment_id TEXT,
  stage TEXT, role TEXT,
  model TEXT, provider TEXT, thinking_mode TEXT, prompt_version TEXT,
  unit_id TEXT, n_images INTEGER,
  input_text_tokens INTEGER, input_vision_tokens INTEGER, output_tokens INTEGER,
  latency_ms INTEGER, status TEXT, retry_of TEXT, error TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS corrections (
  correction_id TEXT PRIMARY KEY,
  verify_call_id TEXT, unit_id TEXT, formula_id TEXT,
  severity TEXT, pattern TEXT, source TEXT, ts TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT, type TEXT, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_calls_run ON llm_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_calls_unit ON llm_calls(unit_id);
CREATE INDEX IF NOT EXISTS idx_corr_unit ON corrections(unit_id);
"""


def _now() -> str:
    """Current UTC timestamp in ISO-8601 (millisecond precision)."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def compute_cost(input_text_tokens: int, input_vision_tokens: int,
                 output_tokens: int, pricing: dict) -> float:
    """Cost in USD given token counts and a pricing dict (USD per 1M tokens)."""
    return (
        input_text_tokens / 1_000_000 * pricing.get("input_text", 0)
        + input_vision_tokens / 1_000_000 * pricing.get("input_vision", 0)
        + output_tokens / 1_000_000 * pricing.get("output", 0)
    )


class Telemetry:
    """Append-only cost ledger and event log for one book."""

    def __init__(self, book_root, run_id: str | None = None,
                 experiment_id: str | None = None):
        self.book_root = Path(book_root)
        self.logs_dir = self.book_root / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.logs_dir / "pipeline.db"
        self.log_path = self.logs_dir / "pipeline.log"
        self.run_id = run_id or f"run-{_now()}"
        self.experiment_id = experiment_id
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ── Writes ──────────────────────────────────────────────────────────
    def record_call(self, *, stage, role, model, provider="", thinking_mode="",
                    prompt_version="", unit_id="", n_images=0,
                    input_text_tokens=0, input_vision_tokens=0, output_tokens=0,
                    latency_ms=0, status="ok", retry_of=None, error=None,
                    call_id=None, experiment_id=None, ts=None) -> str:
        """Record one LLM call and return its id."""
        identifier = call_id or uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO llm_calls (call_id, run_id, experiment_id, stage, role,"
            " model, provider, thinking_mode, prompt_version, unit_id, n_images,"
            " input_text_tokens, input_vision_tokens, output_tokens, latency_ms,"
            " status, retry_of, error, ts) VALUES"
            " (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (identifier, self.run_id,
             experiment_id if experiment_id is not None else self.experiment_id,
             stage, role, model, provider, thinking_mode, prompt_version,
             unit_id, n_images, input_text_tokens, input_vision_tokens,
             output_tokens, latency_ms, status, retry_of, error, ts or _now()),
        )
        self._conn.commit()
        return identifier

    def record_correction(self, *, verify_call_id, unit_id, severity, pattern,
                          source, formula_id=None, correction_id=None,
                          ts=None) -> str:
        """Record one correction found during verification and return its id."""
        identifier = correction_id or uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO corrections (correction_id, verify_call_id, unit_id,"
            " formula_id, severity, pattern, source, ts)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (identifier, verify_call_id, unit_id, formula_id, severity, pattern,
             source, ts or _now()),
        )
        self._conn.commit()
        return identifier

    def emit_event(self, event_type: str, **payload) -> None:
        """Append a structured event (used by the live dashboard and debugging)."""
        self._conn.execute(
            "INSERT INTO events (ts, type, payload) VALUES (?,?,?)",
            (_now(), event_type, json.dumps(payload, ensure_ascii=False)),
        )
        self._conn.commit()

    def log(self, msg: str, level: str = "info") -> None:
        """Append a human-readable line to ``logs/pipeline.log``."""
        line = f"{_now()} [{level}] {msg}\n"
        with open(self.log_path, "a", encoding="utf-8") as handle:
            handle.write(line)

    # ── Reads (used by report / tests) ──────────────────────────────────
    def fetch_calls(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM llm_calls ORDER BY ts").fetchall()

    def fetch_corrections(self) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM corrections ORDER BY ts").fetchall()

    def fetch_events(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM events ORDER BY id").fetchall()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
