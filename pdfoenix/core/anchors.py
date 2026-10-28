"""Deterministic anchor generation and dangling-link repair for the manuscript.

The transcription model is asked to emit cross-references as Markdown links, but
it is unreliable at emitting the matching targets.  After merge the book is full
of links such as ``#thm-8-10-3`` or ``#ref-wilks-1935`` while the theorem
statements and bibliography entries carry no id at all.  Some pages also contain
model-written inline anchors (``<span id="ref-davis-1972a"></span>``) that
``check`` historically ignored because it only understood heading attributes.

This module closes both gaps without a model call:

* theorem-like statements get ``<span id="thm-8-10-3"></span>`` (etc.);
* figure images get ``<span id="fig-8-2"></span>``;
* bibliography entries get ``<span id="ref-author-year"></span>``, preferring an
  id the entry already links to itself;
* dangling links are rewritten to a case-insensitive, aliased
  (``lemma-`` -> ``lem-``), parent-section or fuzzy bibliography target.

Everything is idempotent: re-running merge regenerates the same anchors from the
batches, and links that already resolve are left untouched.
"""
import difflib
import re
import unicodedata
from dataclasses import dataclass

HEADING_ID_RE = re.compile(r"\{#([A-Za-z0-9_-]+)")
SPAN_ID_RE = re.compile(r"<span\s+id=[\"']([A-Za-z0-9_-]+)[\"']")
LINK_RE = re.compile(r"\]\(#([A-Za-z0-9_-]+)\)")

# `**Theorem 8.10.3.**`, `**Lemma A.2.4**`, `**Corollary 13.3.2.**`
STATEMENT_RE = re.compile(
    r"^\*\*(Theorem|Lemma|Corollary|Definition|Proposition|Example)\s+"
    r"([A-Za-z0-9]+(?:\.[0-9]+)+?)\.?\*\*", re.MULTILINE)

# `![Figure 8.2. ...](figures/fig8-2.png)`
FIGURE_RE = re.compile(r"^!\[[^\]]*]\(figures/(fig[0-9]+-[0-9]+)\.png\)", re.MULTILINE)

REF_LINK_RE = re.compile(
    r"^\*{0,2}\[([^\]]{3,240})\]\(#((?:ref|thm|lem|cor|def|sec|fig)-[A-Za-z0-9_-]+)\)")

KIND_PREFIX = {
    "theorem": "thm",
    "lemma": "lem",
    "corollary": "cor",
    "definition": "def",
    "proposition": "prop",
    "example": "ex",
}

# NFKD does not decompose these; without the map "Reiersøl" never matches
# ``ref-reiersol-1950``.
_TRANSLITERATE = str.maketrans({
    "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae", "ß": "ss",
    "ł": "l", "Ł": "l", "đ": "d", "ð": "d", "þ": "th",
})


@dataclass(frozen=True)
class ReferenceEntry:
    """One bibliography entry and the identity links may target."""

    id: str
    year: str
    surnames: tuple[str, ...]
    blob: str


def slug(text: str) -> str:
    """Lowercase ASCII slug used in anchor ids."""
    text = str(text).translate(_TRANSLITERATE)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def anchor_ids(text: str) -> set[str]:
    """Every anchor id in ``text`` (heading attributes and inline spans)."""
    return set(HEADING_ID_RE.findall(text)) | set(SPAN_ID_RE.findall(text))


def add_anchors(text: str) -> tuple[str, list[ReferenceEntry]]:
    """Add statement/figure/bibliography anchors to one section's Markdown."""
    entries: list[ReferenceEntry] = []
    text = _add_statement_anchors(text)
    text = _add_figure_anchors(text)
    if re.search(r"^# References\b", text, re.MULTILINE):
        text, entries = _add_reference_anchors(text)
    return text, entries


def _add_statement_anchors(text: str) -> str:
    present = anchor_ids(text)

    def repl(match: re.Match) -> str:
        kind = KIND_PREFIX[match.group(1).lower()]
        number = match.group(2).lower().replace(".", "-")
        anchor = f"{kind}-{number}"
        if anchor in present:
            return match.group(0)
        present.add(anchor)
        return f'<span id="{anchor}"></span>{match.group(0)}'

    return STATEMENT_RE.sub(repl, text)


def _add_figure_anchors(text: str) -> str:
    present = anchor_ids(text)

    def repl(match: re.Match) -> str:
        anchor = "fig-" + match.group(1).removeprefix("fig")
        if anchor in present:
            return match.group(0)
        present.add(anchor)
        return f'<span id="{anchor}"></span>\n{match.group(0)}'

    return FIGURE_RE.sub(repl, text)


def parse_reference_entries(text: str) -> list[ReferenceEntry]:
    """Parse bibliography entries without modifying the text."""
    entries: list[ReferenceEntry] = []
    in_references = False
    for line in text.splitlines():
        if re.match(r"^# References\b", line):
            in_references = True
            continue
        if in_references and re.match(r"^# ", line):
            break
        if not in_references or not line.strip():
            continue
        existing = SPAN_ID_RE.search(line)
        stripped = _plain(line)
        entry = _parse_reference(stripped, existing.group(1) if existing else None)
        if entry is not None:
            entries.append(entry)
    return entries


def _add_reference_anchors(text: str) -> tuple[str, list[ReferenceEntry]]:
    """Insert one span per bibliography entry and return their identities."""
    lines = text.splitlines()
    entries: list[ReferenceEntry] = []
    used = anchor_ids(text)
    in_references = False
    for index, line in enumerate(lines):
        if re.match(r"^# References\b", line):
            in_references = True
            continue
        if in_references and re.match(r"^# ", line):
            break
        if not in_references or not line.strip():
            continue
        existing = SPAN_ID_RE.search(line)
        stripped = _plain(line)
        entry = _parse_reference(stripped, existing.group(1) if existing else None)
        if entry is None:
            continue
        entries.append(entry)
        if existing is not None:
            continue
        anchor = entry.id
        if anchor in used:
            # A repeated author/year: keep the link target unique but stable.
            suffix = 2
            while f"{entry.id}-{suffix}" in used:
                suffix += 1
            anchor = f"{entry.id}-{suffix}"
            used.add(anchor)
        else:
            used.add(anchor)
        lines[index] = f'<span id="{anchor}"></span>{stripped}'
    return "\n".join(lines), entries


def _plain(line: str) -> str:
    """Drop inline HTML (spans written by the model or by earlier merges)."""
    return re.sub(r"<[^>]+>", "", line).strip()


def _parse_reference(line: str, existing_id: str | None) -> ReferenceEntry | None:
    """Parse ``Author, A. (1977), Title`` into a stable reference identity."""
    line = re.sub(r"<[^>]+>", "", line).strip()
    linked = REF_LINK_RE.match(line)
    link_id = linked.group(2) if linked and linked.group(2).startswith("ref-") else None
    inner = linked.group(1) if linked else line
    # Three-digit years cover damaged scans ("(193.)") that are still useful
    # as identities because the entry carries its own `#ref-...` link.
    match = re.search(r"\((1[6-9]\d\d|20\d\d|\d{3})([a-z]?)[^)]*\)", inner)
    if match is None:
        return None
    year = match.group(1) + match.group(2)
    authors = inner[:match.start()]
    first = re.sub(r"^[^A-Za-z]+", "", authors.split(",", 1)[0]).strip()
    surname = slug(first)
    extras = []
    for segment in re.split(r"\band\b", authors)[1:]:
        words = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ'\u2019-]+", segment)
        if words:
            extras.append(slug(words[-1]))
    surnames = tuple(item for item in [surname, *extras] if item)
    if not surnames and not (existing_id or link_id):
        return None
    anchor = existing_id or link_id or f"ref-{surnames[0]}-{year}"
    if not anchor.startswith("ref-"):
        anchor = f"ref-{anchor}"
    return ReferenceEntry(id=anchor, year=year, surnames=surnames,
                          blob=slug(authors))


def resolve_links(text: str, ids: set[str],
                  entries: list[ReferenceEntry]) -> tuple[str, int]:
    """Rewrite dangling links to a target that exists; return hits fixed."""
    lower = {anchor.lower(): anchor for anchor in ids}
    fixed = 0

    def repl(match: re.Match) -> str:
        nonlocal fixed
        target = match.group(1)
        if target in ids:
            return match.group(0)
        replacement = _resolve_target(target, lower, entries)
        if replacement is None:
            return match.group(0)
        fixed += 1
        return f"](#{replacement})"

    return LINK_RE.sub(repl, text), fixed


def suggest_target(target: str, ids: set[str],
                   entries: list[ReferenceEntry]) -> str | None:
    """Public wrapper used by diagnostics: what would ``resolve_links`` pick?"""
    lower = {anchor.lower(): anchor for anchor in ids}
    return _resolve_target(target, lower, entries)


def _resolve_target(target: str, lower: dict[str, str],
                    entries: list[ReferenceEntry]) -> str | None:
    candidate = lower.get(target.lower())
    if candidate is not None:
        return candidate

    if target.startswith("lemma-"):
        candidate = lower.get(("lem-" + target[len("lemma-"):]).lower())
        if candidate is not None:
            return candidate

    if target.startswith("sec-"):
        if target.endswith("-p"):
            candidate = lower.get((target[:-2] + "-problems").lower())
            if candidate is not None:
                return candidate
        parts = target.split("-")
        for cut in range(len(parts) - 1, 1, -1):
            candidate = lower.get("-".join(parts[:cut]).lower())
            if candidate is not None:
                return candidate

    if target.startswith("ref-"):
        entry = match_reference(target, entries)
        if entry is not None:
            return entry.id
    return None


def match_reference(key: str, entries: list[ReferenceEntry]) -> ReferenceEntry | None:
    """Fuzzy-match a dangling ``ref-*`` key against bibliography entries.

    Keys are often typos or truncations of the printed entry
    (``ref-elfvig-1947`` vs ``Elfving 1947``, ``ref-thomson-193`` for a damaged
    year).  Surname matching is fuzzy but only considered for entries whose year
    is compatible, so a wrong jump is very unlikely.
    """
    if not key.startswith("ref-"):
        return None
    parts = key[len("ref-"):].split("-")
    year = None
    full_year = False
    if parts and re.fullmatch(r"(1[6-9]\d\d|20\d\d)[a-z]?", parts[-1]):
        year = parts.pop()
        full_year = True
    elif parts and re.fullmatch(r"\d{3}", parts[-1]):
        year = parts.pop()
    tokens = [token for token in parts if token]
    if not tokens:
        return None

    exact = next((entry for entry in entries if entry.id == key), None)
    if exact is not None:
        return exact

    best = None
    best_score = 0.0
    for entry in entries:
        if not _year_compatible(year, full_year, entry.year):
            continue
        score = _name_score(tokens, entry)
        if score > best_score:
            best, best_score = entry, score
    return best if best_score >= 0.84 else None


def reference_suggestions(key: str, entries: list[ReferenceEntry],
                          limit: int = 3) -> list[ReferenceEntry]:
    """Best bibliography candidates for a ref link, ignoring year mismatch."""
    if not key.startswith("ref-"):
        return []
    parts = key[len("ref-"):].split("-")
    if parts and (re.fullmatch(r"(1[6-9]\d\d|20\d\d)[a-z]?", parts[-1])
                  or re.fullmatch(r"\d{3}", parts[-1])):
        parts.pop()
    tokens = [token for token in parts if token]
    if not tokens:
        return []
    scored = [(_name_score(tokens, entry), entry) for entry in entries]
    scored.sort(key=lambda item: (-item[0], item[1].id))
    return [entry for score, entry in scored[:limit] if score >= 0.7]


def _year_compatible(year: str | None, full: bool, entry_year: str) -> bool:
    if year is None:
        return True
    if full:
        if year == entry_year or year[:4] == entry_year[:4]:
            return True
        # A damaged printed year such as "(193.)" becomes the key `thomson-1934`.
        return len(entry_year) < 4 and year.startswith(entry_year)
    return entry_year.startswith(year)


def _name_score(tokens: list[str], entry: ReferenceEntry) -> float:
    names = [entry.surnames[0]] if entry.surnames else []
    names.append(entry.blob)
    variants = []
    for name in names:
        variants.extend([name, name.replace("-", "")])
    score = 0.0
    for token in tokens:
        token_score = 0.0
        for name in variants:
            if token in name:
                token_score = max(token_score, 1.0)
            else:
                token_score = max(token_score,
                                  difflib.SequenceMatcher(None, token, name).ratio())
            if token_score >= 0.98:
                break
        score += token_score
    return score / len(tokens)
