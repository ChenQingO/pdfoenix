"""Verification layer: risk-driven page sampling and global auditing."""
from pdfoenix.verify.audit import AuditHit, AuditReport, apply_hits, global_audit
from pdfoenix.verify.sampling import (
    SamplingError,
    SamplingResult,
    SamplingSettings,
    compute_page_risks,
    sample_pages,
)

__all__ = [
    "AuditHit",
    "AuditReport",
    "SamplingError",
    "SamplingResult",
    "SamplingSettings",
    "apply_hits",
    "compute_page_risks",
    "global_audit",
    "sample_pages",
]
