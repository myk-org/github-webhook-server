"""Minimal self-check for the llms-full.txt fenced-block indenting."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docs_render.renderer import _indent_fenced_blocks  # noqa: E402


def test_indents_fence_body_only() -> None:
    src = "# Title\n\nprose\n\n```yaml\n# config.yaml\nkey: value\n\n  # indented comment\n```\n\nafter\n"
    expected = (
        "# Title\n\nprose\n\n```yaml\n    # config.yaml\n    key: value\n\n      # indented comment\n```\n\nafter\n"
    )
    assert _indent_fenced_blocks(src) == expected


def test_fences_still_toggle_and_pass_through() -> None:
    src = "```\nplain\n```\n```sh\necho '# not a heading'\n```\n"
    assert sum(1 for line in _indent_fenced_blocks(src).split("\n") if line.startswith("```")) == 4
    assert "    echo '# not a heading'" in _indent_fenced_blocks(src)
