"""Test to ensure asyncio.to_thread is ONLY used in unified_api.py."""

import ast
from pathlib import Path


def test_asyncio_to_thread_only_in_unified_api() -> None:
    """Verify that asyncio.to_thread is ONLY used in unified_api.py or for send_slack_message."""

    # Files/directories to check
    handlers_dir = Path("webhook_server/libs/handlers/")
    github_api_file = Path("webhook_server/libs/github_api.py")

    violations = []

    def is_slack_message_call(node: ast.Call, content_lines: list[str]) -> bool:
        """Check if asyncio.to_thread call is for send_slack_message."""
        # Check if first argument is send_slack_message
        if node.args and isinstance(node.args[0], ast.Name):
            if node.args[0].id == "send_slack_message":
                return True
        return False

    # Check all handler files (including subpackages)
    for handler_file in handlers_dir.rglob("*.py"):
        if handler_file.name == "__init__.py":
            continue

        content = handler_file.read_text()
        if "asyncio.to_thread" in content:
            # Parse to get line numbers
            tree = ast.parse(content, filename=str(handler_file))
            content_lines = content.splitlines()
            for node in ast.walk(tree):
                # Check for Call nodes where func is asyncio.to_thread
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "asyncio"
                        and node.func.attr == "to_thread"
                    ):
                        # Allow if it's for send_slack_message
                        if not is_slack_message_call(node, content_lines):
                            violations.append(f"{handler_file}:{node.lineno}")

    # Check github_api.py
    if github_api_file.exists():
        content = github_api_file.read_text()
        if "asyncio.to_thread" in content:
            tree = ast.parse(content, filename=str(github_api_file))
            content_lines = content.splitlines()
            for node in ast.walk(tree):
                # Check for Call nodes where func is asyncio.to_thread
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if (
                        isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "asyncio"
                        and node.func.attr == "to_thread"
                    ):
                        # Allow if it's for send_slack_message
                        if not is_slack_message_call(node, content_lines):
                            violations.append(f"{github_api_file}:{node.lineno}")

    # Assert no violations
    assert not violations, (
        f"Found asyncio.to_thread outside unified_api.py (not for send_slack_message):\n"
        f"{chr(10).join(violations)}\n\n"
        f"asyncio.to_thread calls MUST be in webhook_server/libs/graphql/unified_api.py ONLY!\n"
        f"EXCEPTION: asyncio.to_thread(send_slack_message, ...) is allowed in handlers."
    )


def test_unified_api_has_asyncio_to_thread() -> None:
    """Verify that unified_api.py actually uses asyncio.to_thread (sanity check)."""

    unified_api_file = Path("webhook_server/libs/graphql/unified_api.py")
    assert unified_api_file.exists(), "unified_api.py must exist"

    content = unified_api_file.read_text()
    assert "asyncio.to_thread" in content, "unified_api.py should contain asyncio.to_thread for REST operations"
