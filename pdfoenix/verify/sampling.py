"""Risk-distribution page sampling.

Verification is page-based because one page image can be audited completely
(prose, inline math and every display formula) for one vision cost.  The risk
score is still formula-level: each page inherits ``max`` (default) or ``sum`` of
its formulas' scores.

Calibration (2026-10-22, *An Introduction to Multivariate Statistical Analysis*)
found 1530 display formulas with median risk 28 and P90 77.  Aggregating to
pages (old batches lack page anchors, so this was an estimate) gave roughly:
44% low-risk pages, 47% medium and 9% high.  The default bands are therefore:

===========  ==============  =================
band         page risk       sampling chance
===========  ==============  =================
low          ``<= 50``       1%
medium       ``51..125``     9%
high         ``> 125``       60%
===========  ==============  =================

No normalization is applied.  Independent Bernoulli draws at these rates give
an expected sample of about 10% of pages for this distribution.  Formula-sparse
books would otherwise sample too little, so ``min_pages``/``min_rate`` define a
floor: after the risk draw, uniformly random remaining pages are added until the
floor is reached (the whole book if it is smaller than the floor).  ``budget`` is
a hard ceiling (default 20%) for the risk draw; the floor can raise that ceiling
for very small books so the absolute minimum is always respected.
"""
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from pdfoenix.core.book import Book
from pdfoenix.core.config import DEFAULT_CONFIG
from pdfoenix.core.pages import page_number

BANDS = ("low", "medium", "high")


class SamplingError(ValueError):
    """Raised when sampling settings or registries are unusable."""


@dataclass(frozen=True)
class SamplingSettings:
    """Validated sampling knobs, normally read from ``[sampling]``."""

    strategy: str = "risk_distribution"
    page_risk: str = "max"
    budget: str | int = "20%"
    low_max: int = 50
    high_min: int = 125
    low_prob: float = 0.01
    medium_prob: float = 0.09
    high_prob: float = 0.60
    min_pages: int = 10
    min_rate: float = 0.10
    seed: int = 0

    @classmethod
    def from_config(cls, config: Mapping | None = None) -> "SamplingSettings":
        """Build settings from a full configuration, falling back to defaults."""
        raw = dict(DEFAULT_CONFIG["sampling"])
        if config is not None:
            raw.update(config.get("sampling", {}))
        settings = cls(
            strategy=str(raw.get("strategy") or "risk_distribution"),
            page_risk=str(raw.get("page_risk") or "max"),
            budget=raw.get("budget", "20%"),
            low_max=int(raw.get("low_max", 50)),
            high_min=int(raw.get("high_min", 125)),
            low_prob=float(raw.get("low_prob", 0.01)),
            medium_prob=float(raw.get("medium_prob", 0.09)),
            high_prob=float(raw.get("high_prob", 0.60)),
            min_pages=int(raw.get("min_pages", 10)),
            min_rate=float(raw.get("min_rate", 0.10)),
            seed=int(raw.get("seed", 0)),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Fail fast on inverted thresholds or out-of-range probabilities."""
        if self.page_risk not in {"max", "sum"}:
            raise SamplingError("sampling.page_risk must be 'max' or 'sum'")
        if not 0 <= self.low_max < self.high_min:
            raise SamplingError("sampling.low_max must be below sampling.high_min")
        for name in ("low_prob", "medium_prob", "high_prob", "min_rate"):
            value = getattr(self, name)
            if not 0 <= value <= 1:
                raise SamplingError(f"sampling.{name} must be between 0 and 1")
        if (not isinstance(self.min_pages, int) or isinstance(self.min_pages, bool)
                or self.min_pages < 0):
            raise SamplingError("sampling.min_pages must be a non-negative integer")
        if not isinstance(self.budget, (int, str)) or isinstance(self.budget, bool):
            raise SamplingError("sampling.budget must be an integer or a percentage")
        if self.seed < 0:
            raise SamplingError("sampling.seed must be non-negative")

    def probability(self, risk: int) -> float:
        """Sampling probability for one page risk score."""
        if risk > self.high_min:
            return self.high_prob
        if risk <= self.low_max:
            return self.low_prob
        return self.medium_prob

    def band(self, risk: int) -> str:
        """Band name for one page risk score."""
        if risk > self.high_min:
            return "high"
        if risk <= self.low_max:
            return "low"
        return "medium"


@dataclass
class SamplingResult:
    """One sampling decision plus the evidence used to make it."""

    selected_pages: list[int]
    candidate_pages: list[int]
    page_risks: dict[int, int]
    bands: dict[str, list[int]] = field(default_factory=dict)
    expected_pages: float = 0.0
    target_pages: int = 0
    budget_pages: int = 0
    filled_pages: list[int] = field(default_factory=list)
    budget_trimmed: bool = False
    seed: int = 0
    strategy: str = "risk_distribution"

    @property
    def expected_rate(self) -> float:
        """Expected selected fraction of the candidate pages."""
        return self.expected_pages / len(self.candidate_pages) if self.candidate_pages else 0.0

    @property
    def band_counts(self) -> dict[str, int]:
        """Number of candidate pages per risk band."""
        return {name: len(pages) for name, pages in self.bands.items()}


class SamplingStrategy(Protocol):
    """Pluggable page selector (the default is risk-distribution sampling)."""

    def select(self, page_risks: Mapping[int, int], settings: SamplingSettings,
               rng: random.Random) -> SamplingResult:
        """Return the pages to verify."""
        ...


class RiskDistributionStrategy:
    """Independent per-page Bernoulli draws with a hard budget ceiling."""

    def select(self, page_risks: Mapping[int, int], settings: SamplingSettings,
               rng: random.Random) -> SamplingResult:
        pages = sorted(page_risks)
        bands: dict[str, list[int]] = {name: [] for name in BANDS}
        probabilities = {}
        for page in pages:
            risk = page_risks[page]
            bands[settings.band(risk)].append(page)
            probabilities[page] = settings.probability(risk)

        selected = {page for page in pages if rng.random() < probabilities[page]}
        target_pages = min(
            len(pages),
            max(settings.min_pages, math.ceil(len(pages) * settings.min_rate)),
        )
        raw_budget = _budget_pages(settings.budget, len(pages))
        budget_pages = min(len(pages), max(raw_budget, target_pages))
        trimmed = False
        if len(selected) > budget_pages:
            selected = set(rng.sample(sorted(selected), budget_pages))
            trimmed = True

        filled: list[int] = []
        if len(selected) < target_pages:
            remaining = [page for page in pages if page not in selected]
            needed = target_pages - len(selected)
            filled = rng.sample(remaining, min(needed, len(remaining)))
            selected.update(filled)

        return SamplingResult(
            selected_pages=sorted(selected),
            candidate_pages=pages,
            page_risks=dict(page_risks),
            bands=bands,
            expected_pages=sum(probabilities.values()),
            target_pages=target_pages,
            budget_pages=budget_pages,
            filled_pages=sorted(filled),
            budget_trimmed=trimmed,
            seed=settings.seed,
            strategy=settings.strategy,
        )


STRATEGIES: dict[str, SamplingStrategy] = {
    "risk_distribution": RiskDistributionStrategy(),
}


def compute_page_risks(book: Book, settings: SamplingSettings | None = None) -> dict[int, int]:
    """Aggregate formula risks to pages, including pages with no formulas.

    Candidate pages are the rendered ``working/pages/pdfpage_NNN.png`` images;
    any page mentioned only by the formula registry is also included, so a
    partially rendered book still samples its known formula pages.
    """
    settings = settings or SamplingSettings.from_config(book.config)
    registry = book.state.load("formulas")
    formula_risk = {
        formula_id: int(entry.get("risk", 0))
        for formula_id, entry in registry.get("formulas", {}).items()
    }

    risks: dict[int, int] = {}
    for image in book.pages_dir.glob("pdfpage_*.png"):
        page = page_number(image)
        if page is not None:
            risks[page] = 0
    for page_path, formula_ids in registry.get("page_to_formulas", {}).items():
        page = page_number(Path(page_path))
        if page is None:
            continue
        values = [formula_risk.get(formula_id, 0) for formula_id in formula_ids]
        if not values:
            risks.setdefault(page, 0)
            continue
        risks[page] = max(values) if settings.page_risk == "max" else sum(values)
    return dict(sorted(risks.items()))


def sample_pages(
    book: Book,
    *,
    config: Mapping | None = None,
    settings: SamplingSettings | None = None,
    strategy: str | None = None,
    rng: random.Random | None = None,
) -> SamplingResult:
    """Compute page risks and run the configured sampling strategy."""
    config = config if config is not None else book.config
    settings = settings or SamplingSettings.from_config(config)
    name = strategy or settings.strategy
    chosen = STRATEGIES.get(name)
    if chosen is None:
        raise SamplingError(f"unknown sampling strategy {name!r}")
    page_risks = compute_page_risks(book, settings)
    if not page_risks:
        raise SamplingError("no candidate page images found; run 'pdfoenix render' first")
    return chosen.select(page_risks, settings, rng or random.Random(settings.seed))


def _budget_pages(budget: str | int, total: int) -> int:
    """Resolve a budget to an absolute page count, capped at ``total``."""
    if isinstance(budget, bool):
        raise SamplingError("sampling.budget must not be a boolean")
    if isinstance(budget, int):
        return min(total, max(0, budget))
    text = str(budget).strip()
    if text.endswith("%"):
        try:
            fraction = float(text[:-1]) / 100
        except ValueError as exc:
            raise SamplingError(f"invalid sampling budget {budget!r}") from exc
        if not 0 <= fraction <= 1:
            raise SamplingError("sampling budget percentage must be between 0 and 100")
        return min(total, round(total * fraction))
    try:
        return min(total, max(0, int(text)))
    except ValueError as exc:
        raise SamplingError(f"invalid sampling budget {budget!r}") from exc
