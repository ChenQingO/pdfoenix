"""Deterministic LaTeX/MathML hygiene for manuscripts and transcription batches.

Why this module exists
----------------------
The EPUB writer renders math with pandoc's texmath to MathML, not with KaTeX.
texmath accepts a narrower LaTeX dialect than a full TeX engine, and its failure
modes are not uniform:

* some failures become a pandoc warning and leave the raw TeX in the book;
* a formula with unbalanced braces is not even recognised as math, so pandoc
  silently emits the ``$$...$$`` block as ordinary text (no warning at all);
* a missing ``$`` may turn prose into a giant inline formula.

Relying on the builder to notice those is both late and unreliable.  This module
is the single deterministic implementation used by ``check`` (the manuscript
gate) and by batch validation (the transcription gate).  ``build`` does not call
it: building is only responsible for producing the EPUB.

Design rules
------------
* Pure functions: no book object, no state, no writes.
* A tiny allow-list of *safe* rewrites (``\\Bigm`` -> ``\\Big`` and doubled
  backslashes before a known command).  Anything that could change the
  mathematics is reported, never guessed.
* Structural checks run before texmath, so unbalanced braces get a precise
  message instead of pandoc's silent degradation.
* texmath is the final authority: every extracted span is converted in batches
  through ``pandoc -f json -t html --math-method=mathml``.  A fallback
  ``<span class="math">`` in the output means the span did not render.
"""
from __future__ import annotations

import bisect
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

#: Commands that require an argument.  A bare command at the end of a math span
#: (or immediately before ``}``) is a reliable transcription defect.
_ARGUMENT_COMMANDS = frozenset({
    "boldsymbol", "mathbf", "mathit", "mathrm", "mathbb", "mathfrak",
    "mathscr", "mathcal", "mathsf", "mathtt", "text", "operatorname",
    "hat", "widehat", "bar", "overline", "tilde", "widetilde", "vec",
    "dot", "ddot", "check", "breve", "underline", "frac", "dfrac", "tfrac",
    "sqrt", "left", "right", "middle", "big", "Big", "bigg", "Bigg",
    "bigl", "bigr", "Bigl", "Bigr", "biggl", "biggr", "Biggl", "Biggr",
})

#: Commands that texmath cannot render and that are not safe to rewrite
#: automatically.  The message keeps the fix local and actionable.
_COMMAND_HINTS = {
    "cfrac": r"use \frac",
    "hphantom": r"use \phantom",
    "vphantom": r"use \phantom",
    "mathnormal": r"remove \mathnormal",
    "displaystyle": r"remove \displaystyle (display math is already display style)",
    "textstyle": r"remove \textstyle",
    "scriptstyle": r"remove \scriptstyle",
    "scriptscriptstyle": r"remove \scriptscriptstyle",
    "over": r"use \frac",
    "atop": r"use \frac",
    "choose": r"use \binom",
    "genfrac": r"use \frac / \binom",
    "sideset": r"rewrite with \sum and explicit scripts",
    "boxed": r"remove the box or use a styled span",
    "fbox": r"remove the box or use a styled span",
    "textcolor": r"colour is not supported; remove it",
    "colorbox": r"colour is not supported; remove it",
    "rule": r"remove the rule",
    "raisebox": r"remove the raisebox",
    "smash": r"remove \smash",
    "llap": r"remove \llap",
    "rlap": r"remove \rlap",
    "mathllap": r"remove \mathllap",
    "mathrlap": r"remove \mathrlap",
    "mathclap": r"remove \mathclap",
    "underleftrightarrow": r"use \underleftrightarrow only if the renderer supports it",
}

#: Commands that are never valid in this project's dialect, with a deterministic
#: message.  ``\tag`` is intentionally absent: ``check`` already reports it as a
#: residue, and duplicating the same finding helps nobody.
_FORBIDDEN_COMMANDS = {
    "bold": r"\bold is not a LaTeX command; use \boldsymbol{...} or \mathbf{...}",
    "label": r"labels are not used; remove \label{...}",
    "eqref": r"use a Markdown cross-reference instead of \eqref{...}",
    "nonumber": r"remove \nonumber",
    "notag": r"remove \notag",
}

#: Safe, semantics-preserving rewrites.  ``\Bigm``/``\Biggm`` are relation-like
#: delimiters; texmath only knows the plain size commands.
_SAFE_RULES = (
    (re.compile(r"\\Biggm(?![A-Za-z])"), r"\\Bigg", r"\Biggm -> \Bigg"),
    (re.compile(r"\\Bigm(?![A-Za-z])"), r"\\Big", r"\Bigm -> \Big"),
    (re.compile(r"\\biggm(?![A-Za-z])"), r"\\bigg", r"\biggm -> \bigg"),
    (re.compile(r"\\bigm(?![A-Za-z])"), r"\\big", r"\bigm -> \big"),
)

#: A doubled backslash followed immediately by a known command is a typo (a row
#: break is written ``\\`` followed by whitespace or a newline, never by a
#: command name).  Restricting the rule to known commands avoids touching prose.
_DOUBLE_BACKSLASH = re.compile(
    r"\\\\(?=(?:frac|dfrac|tfrac|sqrt|sum|prod|int|iint|iiint|oint|"
    r"boldsymbol|mathbf|mathit|mathrm|mathbb|mathfrak|mathscr|mathcal|"
    r"mathsf|mathtt|text|operatorname|hat|widehat|bar|overline|tilde|"
    r"widetilde|vec|dot|ddot|check|breve|underline|left|right|middle|"
    r"begin|end|qquad|Bigg|Big|bigg|big|alpha|beta|gamma|delta|Delta|mu|nu|"
    r"Sigma|Phi|Psi|Lambda|Omega)(?![A-Za-z]))"
)

_BEGIN_END_RE = re.compile(r"\\(begin|end)\{([^}]*)\}")
_COMMAND_RE = re.compile(r"\\([A-Za-z]+)")
_CHUNK = 500


@dataclass(frozen=True)
class MathSpan:
    """One inline or display math span, with its 1-based source line."""

    line: int
    tex: str
    display: bool


@dataclass(frozen=True)
class MathIssue:
    """One deterministic math finding.  Line is 1-based."""

    line: int
    message: str


@dataclass(frozen=True)
class MathChange:
    """One safe rewrite applied by :func:`normalize`."""

    line: int
    rule: str


def _line_starts(text: str) -> list[int]:
    return [0] + [match.end() for match in re.finditer("\n", text)]


def _line_at(starts: list[int], position: int) -> int:
    return bisect.bisect_right(starts, position)


def _code_ranges(text: str) -> list[tuple[int, int]]:
    """Character ranges occupied by fenced code blocks."""
    ranges: list[tuple[int, int]] = []
    fence: str | None = None
    start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.lstrip()
        if fence is None and stripped.startswith(("```", "~~~")):
            fence = stripped[:3]
            start = offset
        elif fence is not None and stripped.startswith(fence):
            ranges.append((start, offset + len(line)))
            fence = None
        offset += len(line)
    if fence is not None:
        ranges.append((start, len(text)))
    return ranges


def normalize(text: str) -> tuple[str, list[MathChange]]:
    """Apply only safe dialect rewrites and return ``(text, changes)``.

    The rewrite is line-local, so line numbers in later findings are unchanged.
    Fenced code blocks are left untouched.
    """
    code = _code_ranges(text)
    lines = text.splitlines(keepends=True)
    changes: list[MathChange] = []
    offset = 0
    code_index = 0
    output: list[str] = []
    for number, line in enumerate(lines, 1):
        while code_index < len(code) and offset >= code[code_index][1]:
            code_index += 1
        if code_index < len(code) and code[code_index][0] <= offset < code[code_index][1]:
            output.append(line)
            offset += len(line)
            continue
        updated, count = _DOUBLE_BACKSLASH.subn(r"\\", line)
        if count:
            changes.append(MathChange(
                number, r"doubled backslash -> single backslash"))
        for pattern, replacement, rule in _SAFE_RULES:
            updated, count = pattern.subn(replacement, updated)
            if count:
                changes.append(MathChange(number, rule))
        output.append(updated)
        offset += len(line)
    return "".join(output), changes


def _brace_issue(tex: str) -> str | None:
    depth = 0
    index = 0
    while index < len(tex):
        char = tex[index]
        if char == "\\":
            index += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return "unbalanced '}': closing brace without an opener"
        index += 1
    if depth:
        return f"unbalanced '{{': {depth} brace(s) never closed"
    return None


def _dollar_issue(tex: str) -> str | None:
    """A literal ``$`` inside a math span means a delimiter was misplaced."""
    index = 0
    while index < len(tex):
        if tex[index] == "\\":
            index += 2
            continue
        if tex[index] == "$":
            return "unexpected '$' inside math; a closing '$' is probably missing"
        index += 1
    return None


def _environment_issue(tex: str) -> str | None:
    stack: list[str] = []
    for match in _BEGIN_END_RE.finditer(tex):
        kind, name = match.group(1), match.group(2)
        if kind == "begin":
            stack.append(name)
        elif not stack:
            return f"\\end{{{name}}} has no matching \\begin"
        elif stack[-1] != name:
            return (f"\\end{{{name}}} closes \\begin{{{stack[-1]}}}; "
                    f"environments are nested in the wrong order")
        else:
            stack.pop()
    if stack:
        return f"\\begin{{{stack[-1]}}} is never closed"
    return None


def _command_issues(tex: str) -> list[str]:
    issues: list[str] = []
    for name, message in _FORBIDDEN_COMMANDS.items():
        if re.search(rf"\\{name}(?![A-Za-z])", tex):
            issues.append(message)

    # A bare argument-taking command at the end of a span, or directly before a
    # closing brace, cannot be valid.
    for match in _COMMAND_RE.finditer(tex):
        name = match.group(1)
        if name not in _ARGUMENT_COMMANDS:
            continue
        tail = tex[match.end():]
        if not tail.strip() or tail.lstrip().startswith("}"):
            issues.append(f"\\{name} is missing its argument")

    if _DOUBLE_BACKSLASH.search(tex):
        issues.append(
            r"doubled backslash before a command; write \\ only as a row break")
    return issues


def _first_hint(tex: str) -> str | None:
    for match in _COMMAND_RE.finditer(tex):
        hint = _COMMAND_HINTS.get(match.group(1))
        if hint:
            return f" ({hint})"
    return None


def _scan(text: str, *, allow_incomplete: bool) -> tuple[list[MathSpan], list[MathIssue]]:
    """Extract math spans and report delimiter problems."""
    spans: list[MathSpan] = []
    issues: list[MathIssue] = []
    starts = _line_starts(text)
    code = _code_ranges(text)
    code_index = 0
    index = 0
    while index < len(text):
        while code_index < len(code) and index >= code[code_index][1]:
            code_index += 1
        if code_index < len(code) and code[code_index][0] <= index < code[code_index][1]:
            index = code[code_index][1]
            continue
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            if end == -1:
                break
            index = end + 3
            continue

        if text.startswith("$$", index):
            end = text.find("$$", index + 2)
            if end == -1:
                if not allow_incomplete:
                    issues.append(MathIssue(
                        _line_at(starts, index),
                        "unclosed display math: '$$' has no closing pair"))
                break
            spans.append(MathSpan(_line_at(starts, index),
                                  text[index + 2:end], True))
            index = end + 2
            continue

        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char != "$":
            index += 1
            continue

        # Opening inline math follows pandoc's tex_math_dollars rules: no
        # whitespace after the opening dollar and no whitespace before the
        # closing dollar.  This is why a missing '$' often remains invisible to
        # pandoc: the would-be closing dollar is rejected and the text becomes
        # a mix of raw TeX and prose.
        if index + 1 >= len(text) or text[index + 1].isspace():
            index += 1
            continue
        cursor = index + 1
        closing = -1
        while cursor < len(text):
            if text[cursor] == "\\":
                cursor += 2
                continue
            if text[cursor] == "$" and not text[cursor - 1].isspace() and (
                    cursor + 1 >= len(text) or not text[cursor + 1].isdigit()):
                closing = cursor
                break
            if text[cursor] == "\n" and text.startswith("\n\n", cursor):
                break
            cursor += 1
        if closing == -1:
            if not allow_incomplete:
                issues.append(MathIssue(
                    _line_at(starts, index),
                    "unclosed inline math: '$' has no valid closing pair"))
            index += 1
            continue
        spans.append(MathSpan(_line_at(starts, index),
                              text[index + 1:closing], False))
        index = closing + 1
    return spans, issues


def _structural_issues(span: MathSpan) -> list[MathIssue]:
    messages = []
    brace = _brace_issue(span.tex)
    if brace:
        messages.append(brace)
    dollar = _dollar_issue(span.tex)
    if dollar:
        messages.append(dollar)
    environment = _environment_issue(span.tex)
    if environment:
        messages.append(environment)
    messages.extend(_command_issues(span.tex))
    return [MathIssue(span.line, message) for message in messages]


def _texmath_failures(spans: list[MathSpan]) -> list[MathIssue]:
    """Return issues for spans that pandoc/texmath cannot convert to MathML."""
    if not spans:
        return []
    pandoc = shutil.which("pandoc")
    if pandoc is None:
        candidate = Path(sys.executable).parent / "pandoc"
        pandoc = str(candidate) if candidate.exists() else None
    if pandoc is None:
        return [MathIssue(1, "pandoc not found; cannot validate TeX math")]

    failures: list[MathIssue] = []
    for start in range(0, len(spans), _CHUNK):
        chunk = spans[start:start + _CHUNK]
        blocks = [
            {"t": "Para", "c": [{"t": "Math", "c": [
                {"t": "DisplayMath" if span.display else "InlineMath"},
                span.tex]}]}
            for span in chunk
        ]
        document = {
            "pandoc-api-version": [1, 23, 1, 2],
            "meta": {},
            "blocks": blocks,
        }
        try:
            result = subprocess.run(
                [pandoc, "-f", "json", "-t", "html", "--math-method=mathml"],
                input=json.dumps(document), capture_output=True, text=True,
                check=False)
        except OSError as exc:      # pragma: no cover - defensive
            return [MathIssue(1, f"cannot run pandoc: {exc}")]
        if result.returncode != 0:
            return [MathIssue(
                1, f"pandoc could not validate TeX math: {result.stderr.strip()}")]
        paragraphs = re.findall(r"<p>(.*?)</p>", result.stdout, re.DOTALL)
        if len(paragraphs) != len(chunk):
            return [MathIssue(
                1, "pandoc returned an unexpected math-validation result")]
        for span, paragraph in zip(chunk, paragraphs, strict=False):
            if 'class="math' in paragraph:
                hint = _first_hint(span.tex) or ""
                failures.append(MathIssue(
                    span.line,
                    "texmath cannot render this formula as MathML"
                    f"{hint}"))
    return failures


def lint(text: str, *, allow_incomplete: bool = False) -> list[MathIssue]:
    """Return every math issue in ``text``.

    ``allow_incomplete`` is used for a single transcription batch: a display
    block may legitimately continue into the next batch, so an open delimiter at
    EOF is not an error there.  Whole-chapter and whole-manuscript checks use the
    default (``False``).
    """
    spans, issues = _scan(text, allow_incomplete=allow_incomplete)
    checked: list[MathSpan] = []
    for span in spans:
        structural = _structural_issues(span)
        if structural:
            issues.extend(structural)
        else:
            checked.append(span)
    issues.extend(_texmath_failures(checked))
    return issues


def lint_file(path: Path, *, allow_incomplete: bool = False) -> list[MathIssue]:
    """Convenience wrapper for a single file."""
    return lint(path.read_text(encoding="utf-8"),
                allow_incomplete=allow_incomplete)
