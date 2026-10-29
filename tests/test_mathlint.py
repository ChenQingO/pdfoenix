"""Math extraction, safe normalization and texmath validation tests."""
from pdfoenix.core import mathlint


def messages(text, **kwargs):
    return [issue.message for issue in mathlint.lint(text, **kwargs)]


def test_normalize_safe_dialect_and_double_backslash():
    text = r"$\Biggm| x$ and $\Bigm| y$ and $\Gamma(\\frac{1}{2}p)$"
    fixed, changes = mathlint.normalize(text)
    assert r"\Bigg|" in fixed
    assert r"\Big|" in fixed
    assert r"\frac{1}{2}" in fixed
    assert len(changes) == 3
    assert mathlint.normalize(fixed)[1] == []


def test_normalize_leaves_code_fences_alone():
    text = "```\n$\\Bigm|$\n```\n"
    fixed, changes = mathlint.normalize(text)
    assert fixed == text
    assert changes == []


def test_valid_aligned_formula_has_no_issue():
    text = ("$$\n"
            "\\begin{aligned}\n"
            "a &= b \\\\\n"
            "  &= c\n"
            "\\end{aligned}\n"
            "\\qquad (1)\n"
            "$$\n")
    assert mathlint.lint(text) == []


def test_bare_boldsymbol_is_reported():
    found = messages(r"value $\boldsymbol$\Phi$")
    assert any("boldsymbol" in message and "argument" in message
               for message in found)


def test_bold_is_reported():
    found = messages(r"$\bold...$")
    assert any(r"\bold" in message for message in found)


def test_unbalanced_braces_are_reported():
    found = messages("$$\nx{y{z}\n$$")
    assert any("unbalanced" in message for message in found)


def test_missing_closing_dollar_is_reported():
    found = messages(r"where $\cos\theta_jis the $j$th")
    assert any("'" in message and "missing" in message for message in found)


def test_unsupported_command_is_reported_with_hint():
    found = messages(r"$\Bigm| \pi$")
    assert any("texmath" in message for message in found)


def test_batch_validation_can_allow_an_open_display():
    text = "$$\nx = 1\n"
    assert mathlint.lint(text, allow_incomplete=True) == []
    assert mathlint.lint(text) != []


def test_escaped_dollar_is_not_math():
    assert mathlint.lint(r"costs \$5 and no math") == []


def test_html_comment_is_not_linted_as_math():
    assert mathlint.lint(r"<!-- uncertain: printed as $x$; transcribed as printed -->") == []
