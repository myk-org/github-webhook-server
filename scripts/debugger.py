#!/usr/bin/env python3
"""GraphQL debugger script for GitHub API.

This script creates a working GraphQL client for debugging GitHub API queries.
It accepts a GitHub token (as argument or from $GITHUB_TOKEN env var) and provides
an interactive session for running GraphQL queries.

Usage:
    uv run scripts/debugger.py [token]
    uv run scripts/debugger.py ghp_xxxxxxxxxxxxx
    # Or set $GITHUB_TOKEN environment variable:
    uv run scripts/debugger.py

Example:
    $ export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
    $ uv run scripts/debugger.py
    >>> query { viewer { login } }
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from typing import Any

from simple_logger.logger import get_logger

from webhook_server.libs.graphql.graphql_client import GraphQLClient


async def run_query(client: GraphQLClient, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a GraphQL query and return the result."""
    try:
        result = await client.execute(query, variables or {})
        return result
    except Exception as e:
        print(f"\nError executing query: {e}", file=sys.stderr)
        traceback.print_exc()
        raise


async def interactive_session(client: GraphQLClient) -> None:
    """Run an interactive GraphQL query session."""
    print(f"\n{'=' * 70}")
    print("GraphQL Debugger")
    print(f"{'=' * 70}")
    print("\nEnter GraphQL queries (use 'exit' or 'quit' to exit)")
    print("Example queries:")
    print("  query { viewer { login } }")
    print('  query { repository(owner: "owner", name: "repo") { name } }')
    print()

    while True:
        try:
            # Read multi-line query
            lines = []
            print(">>> ", end="", flush=True)
            while True:
                line = input()
                if not line.strip():
                    break
                lines.append(line)
                if line.strip().endswith("}"):
                    break

            query = "\n".join(lines).strip()

            if not query:
                continue

            if query.lower() in ("exit", "quit"):
                print("Exiting...")
                break

            # Execute query
            print("\nExecuting query...")
            result = await run_query(client, query)
            print("\nResult:")
            print(json.dumps(result, indent=2))
            print()

        except KeyboardInterrupt:
            print("\n\nExiting...")
            break
        except EOFError:
            print("\n\nExiting...")
            break
        except Exception as e:
            print(f"\nError: {e}", file=sys.stderr)
            traceback.print_exc()
            print()


async def main() -> None:
    """Main entry point for the debugger script."""
    parser = argparse.ArgumentParser(
        description="GraphQL debugger for GitHub API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode (token from environment)
  export GITHUB_TOKEN=ghp_xxxxxxxxxxxxx
  uv run scripts/debugger.py

  # Interactive mode (token as argument)
  uv run scripts/debugger.py ghp_xxxxxxxxxxxxx

  # Run a single query
  uv run scripts/debugger.py --query 'query { viewer { login } }'
        """,
    )
    parser.add_argument(
        "token",
        nargs="?",
        help=(
            "GitHub personal access token (ghp_...) or GitHub App token (optional, uses $GITHUB_TOKEN if not provided)"
        ),
    )
    parser.add_argument(
        "--query",
        "-q",
        help="GraphQL query to execute (if not provided, starts interactive mode)",
    )
    parser.add_argument(
        "--variables",
        "-v",
        help="JSON string with variables for the query",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Get token from argument or environment variable
    token = args.token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "Error: GitHub token is required. Provide it as an argument or set $GITHUB_TOKEN environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Validate token format (GitHub tokens typically start with ghp_, gho_, ghu_, etc.)
    if not token.startswith(("ghp_", "gho_", "ghu_", "ghs_", "ghr_")):
        print(
            f"Error: Invalid GitHub token format. Token should start with 'ghp_', 'gho_', 'ghu_', 'ghs_', or 'ghr_'.\n"
            f"  Got: {token[:10]}... (first 10 characters)\n"
            f"  Did you mean to set $GITHUB_TOKEN instead?",
            file=sys.stderr,
        )
        sys.exit(1)

    # Set up logging
    log_level = "DEBUG" if args.verbose else "INFO"
    logger = get_logger(name="graphql-debugger", level=log_level)

    # Create GraphQL client
    print("Initializing GraphQL client...")
    client = GraphQLClient(token=token, logger=logger)

    try:
        if args.query:
            # Execute single query
            variables = None
            if args.variables:
                try:
                    variables = json.loads(args.variables)
                    # Strip whitespace from string values to prevent common user errors
                    if isinstance(variables, dict):
                        variables = {
                            key: value.strip() if isinstance(value, str) else value for key, value in variables.items()
                        }
                except json.JSONDecodeError as e:
                    print(f"Error: Invalid JSON in --variables: {e}", file=sys.stderr)
                    print(f"  Input: {args.variables}", file=sys.stderr)
                    sys.exit(1)

            try:
                result = await run_query(client, args.query, variables)
                print(json.dumps(result, indent=2))
            except Exception:
                print("\nQuery execution failed.", file=sys.stderr)
                sys.exit(1)
        else:
            # Interactive mode
            await interactive_session(client)
    finally:
        # Cleanup
        try:
            await client.close()
        except Exception:
            pass  # Ignore cleanup errors


if __name__ == "__main__":
    asyncio.run(main())
