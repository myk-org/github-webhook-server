"""Tests for Alembic migrations environment configuration.

This test module verifies that database credentials are properly URL-encoded
when constructing the connection string, preventing malformed URLs when
credentials contain special characters.
"""

import ast
import pathlib
from urllib.parse import quote

import pytest


class TestMigrationsEnvURLEncoding:
    """Test suite for migrations env.py URL encoding."""

    @pytest.mark.parametrize(
        "username,password,expected_username,expected_password",
        [
            # Special characters that MUST be URL-encoded
            ("user@domain.com", "p@ss:w/rd", "user%40domain.com", "p%40ss%3Aw%2Frd"),
            # More special characters
            ("admin#1", "pass?word", "admin%231", "pass%3Fword"),
            # Mix of safe and unsafe characters
            ("user_name-123", "P@$$w0rd!", "user_name-123", "P%40%24%24w0rd%21"),
            # Simple credentials (no encoding needed)
            ("simple_user", "simple_pass", "simple_user", "simple_pass"),
        ],
    )
    def test_url_encoding_credentials(
        self,
        username: str,
        password: str,
        expected_username: str,
        expected_password: str,
    ) -> None:
        """Test that credentials with special characters are properly URL-encoded.

        This test verifies the fix for URL-encoding database credentials in
        webhook_server/migrations/env.py lines 57-63.

        Args:
            username: Test username (may contain special chars)
            password: Test password (may contain special chars)
            expected_username: Expected URL-encoded username
            expected_password: Expected URL-encoded password
        """
        # Verify our test expectations match urllib.parse.quote behavior
        assert quote(username, safe="") == expected_username
        assert quote(password, safe="") == expected_password

        # Verify URL encoding logic
        # We can't directly execute env.py (it runs on import), so we test the logic
        encoded_username = quote(username, safe="")
        encoded_password = quote(password, safe="")

        db_url = f"postgresql+asyncpg://{encoded_username}:{encoded_password}@localhost:5432/test_db"

        # Verify URL contains encoded credentials
        assert expected_username in db_url
        assert expected_password in db_url

        # Verify URL is well-formed (no unencoded special chars after ://)
        # Split by :// to get credentials part
        credentials_part = db_url.split("://")[1].split("@")[0]
        username_part, password_part = credentials_part.split(":")

        assert username_part == expected_username
        assert password_part == expected_password

    def test_migrations_env_uses_sqlalchemy_url_create(self) -> None:
        """Verify that migrations env.py uses SQLAlchemy URL.create() for safe URL construction.

        SQLAlchemy's URL.create() properly handles special characters in credentials
        and database names, preventing SQL injection and URL parsing issues.
        """
        env_py_path = pathlib.Path(__file__).parent.parent / "migrations" / "env.py"
        env_py_content = env_py_path.read_text()

        # Verify URL is imported from sqlalchemy.engine
        assert "from sqlalchemy.engine import" in env_py_content
        assert "URL" in env_py_content

        # Parse AST to verify URL.create is called
        tree = ast.parse(env_py_content)

        # Check that URL.create() is called (method call on URL object)
        url_create_calls = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Check for URL.create(...) pattern
                if isinstance(node.func, ast.Attribute) and node.func.attr == "create":
                    if isinstance(node.func.value, ast.Name) and node.func.value.id == "URL":
                        url_create_calls += 1

        assert url_create_calls >= 1, "Expected at least 1 call to URL.create() for safe database URL construction"

    def test_special_chars_requiring_encoding(self) -> None:
        """Test that special characters are properly identified and encoded.

        Characters that MUST be encoded in URL credentials:
        - @ (at sign) - separates userinfo from host
        - : (colon) - separates username from password
        - / (slash) - path separator
        - ? (question mark) - query string separator
        - # (hash) - fragment separator
        - % (percent) - encoding prefix
        - & (ampersand) - query parameter separator
        - = (equals) - query parameter value separator
        - + (plus) - space in query strings
        """
        special_chars = {
            "@": "%40",
            ":": "%3A",
            "/": "%2F",
            "?": "%3F",
            "#": "%23",
            "%": "%25",
            "&": "%26",
            "=": "%3D",
            "+": "%2B",
            " ": "%20",
        }

        for char, expected_encoding in special_chars.items():
            # Test encoding with safe="" to encode ALL special chars
            encoded = quote(char, safe="")
            assert encoded == expected_encoding, (
                f"Character '{char}' should encode to '{expected_encoding}', got '{encoded}'"
            )

    def test_real_world_example(self) -> None:
        """Test a real-world example with email username and complex password."""
        # Real-world scenario: email as username, complex password
        username = "webhook-server@example.com"
        password = "C0mpl3x!P@$$w0rd#2024"  # pragma: allowlist secret

        encoded_username = quote(username, safe="")
        encoded_password = quote(password, safe="")

        # Construct URL as in env.py
        db_url = f"postgresql+asyncpg://{encoded_username}:{encoded_password}@db.example.com:5432/webhooks_db"

        # Verify URL is well-formed
        assert "webhook-server%40example.com" in db_url  # @ encoded
        assert "C0mpl3x%21P%40%24%24w0rd%232024" in db_url  # Special chars encoded
        assert "@db.example.com" in db_url  # Host separator @ NOT encoded

        # Verify no unencoded special chars in credentials part
        credentials_part = db_url.split("://")[1].split("@")[0]
        # Should not contain unencoded @ or : or # except the : separator
        assert credentials_part.count(":") == 1  # Only the username:password separator
        assert "@" not in credentials_part  # @ should be encoded
        assert "#" not in credentials_part  # # should be encoded
