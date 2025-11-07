"""Comment formatting utilities."""


def comment_with_details(title: str, body: str) -> str:
    """
    Format comment with collapsible details section.

    Args:
        title: Summary text shown when collapsed
        body: Detailed content shown when expanded

    Returns:
        Formatted HTML comment with details/summary tags
    """
    return f"""
<details>
<summary>{title}</summary>
    {body}
</details>
    """
