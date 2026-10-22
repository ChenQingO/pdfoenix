"""Complexity "risk score" for a LaTeX formula (pure string analysis, no model).

Purpose
-------
The verification stage samples pages, and the sampling probability must favour
pages that are likely to contain transcription errors.  Vision transcription of
mathematics fails most often around a small set of constructs (subscripts,
superscripts, fractions, hats/bars, bold/script fonts, matrices, big operators).
This module quantifies those constructs into a single integer "risk" value.

The score is intentionally a heuristic, not a measure of mathematical
difficulty.  Its per-construct weights live in ``DEFAULT_WEIGHTS`` and can be
overridden per book (``config.toml [sampling].risk_weights``) so that the
sampling policy can be calibrated empirically without changing any code.
"""
import re

# Default weights.  Keys are stable identifiers used by the config file.
DEFAULT_WEIGHTS = {
    "sub_sup": 2,   # each subscript "_" or superscript "^" character
    "frac": 3,      # each \frac / \dfrac / \tfrac / \sqrt
    "hat": 3,       # each accent command (\hat, \bar, \tilde, ...)
    "bold": 2,      # each font-switch command (\boldsymbol, \mathbf, \mathscr, ...)
    "matrix": 5,    # each matrix / array environment
    "bigop": 2,     # each big operator (\sum, \prod, \int, ...)
}

# A LaTeX command name ends at the first non-letter character.  We must NOT use
# "\b" for the right boundary: "_" is a word character in Python's regex engine,
# so "\sum_" would fail a trailing "\b" even though the command clearly ends.
_END = r"(?![A-Za-z])"

# Command families counted per occurrence (subscripts/superscripts are handled
# separately below because they are single characters, not commands).
_PATTERNS = {
    "frac": re.compile(r"\\(?:[dt]?frac|sqrt)" + _END),
    "hat": re.compile(
        r"\\(?:hat|widehat|bar|overline|tilde|widetilde|vec|dot|ddot|check|breve)"
        + _END),
    "bold": re.compile(
        r"\\(?:boldsymbol|mathbf|bm|mathscr|mathcal|mathbb|mathsf|mathtt)"
        + _END),
    "matrix": re.compile(r"\\begin\{(?:array|[pbBvV]?matrix)\}"),
    "bigop": re.compile(
        r"\\(?:sum|prod|coprod|int|iint|iiint|oint|bigcup|bigcap|bigoplus|bigotimes)"
        + _END),
}

# "_" or "^", but not an escaped underscore "\_".
_SUBSUP = re.compile(r"(?<!\\)[_^]")


def risk_score(latex: str, weights: dict | None = None) -> int:
    """Return the integer risk score for a LaTeX fragment.

    ``weights`` may override any subset of ``DEFAULT_WEIGHTS``; unknown keys are
    ignored so that a newer config stays forward-compatible with older code.
    The default weights dict is never mutated.
    """
    w = {**DEFAULT_WEIGHTS, **(weights or {})}
    score = len(_SUBSUP.findall(latex)) * w["sub_sup"]
    for key, pattern in _PATTERNS.items():
        score += len(pattern.findall(latex)) * w[key]
    return score
