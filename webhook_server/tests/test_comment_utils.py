"""Tests for comment_utils module."""

from webhook_server.utils.comment_utils import comment_with_details


class TestCommentWithDetails:
    """Test suite for comment_with_details function."""

    def test_basic_comment_formatting(self) -> None:
        """Test basic comment with simple title and body."""
        result = comment_with_details("Summary", "Details go here")

        assert "<details>" in result
        assert "<summary>Summary</summary>" in result
        assert "Details go here" in result
        assert "</details>" in result

    def test_comment_with_code_block(self) -> None:
        """Test comment containing code block."""
        body = """```python
def hello():
    print("Hello, world!")
```"""
        result = comment_with_details("Code Example", body)

        assert "<summary>Code Example</summary>" in result
        assert "```python" in result
        assert 'print("Hello, world!")' in result

    def test_comment_with_markdown_list(self) -> None:
        """Test comment containing markdown list."""
        body = """- Item 1
- Item 2
- Item 3"""
        result = comment_with_details("List Example", body)

        assert "- Item 1" in result
        assert "- Item 2" in result
        assert "- Item 3" in result

    def test_comment_with_links(self) -> None:
        """Test comment containing markdown links."""
        body = "See [documentation](https://example.com) for details"
        result = comment_with_details("Reference", body)

        assert "[documentation](https://example.com)" in result

    def test_comment_with_html_tags(self) -> None:
        """Test comment containing HTML tags in body."""
        body = "<strong>Bold text</strong> and <em>italic text</em>"
        result = comment_with_details("HTML Content", body)

        assert "<strong>Bold text</strong>" in result
        assert "<em>italic text</em>" in result

    def test_comment_with_empty_body(self) -> None:
        """Test comment with empty body."""
        result = comment_with_details("Empty Details", "")

        assert "<summary>Empty Details</summary>" in result
        assert "<details>" in result
        assert "</details>" in result

    def test_comment_with_empty_title(self) -> None:
        """Test comment with empty title."""
        result = comment_with_details("", "Some content")

        assert "<summary></summary>" in result
        assert "Some content" in result

    def test_comment_with_special_characters_in_title(self) -> None:
        """Test title containing special characters."""
        result = comment_with_details("Build Failed! ⚠️", "Error details")

        assert "<summary>Build Failed! ⚠️</summary>" in result

    def test_comment_with_special_characters_in_body(self) -> None:
        """Test body containing special characters."""
        body = 'Error: "timeout" & connection failed @ 10:30 AM'
        result = comment_with_details("Error Report", body)

        assert 'Error: "timeout" & connection failed @ 10:30 AM' in result

    def test_comment_with_multiline_body(self) -> None:
        """Test comment with multiline body."""
        body = """Line 1
Line 2
Line 3
Line 4"""
        result = comment_with_details("Multiline", body)

        assert "Line 1" in result
        assert "Line 2" in result
        assert "Line 3" in result
        assert "Line 4" in result

    def test_comment_with_table(self) -> None:
        """Test comment containing markdown table."""
        body = """| Column 1 | Column 2 |
|----------|----------|
| Value 1  | Value 2  |"""
        result = comment_with_details("Table Data", body)

        assert "| Column 1 | Column 2 |" in result
        assert "| Value 1  | Value 2  |" in result

    def test_comment_with_headers(self) -> None:
        """Test comment containing markdown headers."""
        body = """# Header 1
## Header 2
### Header 3"""
        result = comment_with_details("Headers", body)

        assert "# Header 1" in result
        assert "## Header 2" in result
        assert "### Header 3" in result

    def test_comment_with_blockquote(self) -> None:
        """Test comment containing blockquote."""
        body = """> This is a quote
> from someone"""
        result = comment_with_details("Quote", body)

        assert "> This is a quote" in result
        assert "> from someone" in result

    def test_comment_with_emoji(self) -> None:
        """Test comment containing emoji."""
        body = "Build succeeded! 🎉 ✅ 🚀"
        result = comment_with_details("Success", body)

        assert "🎉" in result
        assert "✅" in result
        assert "🚀" in result

    def test_comment_with_inline_code(self) -> None:
        """Test comment with inline code."""
        body = "Use the `get_container_repository_and_tag()` function"
        result = comment_with_details("Usage", body)

        assert "`get_container_repository_and_tag()`" in result

    def test_comment_with_unicode_characters(self) -> None:
        """Test comment with Unicode characters."""
        body = "Unicode test: 测试 тест ทดสอบ テスト"
        result = comment_with_details("Unicode", body)

        assert "测试" in result
        assert "тест" in result
        assert "ทดสอบ" in result
        assert "テスト" in result

    def test_comment_with_very_long_body(self) -> None:
        """Test comment with very long body text."""
        long_body = "A" * 10000
        result = comment_with_details("Long Content", long_body)

        assert "Long Content" in result
        assert long_body in result
        assert len(result) > 10000

    def test_comment_with_nested_details(self) -> None:
        """Test comment with nested details/summary in body."""
        body = """<details>
<summary>Nested</summary>
Nested content
</details>"""
        result = comment_with_details("Outer", body)

        assert "<summary>Outer</summary>" in result
        assert "<summary>Nested</summary>" in result
        assert "Nested content" in result

    def test_comment_structure_format(self) -> None:
        """Test that the comment structure follows expected format."""
        result = comment_with_details("Title", "Body")

        # Should have newline after opening details tag
        assert result.startswith("\n<details>")
        # Should have proper indentation
        assert "    Body" in result
        # Should end with closing details tag and newline
        assert result.rstrip().endswith("</details>")

    def test_comment_with_mixed_content(self) -> None:
        """Test comment with mixed markdown, HTML, and special chars."""
        body = """**Build Results:**

- ✅ Tests passed
- ❌ Linting failed

```bash
npm run lint
```

See <a href="https://example.com">logs</a> for details."""
        result = comment_with_details("CI Results", body)

        assert "**Build Results:**" in result
        assert "- ✅ Tests passed" in result
        assert "```bash" in result
        assert '<a href="https://example.com">logs</a>' in result

    def test_comment_preserves_whitespace(self) -> None:
        """Test that whitespace in body is preserved."""
        body = """Line with    multiple   spaces
    Indented line
        More indented"""
        result = comment_with_details("Whitespace", body)

        assert "multiple   spaces" in result
        assert "    Indented line" in result
        assert "        More indented" in result

    def test_comment_with_escape_sequences(self) -> None:
        """Test comment with escape sequences."""
        body = r"Path: C:\Users\test\file.txt\n\tNext line"
        result = comment_with_details("Paths", body)

        assert r"C:\Users\test\file.txt" in result

    def test_comment_with_backticks_in_title(self) -> None:
        """Test title containing backticks."""
        result = comment_with_details("`function()` failed", "Error details")

        assert "<summary>`function()` failed</summary>" in result

    def test_comment_return_type(self) -> None:
        """Test that function returns a string."""
        result = comment_with_details("Test", "Test")

        assert isinstance(result, str)

    def test_comment_with_image_markdown(self) -> None:
        """Test comment with markdown image syntax."""
        body = "![Alt text](https://example.com/image.png)"
        result = comment_with_details("Image", body)

        assert "![Alt text](https://example.com/image.png)" in result

    def test_comment_with_horizontal_rule(self) -> None:
        """Test comment with horizontal rule."""
        body = """Section 1
---
Section 2"""
        result = comment_with_details("Sections", body)

        assert "---" in result
        assert "Section 1" in result
        assert "Section 2" in result
