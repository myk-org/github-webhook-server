"""Security validation for custom check commands.

This module provides defense-in-depth security checks to prevent
malicious commands from harming the server.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class CommandSecurityResult(NamedTuple):
    """Result of command security validation."""

    is_safe: bool
    error_message: str | None


# Shell metacharacters and operators that could be used for injection
DANGEROUS_SHELL_PATTERNS: list[tuple[str, str]] = [
    (r"[;&|]", "Shell operators (;, &, |) are not allowed"),
    (r"\$\(", "Command substitution $() is not allowed"),
    (r"`", "Backtick command substitution is not allowed"),
    (r"\$\{", "Variable expansion ${} is not allowed"),
    (r"\$[A-Za-z_]", "Variable expansion $VAR is not allowed"),
    (r"[><]", "Redirections (>, <) are not allowed"),
    (r"\|\|", "Logical OR (||) is not allowed"),
    (r"&&", "Logical AND (&&) is not allowed"),
    (r"\\n|\\r", "Newline escapes are not allowed"),
    (r"\beval\b", "eval command is not allowed"),
    (r"\bexec\b", "exec command is not allowed"),
    (r"\bsource\b", "source command is not allowed"),
    (r"\bsh\b", "sh command is not allowed"),
    (r"\bbash\b", "bash command is not allowed"),
    (r"\bzsh\b", "zsh command is not allowed"),
    (r"\bcurl\b", "curl command is not allowed"),
    (r"\bwget\b", "wget command is not allowed"),
    (r"\bnc\b|\bnetcat\b", "netcat/nc command is not allowed"),
    (r"\brm\s+-rf", "rm -rf is not allowed"),
    (r"\bsudo\b", "sudo is not allowed"),
    (r"\bsu\b", "su command is not allowed"),
    (r"\bchmod\b", "chmod is not allowed"),
    (r"\bchown\b", "chown is not allowed"),
    (r"\bmkdir\s+-p\s+/", "Creating directories in root is not allowed"),
]

# Sensitive paths that should never be accessed
SENSITIVE_PATH_PATTERNS: list[tuple[str, str]] = [
    (r"/etc/", "Access to /etc/ is not allowed"),
    (r"/root/", "Access to /root/ is not allowed"),
    (r"~/.ssh", "Access to SSH keys is not allowed"),
    (r"/proc/", "Access to /proc/ is not allowed"),
    (r"/sys/", "Access to /sys/ is not allowed"),
    (r"/dev/", "Access to /dev/ is not allowed"),
    (r"/var/log/", "Access to /var/log/ is not allowed"),
    (r"/boot/", "Access to /boot/ is not allowed"),
    (r"\.\.\/", "Path traversal (..) is not allowed"),
    (r"\.env", "Access to .env files is not allowed"),
    (r"config\.yaml", "Access to config.yaml is not allowed"),
    (r"credentials", "Access to credentials files is not allowed"),
    (r"\.pem\b", "Access to PEM files is not allowed"),
    (r"\.key\b", "Access to key files is not allowed"),
    (r"id_rsa", "Access to SSH private keys is not allowed"),
    (r"id_ed25519", "Access to SSH private keys is not allowed"),
]

# Maximum command length to prevent buffer overflow attacks
MAX_COMMAND_LENGTH = 4096


def validate_command_security(command: str) -> CommandSecurityResult:
    """Validate a command for security issues.

    Args:
        command: The command string to validate

    Returns:
        CommandSecurityResult with is_safe=True if command passes all checks,
        or is_safe=False with an error_message describing the issue.
    """
    # Check command length
    if len(command) > MAX_COMMAND_LENGTH:
        return CommandSecurityResult(
            is_safe=False,
            error_message=f"Command exceeds maximum length of {MAX_COMMAND_LENGTH} characters",
        )

    # Check for dangerous shell patterns
    for pattern, message in DANGEROUS_SHELL_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandSecurityResult(is_safe=False, error_message=message)

    # Check for sensitive path access
    for pattern, message in SENSITIVE_PATH_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            return CommandSecurityResult(is_safe=False, error_message=message)

    # Check for null bytes (could be used to bypass checks)
    if "\x00" in command:
        return CommandSecurityResult(
            is_safe=False,
            error_message="Null bytes are not allowed in commands",
        )

    # Check for non-printable characters (except common whitespace)
    if re.search(r"[^\x20-\x7E\t\n\r]", command):
        return CommandSecurityResult(
            is_safe=False,
            error_message="Non-printable characters are not allowed in commands",
        )

    return CommandSecurityResult(is_safe=True, error_message=None)
