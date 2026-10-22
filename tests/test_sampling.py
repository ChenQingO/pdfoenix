"""Risk-distribution sampling tests (TDD)."""
import random

import pytest

from pdfoenix.core.book import Book
from pdfoenix.verify.sampling import (
    SamplingError,
    SamplingSettings,
    compute_page_risks,
    sample_pages,
)


def make_book(tmp_path, pages, page_formulas=None, formulas=None, config=None):
    book = Book(tmp_path, config=config or {})
    book.pages_dir.mkdir(parents=True)
    for page in pages:
        (book.pages_dir / f"pdfpage_{page:03d}.png").write_bytes(b"png")
    book.state.save("formulas", {
        "formula_to_page": {},
        "page_to_formulas": page_formulas or {},
        "formulas": formulas or {},
    })
    return book


def test_page_risk_uses_max_by_default(tmp_path):
    book = make_book(
        tmp_path, [1, 2, 3],
        page_formulas={
            "working/pages/pdfpage_001.png": ["eq1", "eq2"],
            "working/pages/pdfpage_002.png": ["eq3"],
        },
        formulas={"eq1": {"risk": 10}, "eq2": {"risk": 200}, "eq3": {"risk": 60}},
    )
    assert compute_page_risks(book) == {1: 200, 2: 60, 3: 0}


def test_page_risk_can_sum(tmp_path):
    book = make_book(
        tmp_path, [1],
        page_formulas={"working/pages/pdfpage_001.png": ["eq1", "eq2"]},
        formulas={"eq1": {"risk": 10}, "eq2": {"risk": 20}},
        config={"sampling": {"page_risk": "sum"}},
    )
    assert compute_page_risks(book) == {1: 30}


def test_settings_band_and_probability():
    settings = SamplingSettings()
    assert settings.band(0) == "low"
    assert settings.band(50) == "low"
    assert settings.band(51) == "medium"
    assert settings.band(125) == "medium"
    assert settings.band(126) == "high"
    assert settings.probability(0) == 0.01
    assert settings.probability(100) == 0.09
    assert settings.probability(200) == 0.60


def test_settings_validation():
    with pytest.raises(SamplingError):
        SamplingSettings(low_max=200, high_min=100).validate()
    with pytest.raises(SamplingError):
        SamplingSettings(high_prob=1.5).validate()
    with pytest.raises(SamplingError):
        SamplingSettings(page_risk="median").validate()


def test_expected_pages_are_not_normalized(tmp_path):
    book = make_book(
        tmp_path, list(range(1, 11)),
        page_formulas={f"working/pages/pdfpage_{p:03d}.png": [f"eq{p}"]
                       for p in range(1, 11)},
        formulas={f"eq{p}": {"risk": 0 if p <= 5 else 100 if p <= 8 else 200}
                  for p in range(1, 11)},
    )
    settings = SamplingSettings(low_prob=0.1, medium_prob=0.5, high_prob=1.0,
                                budget="100%", seed=7)
    result = sample_pages(book, settings=settings, rng=random.Random(7))
    assert result.band_counts == {"low": 5, "medium": 3, "high": 2}
    assert result.expected_pages == pytest.approx(5 * 0.1 + 3 * 0.5 + 2 * 1.0)
    assert result.expected_rate == pytest.approx(0.4)
    assert result.budget_pages == 10
    assert not result.budget_trimmed


def test_budget_is_a_hard_ceiling(tmp_path):
    book = make_book(
        tmp_path, list(range(1, 11)),
        page_formulas={f"working/pages/pdfpage_{p:03d}.png": [f"eq{p}"]
                       for p in range(1, 11)},
        formulas={f"eq{p}": {"risk": 500} for p in range(1, 11)},
    )
    settings = SamplingSettings(high_prob=1.0, budget="20%", seed=1,
                                min_pages=0, min_rate=0)
    result = sample_pages(book, settings=settings, rng=random.Random(1))
    assert result.expected_pages == 10
    assert result.budget_pages == 2
    assert len(result.selected_pages) == 2
    assert result.budget_trimmed is True
    assert result.selected_pages == sorted(result.selected_pages)


def test_sparse_formula_book_is_filled_to_the_minimum(tmp_path):
    book = make_book(tmp_path, list(range(1, 101)),
                     page_formulas={}, formulas={})
    settings = SamplingSettings(low_prob=0.01, medium_prob=0.09, high_prob=0.60,
                                min_pages=10, min_rate=0.10, budget="20%", seed=3)
    result = sample_pages(book, settings=settings, rng=random.Random(3))
    assert result.target_pages == 10
    assert len(result.selected_pages) == 10
    assert len(set(result.selected_pages)) == 10
    assert result.filled_pages
    assert set(result.filled_pages) <= set(result.selected_pages)


def test_tiny_book_is_sampled_completely(tmp_path):
    book = make_book(tmp_path, list(range(1, 6)), page_formulas={}, formulas={})
    settings = SamplingSettings(min_pages=10, min_rate=0.10, budget="20%", seed=4)
    result = sample_pages(book, settings=settings, rng=random.Random(4))
    assert result.target_pages == 5
    assert result.selected_pages == [1, 2, 3, 4, 5]
    assert result.budget_pages == 5


def test_seed_makes_selection_reproducible(tmp_path):
    book = make_book(
        tmp_path, list(range(1, 51)),
        page_formulas={f"working/pages/pdfpage_{p:03d}.png": [f"eq{p}"]
                       for p in range(1, 51)},
        formulas={f"eq{p}": {"risk": 100} for p in range(1, 51)},
    )
    settings = SamplingSettings(medium_prob=0.2, budget="100%", seed=42)
    first = sample_pages(book, settings=settings)
    second = sample_pages(book, settings=settings)
    assert first.selected_pages == second.selected_pages


def test_unknown_strategy_raises(tmp_path):
    book = make_book(tmp_path, [1])
    with pytest.raises(SamplingError, match="unknown sampling strategy"):
        sample_pages(book, strategy="uniform")


def test_no_candidate_pages_raises(tmp_path):
    book = Book(tmp_path, config={"sampling": {"budget": "20%"}})
    book.pages_dir.mkdir(parents=True)
    with pytest.raises(SamplingError, match="no candidate page"):
        sample_pages(book)
