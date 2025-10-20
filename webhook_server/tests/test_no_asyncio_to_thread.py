"""Test to ensure asyncio.to_thread is ONLY used in unified_api.py."""

import ast
from pathlib import Path


def test_asyncio_to_thread_only_in_unified_api():
    """Verify that asyncio.to_thread is ONLY used in unified_api.py."""
    
    # Files/directories to check
    handlers_dir = Path("webhook_server/libs/handlers/")
    github_api_file = Path("webhook_server/libs/github_api.py")
    
    violations = []
    
    # Check all handler files
    for handler_file in handlers_dir.glob("*.py"):
        if handler_file.name == "__init__.py":
            continue
            
        content = handler_file.read_text()
        if "asyncio.to_thread" in content:
            # Parse to get line numbers
            tree = ast.parse(content, filename=str(handler_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    if (
                        isinstance(node.value, ast.Attribute)
                        and isinstance(node.value.value, ast.Name)
                        and node.value.value.id == "asyncio"
                        and node.value.attr == "to_thread"
                    ):
                        violations.append(f"{handler_file}:{node.lineno}")
    
    # Check github_api.py
    if github_api_file.exists():
        content = github_api_file.read_text()
        if "asyncio.to_thread" in content:
            tree = ast.parse(content, filename=str(github_api_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    if (
                        isinstance(node.value, ast.Attribute)
                        and isinstance(node.value.value, ast.Name)
                        and node.value.value.id == "asyncio"
                        and node.value.attr == "to_thread"
                    ):
                        violations.append(f"{github_api_file}:{node.lineno}")
    
    # Assert no violations
    assert not violations, (
        f"Found asyncio.to_thread outside unified_api.py:\n"
        f"{chr(10).join(violations)}\n\n"
        f"ALL asyncio.to_thread calls MUST be in webhook_server/libs/graphql/unified_api.py ONLY!"
    )


def test_unified_api_has_asyncio_to_thread():
    """Verify that unified_api.py actually uses asyncio.to_thread (sanity check)."""
    
    unified_api_file = Path("webhook_server/libs/graphql/unified_api.py")
    assert unified_api_file.exists(), "unified_api.py must exist"
    
    content = unified_api_file.read_text()
    assert "asyncio.to_thread" in content, (
        "unified_api.py should contain asyncio.to_thread for REST operations"
    )
