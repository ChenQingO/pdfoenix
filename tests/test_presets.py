"""Packaged preset existence and parse tests (TDD)."""
import tomllib
from pathlib import Path

PRESETS = Path(__file__).resolve().parents[1] / "pdfoenix" / "presets"


def test_all_presets_exist():
    for name in ("epub.css", "opencode.jsonc", "transcribe.md", "verify.md",
                 "pricing.toml", "config.schema.toml"):
        path = PRESETS / name
        assert path.exists() and path.stat().st_size > 0


def test_schema_and_pricing_parse():
    with open(PRESETS / "config.schema.toml", "rb") as handle:
        schema = tomllib.load(handle)
    assert schema["sampling"]["high_min"] == 125
    with open(PRESETS / "pricing.toml", "rb") as handle:
        pricing = tomllib.load(handle)
    assert "pricing" in pricing
