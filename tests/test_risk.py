"""Complexity risk-score tests (TDD)."""
import pytest

from pdfoenix.core.risk import DEFAULT_WEIGHTS, risk_score


@pytest.mark.parametrize("latex, expected", [
    # empty / plain text
    ("", 0),
    ("x + y", 0),
    ("\\alpha", 0),
    # subscripts / superscripts (2 points each)
    ("x^2", 2),
    ("x_i", 2),
    ("x_i^2", 4),
    ("x_i^2 + y_j^3", 8),
    # fractions / roots (3 points each)
    ("\\frac{a}{b}", 3),
    ("\\dfrac{a}{b}", 3),
    ("\\tfrac{a}{b}", 3),
    ("\\sqrt{x}", 3),
    ("\\sqrt[3]{x}", 3),
    # accents (3 points each)
    ("\\hat{x}", 3),
    ("\\bar{x}", 3),
    ("\\tilde{x}", 3),
    ("\\widehat{x}", 3),
    ("\\overline{x}", 3),
    ("\\vec{x}", 3),
    ("\\dot{x}", 3),
    # bold / script fonts (2 points each)
    ("\\boldsymbol{x}", 2),
    ("\\mathbf{x}", 2),
    ("\\bm{x}", 2),
    ("\\mathscr{E}", 2),
    ("\\mathcal{Q}", 2),
    ("\\mathbb{R}", 2),
    # matrix / array environments (5 points each)
    ("\\begin{matrix}a\\\\b\\end{matrix}", 5),
    ("\\begin{pmatrix}a\\\\b\\end{pmatrix}", 5),
    ("\\begin{bmatrix}a\\\\b\\end{bmatrix}", 5),
    ("\\begin{array}{cc}a&b\\end{array}", 5),
    # big operators (2 points each)
    ("\\sum", 2),
    ("\\prod", 2),
    ("\\int", 2),
    ("\\oint", 2),
    # combinations
    ("\\sum_{i=1}^n x_i", 2 + 3 * 2),          # two "_", one "^" -> 6; plus bigop 2
    ("\\hat{\\boldsymbol{\\beta}}_{ij}", 3 + 2 + 2),
    (r"\hat{\boldsymbol{\Sigma}}_{ii}^{\frac{1}{2}N}", 3 + 2 + 4 + 3),
    # escaped underscore is not counted
    (r"a\_b", 0),
])
def test_risk_score(latex, expected):
    assert risk_score(latex) == expected


def test_default_weights_present():
    assert set(DEFAULT_WEIGHTS) == {"sub_sup", "frac", "hat", "bold", "matrix", "bigop"}


def test_custom_weights_override():
    assert risk_score("x^2", {"sub_sup": 10}) == 10
    assert risk_score("\\frac{a}{b}", {"frac": 0}) == 0


def test_unknown_weight_keys_ignored():
    assert risk_score("x^2", {"nope": 999}) == 2


def test_weights_do_not_mutate_defaults():
    before = dict(DEFAULT_WEIGHTS)
    risk_score("x^2", {"sub_sup": 99})
    assert before == DEFAULT_WEIGHTS


def test_multiplicity():
    # three fractions, 3 points each
    assert risk_score("\\frac{a}{b}\\frac{c}{d}\\frac{e}{f}") == 9
