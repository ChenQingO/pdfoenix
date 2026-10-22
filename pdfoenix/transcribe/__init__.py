"""Model layer: the only package that talks to an LLM."""
from pdfoenix.transcribe.opencode import OpenCodeTranscriber
from pdfoenix.transcribe.protocol import Correction, Transcriber, Verifier

__all__ = ["Correction", "OpenCodeTranscriber", "Transcriber", "Verifier"]
