"""Plain-text and Markdown table rendering for CLI output.

One row shape is shared by every read-only view (cost reports, experiment
grids): a list of dicts plus an ordered column tuple.  ``print_table`` renders
it for the terminal, ``markdown_table`` for ``expt/report.md``.
"""
from collections.abc import Sequence


def print_table(rows: Sequence[dict], columns: Sequence[str]) -> None:
    """Print an aligned plain-text table to stdout."""
    if not rows:
        print("(no rows)")
        return
    widths = {column: max([len(str(column))]
                          + [len(_cell(row.get(column))) for row in rows])
              for column in columns}
    print("  ".join(str(column).ljust(widths[column]) for column in columns))
    print("  ".join("-" * widths[column] for column in columns))
    for row in rows:
        print("  ".join(_cell(row.get(column)).ljust(widths[column])
                        for column in columns))


def markdown_table(rows: Sequence[dict], columns: Sequence[str]) -> str:
    """Render a GitHub-flavored Markdown table (header, separator, rows)."""
    lines = ["| " + " | ".join(columns) + " |",
             "|" + "---|" * len(columns)]
    for row in rows:
        lines.append("| " + " | ".join(
            str(row.get(column, "")) for column in columns) + " |")
    return "\n".join(lines)


def _cell(value) -> str:
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value if value is not None else "")
