"""Pluggable model-layer protocols and the structured correction record.

The deterministic core never imports a model SDK.  It talks to these protocols
only, so a user can replace OpenCode with another implementation without
touching the pipeline.  The two capabilities mirror the two places where an LLM
is allowed (see ARCHITECTURE §8):

* ``transcribe`` turns page images plus a task book into Markdown,
* ``verify`` audits that Markdown against the same images and returns a
  structured list of corrections.

A correction is deliberately small and normalized: severity and source are
controlled vocabularies so the ledger can group and join them, while ``pattern``
uses the ``X→Y`` shape consumed by the global-audit grep pass.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

#: Allowed values for :attr:`Correction.severity`.
SEVERITIES = ("introduced", "faithful", "style")


@dataclass(frozen=True)
class Correction:
    """One discrepancy found while auditing a transcribed unit.

    ``introduced`` means the transcription deviates from the scan (the LLM made
    an error).  ``faithful`` means the scan itself is wrong or ambiguous but the
    transcription reproduces it correctly.  ``style`` covers presentational
    issues that do not change content.
    """

    unit_id: str
    formula_id: str | None = None
    severity: str = "style"
    pattern: str = ""
    source: str = ""
    note: str = ""
    call_id: str = ""

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(
                f"invalid severity {self.severity!r}; expected one of {SEVERITIES}")

    @classmethod
    def from_mapping(cls, data: Mapping, *, default_unit_id: str = "",
                     default_call_id: str = "") -> "Correction":
        """Build a correction from a decoded JSON object.

        Missing optional fields fall back to conservative defaults, but an
        unknown severity is rejected instead of silently reclassified: it means
        the verifier broke the output contract and the caller should retry.
        """
        if not isinstance(data, Mapping):
            raise ValueError(f"correction entry must be an object, got {type(data).__name__}")

        unit_id = str(data.get("unit_id") or default_unit_id)
        formula_id = data.get("formula_id")
        if formula_id is not None:
            formula_id = str(formula_id) or None
        severity = str(data.get("severity") or "style")
        pattern = str(data.get("pattern") or "")
        source = str(data.get("source") or "")
        note = str(data.get("note") or data.get("comment") or data.get("detail") or "")
        call_id = str(data.get("call_id") or default_call_id)
        return cls(
            unit_id=unit_id,
            formula_id=formula_id,
            severity=severity,
            pattern=pattern,
            source=source,
            note=note,
            call_id=call_id,
        )


@runtime_checkable
class Verifier(Protocol):
    """Audit transcribed content against the source page images."""

    def verify(self, images: list[Path], content: str) -> list[Correction]:
        """Return every correction found in ``content`` for ``images``."""
        ...


@runtime_checkable
class Transcriber(Verifier, Protocol):
    """Turn page images into Markdown and audit the result when asked."""

    def transcribe(self, images: list[Path], task: str) -> str:
        """Return the Markdown transcription of ``images`` under ``task``."""
        ...
