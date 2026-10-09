"""Result table rendering (markdown) for reports and the results chapter.

Every table is also persisted beside the joblib metrics, so the numbers in
Chapter 4 can be copied from a file the pipeline wrote - not retyped.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd


def render_markdown_table(
    df: pd.DataFrame,
    float_format: str = ".3f",
    index: bool = False,
) -> str:
    """Render a DataFrame as a GitHub-flavoured markdown table."""
    return df.to_markdown(index=index, floatfmt=float_format)


def save_table(
    df: pd.DataFrame,
    path: str | Path,
    float_format: str = ".3f",
) -> Path:
    """Write a markdown table file and return its path."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_table(df, float_format=float_format) + "\n")
    return path
