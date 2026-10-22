"""State registry read/write tests (TDD)."""
import json

import pytest

from pdfoenix.core.state import (
    DEFAULTS,
    StateError,
    StateStore,
    atomic_write_json,
)


def test_all_six_registries_have_defaults():
    assert set(DEFAULTS) == {
        "scout", "formulas", "figures", "tables", "corrections", "progress"}


def test_load_missing_returns_deepcopy_default(tmp_path):
    st = StateStore(tmp_path)
    data = st.load("formulas")
    assert data == DEFAULTS["formulas"]
    data["formulas"]["x"] = 1
    assert DEFAULTS["formulas"]["formulas"] == {}


def test_save_load_roundtrip(tmp_path):
    st = StateStore(tmp_path)
    st.save("formulas", {
        "formula_to_page": {"eq1": "p1.png"},
        "page_to_formulas": {"p1.png": ["eq1"]},
        "formulas": {"eq1": {"number": "(1)", "latex": "x", "risk": 0}},
    })
    got = st.load("formulas")
    assert got["formula_to_page"]["eq1"] == "p1.png"
    assert got["formulas"]["eq1"]["number"] == "(1)"


def test_missing_key_filled_from_default(tmp_path):
    st = StateStore(tmp_path)
    st.dir.mkdir(parents=True)
    st.path("formulas").write_text('{"formula_to_page": {}}', encoding="utf-8")
    got = st.load("formulas")
    assert got["page_to_formulas"] == {}
    assert got["formulas"] == {}


def test_unknown_keys_preserved(tmp_path):
    st = StateStore(tmp_path)
    st.dir.mkdir(parents=True)
    st.path("progress").write_text(
        '{"units": {}, "chapters": {}, "extra": 42}', encoding="utf-8")
    got = st.load("progress")
    assert got["extra"] == 42


def test_save_creates_state_dir(tmp_path):
    st = StateStore(tmp_path)
    assert not st.dir.exists()
    st.save("scout", dict(DEFAULTS["scout"]))
    assert st.path("scout").exists()


def test_save_missing_key_raises(tmp_path):
    st = StateStore(tmp_path)
    with pytest.raises(StateError):
        st.save("formulas", {"formula_to_page": {}})


def test_invalid_json_raises(tmp_path):
    st = StateStore(tmp_path)
    st.dir.mkdir(parents=True)
    st.path("tables").write_text("{not json", encoding="utf-8")
    with pytest.raises(StateError):
        st.load("tables")


def test_non_dict_root_raises(tmp_path):
    st = StateStore(tmp_path)
    st.dir.mkdir(parents=True)
    st.path("tables").write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(StateError):
        st.load("tables")


def test_unknown_registry_name_raises(tmp_path):
    st = StateStore(tmp_path)
    with pytest.raises(StateError):
        st.load("nope")


def test_update_persists(tmp_path):
    st = StateStore(tmp_path)

    def add(d):
        d["units"]["ch01/p001-005"] = {"done": True}

    st.update("progress", add)
    assert st.load("progress")["units"]["ch01/p001-005"]["done"] is True


def test_atomic_write_json_leaves_no_tmp(tmp_path):
    p = tmp_path / "sub" / "x.json"
    atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert not p.with_suffix(".json.tmp").exists()


def test_bool_not_accepted_as_int(tmp_path):
    st = StateStore(tmp_path)
    with pytest.raises(StateError):
        st.save("scout", {
            "page_offset": True, "chapters": [], "sections": [],
            "figures": [], "tables": [], "equations": []})
