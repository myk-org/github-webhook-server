"""Self-checks for the vendored renderer: llms-full indenting and HTML sanitizing."""

from __future__ import annotations

from docs_render.renderer import _indent_fenced_blocks, _sanitize_html


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
