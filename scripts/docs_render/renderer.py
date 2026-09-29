"""Static markdown -> HTML renderer for the documentation site.

Vendored verbatim (modulo the adaptations below) from ``myk-org/docsfy``
(``src/docsfy/renderer.py``) so this repo can rebuild ``docs/`` on its own,
without depending on the external docsfy clone at runtime. Only the
deterministic rendering half was taken: no AI generation, no network, no
``docsfy`` package imports. The docsfy images pipeline (``copy_images_to_site``)
is not vendored because this repo ships no ``docsfy-images/`` source.

Upstream behaviour is preserved, including the "Generated with docsfy" badge,
which keeps working via the ``docsfy_repo_url`` template variable.

It is a vendored copy, so local modifications are expected. The one current
deviation is ``_indent_fenced_blocks`` (see its docstring), which only affects
``llms-full.txt``; the HTML rendering path is byte-identical to upstream.
"""

from __future__ import annotations

import html as _html_mod
import json
import logging
import re
import shutil
import urllib.parse
from collections.abc import Callable
from pathlib import Path
from typing import Any

import markdown
from jinja2 import Environment, FileSystemLoader, select_autoescape

logger = logging.getLogger(__name__)

DOCSFY_REPO_URL = "https://github.com/myk-org/docsfy"

# Vendored from docsfy.generator. Deliberately NOT re.DOTALL, and the title is
# restricted to a single line ([^\n]+ rather than .+). Without these
# constraints, "." matches newlines, so a real multi-paragraph document that
# happens to *end* with this exact phrase would have its entire body greedily
# swallowed as part of the "title" and get misclassified as a stub.
_FAILURE_STUB_RE = re.compile(r"^#[ \t]+[^\n]+\n\n\*Documentation generation failed\.(?: Please re-run\.)?\*\s*$")


def is_generation_failure_stub(content: str) -> bool:
    """Check whether content is one of the known generation-failure stub formats."""
    return bool(_FAILURE_STUB_RE.match(content.strip()))


TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"
_jinja_env: Environment | None = None


def _get_jinja_env() -> Environment:
    global _jinja_env
    if _jinja_env is None:
        _jinja_env = Environment(
            loader=FileSystemLoader(str(TEMPLATES_DIR)),
            autoescape=select_autoescape(["html"]),
        )
    return _jinja_env


def _sanitize_html(html: str) -> str:
    """Remove dangerous HTML elements from AI-generated content."""
    # Remove script tags and content
    html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove iframe, object, embed, form tags
    for tag in ["iframe", "object", "embed", "form"]:
        html = re.sub(rf"<{tag}[^>]*>.*?</{tag}>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(rf"<{tag}[^>]*/>", "", html, flags=re.IGNORECASE)
    # Remove event handler attributes
    html = re.sub(r'\s+on\w+\s*=\s*["\'][^"\']*["\']', "", html, flags=re.IGNORECASE)
    html = re.sub(r"\s+on\w+\s*=\s*\S+", "", html, flags=re.IGNORECASE)

    # Sanitize href/src: allowlist-based URL validation.
    # Safe schemes that pass through unchanged: http://, https://, #, /, mailto:
    # This preserves valid markdown-generated HTML like <a href="https://...">,
    # anchor links (#section), relative paths (/page), and mailto: links.
    # All other schemes (javascript:, data:, vbscript:, etc.) are blocked
    # by replacing the URL with "#".
    def _is_safe_url(url: str) -> bool:
        """Check if a URL is safe to keep (not a dangerous scheme)."""
        decoded = _html_mod.unescape(url).strip()
        decoded_lower = decoded.lower()
        if decoded_lower.startswith(("http://", "https://", "#", "mailto:")):
            return True
        # Allow absolute paths but reject protocol-relative URLs (//evil.com)
        if decoded.startswith("/") and not decoded.startswith("//"):
            return True
        # Reject protocol-relative URLs (//evil.com)
        if decoded.startswith("//"):
            return False
        # Allow relative URLs (no scheme) - e.g., "page-slug.html", "page.html#section:details"
        # Use urlsplit to check for a scheme — it correctly handles
        # colons in fragments/paths vs actual schemes
        parsed = urllib.parse.urlsplit(decoded)
        return not parsed.scheme  # No scheme = relative URL, safe

    def _sanitize_url_attr(match: re.Match) -> str:  # type: ignore[type-arg]
        attr = match.group(1)  # href or src
        quote = match.group(2)  # " or '
        url = match.group(3)  # the URL value
        if _is_safe_url(url):
            return match.group(0)
        return f"{attr}={quote}#{quote}"

    html = re.sub(
        r"(href|src)\s*=\s*([\"'])(.*?)\2",
        _sanitize_url_attr,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Also handle unquoted URLs
    def _sanitize_unquoted_url(match: re.Match) -> str:  # type: ignore[type-arg]
        attr = match.group(1)
        url = match.group(2)
        if _is_safe_url(url):
            return match.group(0)
        return f'{attr}="#"'

    html = re.sub(
        r"(href|src)\s*=\s*([^\s\"'>=]+)",
        _sanitize_unquoted_url,
        html,
        flags=re.IGNORECASE,
    )

    return html


_CODE_FENCE_ANNOTATION_RE = re.compile(r"^(```+)\d+:\d+:(.+)$", re.MULTILINE)
_CODE_FENCE_FILEPATH_RE = re.compile(r"^(```+)((?:\S+/)+\S+)\s*$", re.MULTILINE)
_CODE_FENCE_BARE_FILE_RE = re.compile(
    r"^(```+)([A-Za-z0-9_.-]+\.[A-Za-z0-9_.-]+)\s*$",
    re.MULTILINE,
)

_EXT_TO_LANG: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".rs": "rust",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".yml": "yaml",
    ".yaml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".md": "markdown",
    ".xml": "xml",
    ".ini": "ini",
    ".cfg": "ini",
    ".dockerfile": "dockerfile",
    ".groovy": "groovy",
    ".kt": "kotlin",
    ".swift": "swift",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".r": "r",
    ".pl": "perl",
    ".lua": "lua",
    ".ex": "elixir",
    ".exs": "elixir",
    ".tf": "hcl",
    ".proto": "protobuf",
    ".graphql": "graphql",
    ".scss": "scss",
    ".less": "less",
}


_FILENAME_TO_LANG: dict[str, str] = {
    "Dockerfile": "dockerfile",
    "Makefile": "makefile",
    "Jenkinsfile": "groovy",
    "Vagrantfile": "ruby",
    "Gemfile": "ruby",
    "Rakefile": "ruby",
    "Procfile": "",
    "Brewfile": "ruby",
}


def _lang_from_filepath(filepath: str) -> str:
    """Extract a language identifier from a file path's extension or filename."""
    filepath = filepath.strip()
    basename = filepath.rsplit("/", 1)[-1] if "/" in filepath else filepath
    if "." not in basename:
        return _FILENAME_TO_LANG.get(basename, "")
    ext = "." + basename.rsplit(".", 1)[-1].lower()
    return _EXT_TO_LANG.get(ext, "")


def _clean_code_fence_annotations(md_text: str) -> str:
    """Strip file reference annotations from code fence opening lines.

    AI-generated code blocks sometimes use formats like:
        ```135:150:src/file.py
        ```src/utils/helper.js
        ```config.yaml
    which the markdown library cannot parse. Convert them to:
        ```python
        ```javascript
        ```yaml

    Only applies replacements at the outermost fence level (depth 0) so that
    inner fences inside nested code blocks (e.g. documentation examples) are
    left untouched.
    """

    def _replace_annotated_fence(match: re.Match[str]) -> str:
        fence = match.group(1)
        filepath = match.group(2).strip()
        lang = _lang_from_filepath(filepath)
        return f"{fence}{lang}"

    def _replace_filepath_fence(match: re.Match[str]) -> str:
        fence = match.group(1)
        filepath = match.group(2)
        lang = _lang_from_filepath(filepath)
        return f"{fence}{lang}"

    def _replace_bare_file_fence(match: re.Match[str]) -> str:
        fence = match.group(1)
        filename = match.group(2)
        lang = _lang_from_filepath(filename)
        return f"{fence}{lang}"

    lines = md_text.split("\n")
    result: list[str] = []
    fence_depth = 0
    opening_fence_len = 0

    for line in lines:
        stripped = line.lstrip()

        if stripped.startswith("```"):
            original_line = line
            backtick_count = len(stripped) - len(stripped.lstrip("`"))
            rest = stripped[backtick_count:].strip()

            if fence_depth == 0:
                # Outermost fence opening: apply annotation cleaning
                line = _CODE_FENCE_ANNOTATION_RE.sub(_replace_annotated_fence, line)
                line = _CODE_FENCE_FILEPATH_RE.sub(_replace_filepath_fence, line)
                line = _CODE_FENCE_BARE_FILE_RE.sub(_replace_bare_file_fence, line)
                if line == original_line and rest in _FILENAME_TO_LANG:
                    indent = line[: len(line) - len(stripped)]
                    line = f"{indent}{'`' * backtick_count}{_FILENAME_TO_LANG[rest]}"
                fence_depth = 1
                opening_fence_len = backtick_count
            elif backtick_count >= opening_fence_len and not rest:
                # Closing the outermost fence
                fence_depth = 0
                opening_fence_len = 0
            # else: inner fence marker, ignore

        result.append(line)

    return "\n".join(result)


def _ensure_blank_lines(md_text: str) -> str:
    """Ensure blank lines before markdown block elements.

    The Python markdown library requires blank lines before lists,
    blockquotes, and code fences. AI-generated content often omits these.

    Blank lines are never inserted inside fenced code blocks.
    """
    lines = md_text.split("\n")
    result: list[str] = []
    fence_depth = 0
    opening_fence_len = 0
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Track fence state using backtick-count matching so that
        # inner fences inside an outer block don't toggle the state.
        was_in_fence = fence_depth > 0
        if stripped.startswith("```"):
            backtick_count = len(stripped) - len(stripped.lstrip("`"))
            rest = stripped[backtick_count:].strip()
            if fence_depth == 0:
                # Opening a new fence
                fence_depth = 1
                opening_fence_len = backtick_count
            elif backtick_count >= opening_fence_len and not rest:
                # Closing the current fence (same or more backticks, nothing after)
                fence_depth = 0
                opening_fence_len = 0
            # else: inner fence marker inside outer block, ignore

        # Only insert blank lines outside fenced code blocks.
        # Use was_in_fence so that closing fences (which just toggled
        # in_fence to False) are still considered "inside" the block.
        if not was_in_fence and indent == 0 and i > 0 and result and result[-1].strip() != "":
            # Check if this line starts a block element
            needs_blank = False
            prev_stripped = result[-1].lstrip()

            # List item not preceded by another list item
            if (
                (stripped.startswith(("- ", "* ")) and not prev_stripped.startswith(("- ", "* ")))
                or re.match(r"^\d+\. ", stripped)
                and not re.match(r"^\d+\. ", prev_stripped)
                or stripped.startswith("> ")
                and not prev_stripped.startswith("> ")
                or stripped.startswith("```")
                and not prev_stripped.startswith("```")
            ):
                needs_blank = True

            if needs_blank:
                result.append("")

        result.append(line)

    return "\n".join(result)


def _md_to_html(md_text: str) -> tuple[str, str]:
    """Convert markdown to HTML. Returns (content_html, toc_html)."""
    md = markdown.Markdown(
        extensions=["fenced_code", "codehilite", "tables", "toc"],
        extension_configs={
            "codehilite": {"css_class": "highlight", "guess_lang": False},
            "toc": {"toc_depth": "2-3"},
        },
    )
    md_text = _clean_code_fence_annotations(md_text)
    md_text = _ensure_blank_lines(md_text)
    content_html = _sanitize_html(md.convert(md_text))
    toc_html = getattr(md, "toc", "")
    return content_html, toc_html


def render_page(
    markdown_content: str,
    page_title: str,
    project_name: str,
    tagline: str,
    navigation: list[dict[str, Any]],
    current_slug: str,
    prev_page: dict[str, str] | None = None,
    next_page: dict[str, str] | None = None,
    repo_url: str = "",
    version: str | None = None,
) -> str:
    env = _get_jinja_env()
    template = env.get_template("page.html")
    content_html, toc_html = _md_to_html(markdown_content)
    return template.render(
        title=page_title,
        project_name=project_name,
        tagline=tagline,
        content=content_html,
        toc=toc_html,
        navigation=navigation,
        current_slug=current_slug,
        prev_page=prev_page,
        next_page=next_page,
        repo_url=repo_url,
        docsfy_repo_url=DOCSFY_REPO_URL,
        version=version,
    )


def render_index(
    project_name: str,
    tagline: str,
    navigation: list[dict[str, Any]],
    repo_url: str = "",
    version: str | None = None,
) -> str:
    env = _get_jinja_env()
    template = env.get_template("index.html")
    return template.render(
        title=project_name,
        project_name=project_name,
        tagline=tagline,
        navigation=navigation,
        repo_url=repo_url,
        current_slug="",
        docsfy_repo_url=DOCSFY_REPO_URL,
        version=version,
    )


def _build_search_index(pages: dict[str, str], plan: dict[str, Any]) -> list[dict[str, str]]:
    index: list[dict[str, str]] = []
    title_map: dict[str, str] = {}
    for group in plan.get("navigation", []):
        for page in group.get("pages", []):
            title_map[page.get("slug", "")] = page.get("title", "")
    for slug, content in pages.items():
        index.append({
            "slug": slug,
            "title": title_map.get(slug, slug),
            "content": content[:2000],
        })
    return index


def _build_llms_txt(
    plan: dict[str, Any],
    navigation: list[dict[str, Any]] | None = None,
) -> str:
    """Build llms.txt index file.

    Args:
        plan: The documentation plan dict.
        navigation: Optional filtered navigation list. When provided, this is
            used instead of ``plan["navigation"]`` so that only pages present
            in ``valid_pages`` are included.
    """
    project_name = plan.get("project_name", "Documentation")
    tagline = plan.get("tagline", "")
    nav = navigation if navigation is not None else plan.get("navigation", [])
    lines = [f"# {project_name}", ""]
    if tagline:
        lines.extend([f"> {tagline}", ""])
    for group in nav:
        lines.extend([f"## {group.get('group', '')}", ""])
        for page in group.get("pages", []):
            desc = page.get("description", "")
            page_title = page.get("title", "")
            page_slug = page.get("slug", "")
            if desc:
                lines.append(f"- [{page_title}]({page_slug}.md): {desc}")
            else:
                lines.append(f"- [{page_title}]({page_slug}.md)")
        lines.append("")
    return "\n".join(lines)


def _indent_fenced_blocks(text: str) -> str:
    """Indent the body of every fenced code block by 4 spaces, fences unchanged.

    LOCAL, INTENTIONAL DIVERGENCE from upstream ``myk-org/docsfy``, which emits
    the fences' contents at column 0. ``llms-full.txt`` is read as plain text by
    an LLM, where a line such as ``# config.yaml`` inside a YAML fence looks
    exactly like a top-level markdown heading and pollutes the document outline.
    Indenting the block body by 4 spaces (a valid markdown list/code indent) keeps
    the block delimited by its unindented ``` fences while making the comments
    read as quoted code rather than headings.

    Only lines between an opening ``` fence and its matching closing ``` fence
    are touched; blank lines inside a fence are left truly empty to avoid
    trailing whitespace.
    """
    out: list[str] = []
    in_fence = False
    for line in text.split("\n"):
        if line.startswith("```"):
            in_fence = not in_fence
        elif in_fence and line.strip():
            out.append(f"    {line}")
            continue
        out.append(line)
    return "\n".join(out)


def _build_llms_full_txt(
    plan: dict[str, Any],
    pages: dict[str, str],
    navigation: list[dict[str, Any]] | None = None,
) -> str:
    """Build llms-full.txt with all content concatenated.

    Args:
        plan: The documentation plan dict.
        pages: Mapping of slug to markdown content.
        navigation: Optional filtered navigation list. When provided, this is
            used instead of ``plan["navigation"]`` so that only pages present
            in ``valid_pages`` are included.
    """
    project_name = plan.get("project_name", "Documentation")
    tagline = plan.get("tagline", "")
    nav = navigation if navigation is not None else plan.get("navigation", [])
    lines = [f"# {project_name}", ""]
    if tagline:
        lines.extend([f"> {tagline}", ""])
    lines.extend(["---", ""])
    for group in nav:
        for page in group.get("pages", []):
            slug = page.get("slug", "")
            content = _indent_fenced_blocks(pages.get(slug, ""))
            lines.extend([
                f"Source: {slug}.md",
                "",
                content,
                "",
                "---",
                "",
            ])
    return "\n".join(lines)


def _filter_navigation_pages(
    navigation: list[dict[str, Any]],
    keep_page: Callable[[dict[str, Any]], bool],
) -> list[dict[str, Any]]:
    """Return navigation groups with pages filtered by `keep_page`, dropping empty groups."""
    filtered: list[dict[str, Any]] = []
    for group in navigation:
        kept_pages = [page for page in group.get("pages", []) if keep_page(page)]
        if kept_pages:
            filtered.append({**group, "pages": kept_pages})
    return filtered


def render_site(
    plan: dict[str, Any],
    pages: dict[str, str],
    output_dir: Path,
) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = output_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    # Prevent GitHub Pages from running Jekyll
    (output_dir / ".nojekyll").touch()

    project_name: str = plan.get("project_name", "Documentation")
    tagline: str = plan.get("tagline", "")
    navigation: list[dict[str, Any]] = plan.get("navigation", [])
    repo_url: str = plan.get("repo_url", "")
    version: str | None = plan.get("version")

    if STATIC_DIR.exists():
        for static_file in STATIC_DIR.iterdir():
            if static_file.is_file():
                shutil.copy2(static_file, assets_dir / static_file.name)

    # Filter out invalid slugs
    valid_pages: dict[str, str] = {}
    for slug, content in pages.items():
        if "/" in slug or "\\" in slug or slug.startswith(".") or ".." in slug:
            logger.warning(f"Skipping invalid slug: {slug}")
        else:
            valid_pages[slug] = content

    # Filter navigation to only include pages that exist in valid_pages
    filtered_navigation = _filter_navigation_pages(navigation, lambda page: page.get("slug", "") in valid_pages)

    index_html = render_index(project_name, tagline, filtered_navigation, repo_url=repo_url, version=version)
    (output_dir / "index.html").write_text(index_html, encoding="utf-8")

    # Build ordered list of valid slugs for prev/next navigation
    valid_slug_order: list[dict[str, str]] = []
    for group in filtered_navigation:
        for page in group.get("pages", []):
            slug = page.get("slug", "")
            valid_slug_order.append({"slug": slug, "title": page.get("title", slug)})

    for idx, slug_info in enumerate(valid_slug_order):
        slug = slug_info["slug"]
        md_content = valid_pages[slug]
        title = slug_info["title"]

        prev_page = valid_slug_order[idx - 1] if idx > 0 else None
        next_page = valid_slug_order[idx + 1] if idx < len(valid_slug_order) - 1 else None

        page_html = render_page(
            markdown_content=md_content,
            page_title=title,
            project_name=project_name,
            tagline=tagline,
            navigation=filtered_navigation,
            current_slug=slug,
            prev_page=prev_page,
            next_page=next_page,
            repo_url=repo_url,
            version=version,
        )
        (output_dir / f"{slug}.html").write_text(page_html, encoding="utf-8")
        (output_dir / f"{slug}.md").write_text(md_content, encoding="utf-8")

    search_index = _build_search_index(valid_pages, plan)
    (output_dir / "search-index.json").write_text(json.dumps(search_index), encoding="utf-8")

    # Generate llms.txt files using filtered navigation so only rendered pages
    # appear. Pages whose content is a known generation-failure stub are
    # additionally excluded here so AI-readable indexes never present a
    # failed/CoT page as if it were real documentation, even though the
    # stub remains visible on the HTML site itself as a failure notice.
    llms_navigation = _filter_navigation_pages(
        filtered_navigation,
        lambda page: not is_generation_failure_stub(valid_pages.get(page.get("slug", ""), "")),
    )

    llms_txt = _build_llms_txt(plan, navigation=llms_navigation)
    (output_dir / "llms.txt").write_text(llms_txt, encoding="utf-8")

    llms_full_txt = _build_llms_full_txt(plan, valid_pages, navigation=llms_navigation)
    (output_dir / "llms-full.txt").write_text(llms_full_txt, encoding="utf-8")

    logger.info(f"Rendered site: {len(valid_pages)} pages to {output_dir}")
