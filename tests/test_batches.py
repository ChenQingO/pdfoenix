"""Batch integrity validator tests."""
from pdfoenix.core.batches import validate_batch_text, validate_stream


def kinds(issues, severity="error"):
    return [issue.kind for issue in issues if issue.severity == severity]


def test_valid_batch_passes():
    text = ("<!-- page:1 -->\n\n$$\nx = 1\n$$\n\n"
            "<!-- page:2 -->\n\nSome prose.\n")
    assert validate_batch_text(text, 1, 2) == []


def test_missing_page_anchor_is_an_error():
    text = "<!-- page:1 -->\n\nOnly page one.\n"
    assert "anchor-missing" in kinds(validate_batch_text(text, 1, 2))


def test_inline_and_duplicate_anchors_are_errors():
    text = ("<!-- page:1 -->\ntext <!-- page:1 -->\n"
            "<!-- page:2 -->\n<!-- page:2 -->\n")
    found = kinds(validate_batch_text(text, 1, 2))
    assert "anchor-inline" in found
    assert "anchor-duplicate" in found


def test_restarted_prose_block_is_an_error():
    paragraph = ("This is a long prose paragraph that the model repeated after "
                 "a restart, with enough words to be recognised as prose.")
    text = (f"<!-- page:1 -->\n\n{paragraph}\n\n"
            f"text between the two copies\n\n{paragraph}\n\n"
            f"<!-- page:2 -->\n\nend\n")
    assert {"duplicate-block", "duplicate-line"} & set(
        kinds(validate_batch_text(text, 1, 2)))


def test_repeated_matrix_rows_are_not_a_restart():
    rows = "\n".join([r"\boldsymbol{A}_{11} & \cdots & \boldsymbol{A}_{1i} \\"] * 8)
    text = (f"<!-- page:1 -->\n\n$$\n\\begin{{pmatrix}}\n{rows}\n\\end{{pmatrix}}\n$$\n\n"
            f"text\n\n$$\n\\begin{{pmatrix}}\n{rows}\n\\end{{pmatrix}}\n$$\n\n"
            f"<!-- page:2 -->\n\nend\n")
    assert "duplicate-block" not in kinds(validate_batch_text(text, 1, 2))


def test_stream_checks_math_and_environments():
    issues = validate_stream(["<!-- page:1 -->\n\n$$\nx = 1\n\n"
                              "$$\\begin{aligned}x\\end{aligned}$$$"])
    assert "math" in kinds(issues)
