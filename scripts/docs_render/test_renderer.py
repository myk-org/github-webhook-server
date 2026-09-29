"""Self-checks for the vendored renderer: llms-full indenting and HTML sanitizing."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock

import pytest

from docs_render import renderer
from docs_render.renderer import (
    _clean_code_fence_annotations,
    _ensure_blank_lines,
    _indent_fenced_blocks,
    _sanitize_html,
)


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


def test_cleans_script_with_variant_closing_tag() -> None:
    html = '<script src="https://attacker.example/x.js"></script >\n<p>body</p>'
    assert _sanitize_html(html) == "\n<p>body</p>"


@pytest.mark.parametrize("tag", ["iframe", "object", "embed", "form"])
def test_cleans_unclosed_dangerous_tag_with_encoded_srcdoc(tag: str) -> None:
    html = f'<{tag} srcdoc="&lt;script&gt;alert(1)&lt;/script&gt;">'
    cleaned = _sanitize_html(html)
    assert f"<{tag}" not in cleaned
    assert "srcdoc" not in cleaned


def test_logger_falls_back_only_when_config_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(renderer, "get_logger_with_params", Mock(side_effect=FileNotFoundError))
    assert renderer._make_logger() is logging.getLogger(renderer.__name__)


def test_logger_propagates_config_validation_errors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("repositories: {}\n", encoding="utf-8")
    monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(renderer, "get_logger_with_params", Mock(side_effect=ValueError("invalid labels")))
    with pytest.raises(ValueError, match="invalid labels"):
        renderer._make_logger()


def test_safe_urls_survive() -> None:
    html = (
        '<a href="https://example.com/docs">docs</a>'
        '<a href="mailto:someone@example.com">mail</a>'
        '<a href="#section">anchor</a>'
        '<a href="/absolute/page">abs</a>'
        '<a href="relative.html">rel</a>'
        '<img src="assets/style.css">'
    )
    assert _sanitize_html(html) == html


def test_dangerous_urls_neutralised() -> None:
    html = '<a href="javascript:alert(1)">x</a><img src="data:text/html;base64,PHNjcmlwdD4=">'
    cleaned = _sanitize_html(html)
    assert "javascript:" not in cleaned
    assert "data:" not in cleaned
    assert cleaned == '<a href="#">x</a><img src="#">'


def test_base_tag_is_stripped() -> None:
    html = '<base href="https://attacker.example/">\n<p>body</p>'
    assert _sanitize_html(html) == "\n<p>body</p>"


def test_self_closing_base_tag_is_stripped() -> None:
    assert _sanitize_html('<base href="https://attacker.example/" />') == ""


def test_base_tag_inside_markdown_snippet_is_stripped() -> None:
    # What the renderer actually sees: python-markdown passes raw HTML through.
    html = '<h1 id="t">Title</h1>\n<base href="https://attacker.example/">\n<p>text</p>'
    cleaned = _sanitize_html(html)
    assert "attacker.example" not in cleaned
    assert "<base" not in cleaned
    assert "<p>text</p>" in cleaned


def test_cleans_annotations_only_on_outermost_code_fence() -> None:
    markdown = "```135:150:src/example.py\nprint('outer')\n```\n\n````markdown\n```config.yaml\nkey: value\n```\n````\n"
    cleaned = _clean_code_fence_annotations(markdown)
    assert "```python" in cleaned
    assert "```config.yaml" in cleaned


def test_inserts_blank_lines_before_blocks_not_inside_fences() -> None:
    markdown = "intro\n- item\n> quote\n```python\n- code\n> code\n```\nafter"
    lines = _ensure_blank_lines(markdown).splitlines()

    for marker in ("- item", "> quote", "```python"):
        index = lines.index(marker)
        assert lines[index - 1] == ""

    assert "```python\n- code\n> code\n```" in "\n".join(lines)


def test_still_inserts_blank_lines_in_prose() -> None:
    markdown = "intro\n- item\n> quote\nafter"
    assert _ensure_blank_lines(markdown) == "intro\n\n- item\n\n> quote\nafter"


def test_no_blank_line_inserted_inside_tilde_fence() -> None:
    markdown = "intro\n~~~\n- item\n> quote\n~~~\nafter"
    # Blank before the opening fence only; the body stays untouched.
    assert _ensure_blank_lines(markdown) == "intro\n\n~~~\n- item\n> quote\n~~~\nafter"


def test_tilde_fence_not_closed_by_backticks() -> None:
    # The ``` lines inside the ~~~ block are content, not closers.
    markdown = "intro\n~~~\n```\n- item\n```\n~~~\n- item\nafter"
    assert _ensure_blank_lines(markdown) == "intro\n\n~~~\n```\n- item\n```\n~~~\n\n- item\nafter"


def test_indents_tilde_fence_body_only() -> None:
    src = "~~~\n# config.yaml\nkey: value\n\n~~~\nafter\n"
    expected = "~~~\n    # config.yaml\n    key: value\n\n~~~\nafter\n"
    assert _indent_fenced_blocks(src) == expected


def test_indent_does_not_close_tilde_fence_on_backticks_and_vice_versa() -> None:
    src = "~~~\n```\n# still inside\n```\n~~~\n"
    assert _indent_fenced_blocks(src) == "~~~\n    ```\n    # still inside\n    ```\n~~~\n"

    src = "```\n~~~\n# still inside\n~~~\n```\n"
    assert _indent_fenced_blocks(src) == "```\n    ~~~\n    # still inside\n    ~~~\n```\n"
