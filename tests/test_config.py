"""Configuration loading and validation tests (TDD)."""
import pytest

from pdfoenix.core.config import (
    DEFAULT_CONFIG,
    ConfigError,
    deep_merge,
    load_config,
    risk_weights,
)


def test_defaults_when_no_file():
    cfg = load_config()
    assert cfg["pipeline"]["batch_pages"] == 5
    assert cfg["pipeline"]["render_dpi"] == 150
    assert cfg["pipeline"]["verify_chunk_size"] == 5
    assert cfg["sampling"]["page_risk"] == "max"
    assert cfg["sampling"]["budget"] == "20%"
    assert cfg["sampling"]["low_max"] == 50
    assert cfg["sampling"]["high_min"] == 125
    assert cfg["sampling"]["high_prob"] == 0.60
    assert cfg["sampling"]["min_pages"] == 10
    assert cfg["sampling"]["min_rate"] == 0.10
    assert cfg["review"]["global_audit"] is True
    assert cfg["logging"]["level"] == "info"


def test_partial_override_preserves_siblings(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[pipeline]\nbatch_pages = 8\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["pipeline"]["batch_pages"] == 8
    assert cfg["pipeline"]["render_dpi"] == 150


def test_nested_override(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[sampling.risk_weights]\nfrac = 10\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["sampling"]["risk_weights"]["frac"] == 10
    assert cfg["sampling"]["risk_weights"]["sub_sup"] == 2


def test_book_section_override(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[book]\ntitle = "X"\npage_offset = 18\n', encoding="utf-8")
    cfg = load_config(p)
    assert cfg["book"]["title"] == "X"
    assert cfg["book"]["page_offset"] == 18


def test_invalid_int_type_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[pipeline]\nbatch_pages = "lots"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_page_risk_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[sampling]\npage_risk = "median"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_log_level_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('[logging]\nlevel = "verbose"\n', encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_non_table_toml_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text('x = 1\n', encoding="utf-8")   # top-level key, not a table? still dict
    # top-level TOML is always a table; use invalid TOML instead
    p.write_text("this is not toml = = =", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_unknown_keys_allowed(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[future]\nfoo = 1\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg["future"]["foo"] == 1


def test_deep_merge_dicts():
    base = {"a": {"x": 1, "y": 2}, "b": 1}
    out = deep_merge(base, {"a": {"y": 9}, "c": 3})
    assert out == {"a": {"x": 1, "y": 9}, "b": 1, "c": 3}


def test_deep_merge_lists_replaced():
    assert deep_merge({"a": [1, 2]}, {"a": [3]}) == {"a": [3]}


def test_defaults_not_mutated_by_caller():
    cfg = load_config()
    cfg["pipeline"]["batch_pages"] = 999
    assert DEFAULT_CONFIG["pipeline"]["batch_pages"] == 5


def test_risk_weights_helper():
    cfg = load_config()
    w = risk_weights(cfg)
    assert w["sub_sup"] == 2 and w["matrix"] == 5


def test_invalid_sampling_thresholds_raise(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[sampling]\nlow_max = 200\nhigh_min = 100\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


def test_invalid_sampling_probability_raises(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[sampling]\nhigh_prob = 1.5\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)
