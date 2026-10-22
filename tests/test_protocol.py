"""Model-layer protocol and correction-record tests (TDD)."""
import pytest

from pdfoenix.transcribe.protocol import Correction, Transcriber, Verifier


class DummyModel:
    def transcribe(self, _images, _task):
        return "text"

    def verify(self, _images, _content):
        return []


def test_dummy_satisfies_protocols():
    model = DummyModel()
    assert isinstance(model, Verifier)
    assert isinstance(model, Transcriber)


def test_correction_defaults():
    correction = Correction(unit_id="p1")
    assert correction.formula_id is None
    assert correction.severity == "style"
    assert correction.pattern == ""
    assert correction.source == ""
    assert correction.note == ""


def test_correction_rejects_unknown_severity():
    with pytest.raises(ValueError):
        Correction(unit_id="p1", severity="major")


def test_from_mapping_normalizes_fields():
    correction = Correction.from_mapping(
        {
            "unit_id": "p7",
            "formula_id": "eq7-1-1",
            "severity": "introduced",
            "pattern": "sed→seb",
            "source": "ocr",
            "detail": "line 3",
        },
        default_unit_id="fallback")
    assert correction.unit_id == "p7"
    assert correction.formula_id == "eq7-1-1"
    assert correction.note == "line 3"


def test_from_mapping_uses_default_unit_and_optional_nulls():
    correction = Correction.from_mapping(
        {"formula_id": None, "severity": "faithful"},
        default_unit_id="p9")
    assert correction.unit_id == "p9"
    assert correction.formula_id is None
    assert correction.severity == "faithful"


def test_from_mapping_rejects_non_mapping():
    with pytest.raises(ValueError):
        Correction.from_mapping(["not", "an", "object"])
