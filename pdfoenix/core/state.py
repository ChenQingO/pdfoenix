"""JSON registries under ``state/``: atomic reads/writes with light schema checks.

There are six registries (scout, formulas, figures, tables, corrections,
progress).  They are plain text so that lookups are cheap, diffable and
greppable.  Writes go through a temp file followed by ``os.replace`` so an
interrupted run never leaves a half-written JSON file behind.
"""
import copy
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

STATE_DIRNAME = "state"


class StateError(ValueError):
    """Raised when a registry file is missing required data or is malformed."""


# Default (empty) structure for each registry.  ``load`` fills any missing keys
# from here, which keeps old registry files forward-compatible.
DEFAULTS = {
    "scout": {
        "page_offset": 0,
        "chapters": [],
        "sections": [],
        "figures": [],
        "tables": [],
        "equations": [],
    },
    "formulas": {
        "formula_to_page": {},   # formula id -> page image path (relative to book)
        "page_to_formulas": {},  # page image path -> [formula id, ...]
        "formulas": {},          # formula id -> {number, latex, risk, section}
    },
    "figures": {"figures": {}},      # figure id -> {file, caption, description, page}
    "tables": {"tables": {}},        # table id -> {caption, md_file, page}
    "corrections": {"corrections": []},
    "progress": {"units": {}, "chapters": {}},
}

# name -> {key: expected Python type}
_SCHEMA = {
    "scout": {
        "page_offset": int,
        "chapters": list,
        "sections": list,
        "figures": list,
        "tables": list,
        "equations": list,
    },
    "formulas": {
        "formula_to_page": dict,
        "page_to_formulas": dict,
        "formulas": dict,
    },
    "figures": {"figures": dict},
    "tables": {"tables": dict},
    "corrections": {"corrections": list},
    "progress": {"units": dict, "chapters": dict},
}


def _type_ok(value, expected_type) -> bool:
    # bool is a subclass of int in Python; reject it where an int is expected.
    if expected_type is int and isinstance(value, bool):
        return False
    return isinstance(value, expected_type)


def validate(name: str, data: Mapping[str, Any]) -> None:
    """Validate that ``data`` has every required key with the expected type."""
    if name not in _SCHEMA:
        raise StateError(f"unknown registry {name!r}")
    if not isinstance(data, dict):
        raise StateError(f"{name} root must be a dict")
    for key, expected_type in _SCHEMA[name].items():
        if key not in data:
            raise StateError(f"{name} is missing key {key!r}")
        if not _type_ok(data[key], expected_type):
            raise StateError(
                f"{name}.{key} should be {expected_type.__name__}")


def _fill_defaults(data: dict, default: dict) -> dict:
    """Recursively add missing keys from ``default`` (in place)."""
    for key, value in default.items():
        if key not in data:
            data[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(data[key], dict):
            _fill_defaults(data[key], value)
    return data


def atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    """Write JSON to ``path`` atomically (temp file + ``os.replace``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


class StateStore:
    """Read/write the registries of a single book (``<book>/state/*.json``)."""

    def __init__(self, book_root):
        self.book_root = Path(book_root)
        self.dir = self.book_root / STATE_DIRNAME

    def path(self, name: str) -> Path:
        """Absolute path of a registry file (whether it exists or not)."""
        return self.dir / f"{name}.json"

    def load(self, name: str) -> dict:
        """Load a registry, filling missing keys from the defaults."""
        if name not in DEFAULTS:
            raise StateError(f"unknown registry {name!r}")
        path = self.path(name)
        if not path.exists():
            return copy.deepcopy(DEFAULTS[name])
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise StateError(f"failed to parse {name}.json: {exc}") from exc
        if not isinstance(data, dict):
            raise StateError(f"{name}.json root must be a dict")
        _fill_defaults(data, DEFAULTS[name])
        validate(name, data)
        return data

    def save(self, name: str, data: Mapping[str, Any]) -> None:
        """Validate and atomically write a registry."""
        validate(name, data)
        atomic_write_json(self.path(name), data)

    def update(self, name: str, mutate) -> dict:
        """Load a registry, let ``mutate(data)`` change it, then save it."""
        data = self.load(name)
        mutate(data)
        self.save(name, data)
        return data
