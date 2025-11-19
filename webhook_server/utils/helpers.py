from __future__ import annotations

import asyncio
import contextlib
import datetime
import json
import os
import random
import re
import shlex
import shutil
import subprocess
from collections.abc import AsyncGenerator
from concurrent.futures import Future, as_completed
from logging import Logger
from typing import Any
from uuid import uuid4

import github
from colorama import Fore
from github import GithubException
from github.RateLimitOverview import RateLimitOverview
from github.Repository import Repository
from simple_logger.logger import get_logger
from stringcolor import cs

from webhook_server.libs.config import Config
from webhook_server.libs.exceptions import NoApiTokenError


def get_logger_with_params(
    repository_name: str = "",
) -> Logger:
    mask_sensitive_patterns: list[str] = [
        # Passwords and secrets
        "container_repository_password",
        "password",
        "secret",
        # Tokens and API keys
        "token",
        "apikey",
        "api_key",
        "github_token",
        "GITHUB_TOKEN",
        "pypi",
        # Authentication credentials
        "username",
        "login",
        "-u",
        "-p",
        "--username",
        "--password",
        "--creds",
        # Private keys and sensitive IDs
        "private_key",
        "private-key",
        "webhook_secret",
        "webhook-secret",
        "github-app-id",
        # Slack webhooks (contain sensitive URLs)
        "slack-webhook-url",
        "slack_webhook_url",
        "webhook-url",
        "webhook_url",
    ]

    _config = Config(repository=repository_name)

    log_level: str = _config.get_value(value="log-level", return_on_none="INFO")
    log_file: str = _config.get_value(value="log-file")
    # Get mask-sensitive-data config (default: True to hide sensitive data)
    mask_sensitive: bool = _config.get_value(value="mask-sensitive-data", return_on_none=True)

    if log_file and not log_file.startswith("/"):
        log_file_path = os.path.join(_config.data_dir, "logs")

        if not os.path.isdir(log_file_path):
            os.makedirs(log_file_path, exist_ok=True)

        log_file = os.path.join(log_file_path, log_file)

    # CRITICAL FIX: Use a fixed logger name for the same log file to ensure
    # only ONE RotatingFileHandler instance manages the file rotation.
    # Multiple handlers writing to the same file causes rotation to fail.
    # The original 'name' parameter is preserved in log records via the logger name.
    logger_cache_key = os.path.basename(log_file) if log_file else "console"

    return get_logger(
        name=logger_cache_key,
        filename=log_file,
        level=log_level,
        file_max_bytes=1024 * 1024 * 10,
        mask_sensitive=mask_sensitive,
        mask_sensitive_patterns=mask_sensitive_patterns,
        console=True,  # Enable console output for docker logs with FORCE_COLOR support
    )


def _sanitize_log_value(value: str) -> str:
    """Sanitize value for safe inclusion in structured log messages.

    Prevents log injection by removing newlines and escaping brackets.

    Args:
        value: Raw value to sanitize

    Returns:
        Sanitized value safe for log formatting
    """
    # Remove newlines and carriage returns to prevent log injection
    sanitized = value.replace("\n", " ").replace("\r", " ")
    # Escape brackets to prevent breaking structured log parsing
    sanitized = sanitized.replace("[", "\\[").replace("]", "\\]")
    return sanitized


def format_task_fields(task_id: str | None = None, task_type: str | None = None, task_status: str | None = None) -> str:
    """Format task correlation fields for log messages.

    Args:
        task_id: Task identifier (e.g., "check_tox", "webhook_processing")
        task_type: Task type category (e.g., "ci_check", "webhook_routing")
        task_status: Task status (e.g., "started", "completed", "failed")

    Returns:
        Formatted string with task fields in brackets, or empty string if no fields provided.
        Example: "[task_id=check_tox] [task_type=ci_check] [task_status=started]"
    """
    parts = []
    if task_id:
        parts.append(f"[task_id={_sanitize_log_value(task_id)}]")
    if task_type:
        parts.append(f"[task_type={_sanitize_log_value(task_type)}]")
    if task_status:
        parts.append(f"[task_status={_sanitize_log_value(task_status)}]")
    return " ".join(parts)


# Global cache for compiled regex patterns
# Cache key: (tuple of secrets, case_insensitive flag)
_REDACT_REGEX_CACHE: dict[tuple[tuple[str, ...], bool], re.Pattern[str]] = {}


def _redact_secrets(
    text: str, secrets: list[str] | None, case_insensitive: bool = False, mask_sensitive: bool = True
) -> str:
    """
    Redact sensitive strings from text for logging using compiled regex for performance.

    Uses regex with escaped patterns for safer matching and better scalability.
    For large secret lists or frequent calls, this is significantly faster than
    multiple string.replace() operations.

    Args:
        text: The text to redact secrets from
        secrets: List of sensitive strings to redact (empty strings are filtered out)
        case_insensitive: Enable case-insensitive matching (default: False for security)
        mask_sensitive: Whether to mask sensitive data (default: True). If False, returns text unchanged.

    Returns:
        Text with secrets replaced by ***REDACTED*** (if mask_sensitive=True), otherwise unchanged text

    Performance:
        - O(n) where n = len(text) instead of O(s*n) where s = len(secrets)
        - Compiles single regex pattern from all secrets
        - Uses re.escape() to handle special regex characters safely
        - Caches compiled regex by (secrets, case_insensitive) to reduce CPU in hot paths

    Security Note:
        - Default case-sensitive matching prevents accidental false positives
        - Enable case_insensitive only when secrets may vary in case (e.g., base64 tokens)
    """
    # Early return if masking is disabled
    if not mask_sensitive:
        return text

    if not secrets:
        return text

    # Filter out empty secrets, deduplicate, and escape special regex characters
    # Sort by length descending to prevent substring leaks
    # (e.g., if "abc" and "abcdef" are both secrets, match "abcdef" first)
    escaped_secrets = sorted(
        {re.escape(secret) for secret in secrets if secret},
        key=len,
        reverse=True,
    )
    if not escaped_secrets:
        return text

    # Create cache key from tuple of sorted secrets and case_insensitive flag
    cache_key = (tuple(escaped_secrets), case_insensitive)

    # Check cache for existing compiled regex
    if cache_key in _REDACT_REGEX_CACHE:
        regex = _REDACT_REGEX_CACHE[cache_key]
    else:
        # Build single regex pattern with non-capturing group: (?:secret1|secret2|secret3)
        # Non-capturing group for alternation without word boundaries
        # (tokens can appear anywhere in strings, not just as whole words)
        # Longer secrets first prevents partial redaction
        pattern = f"(?:{'|'.join(escaped_secrets)})"

        # Compile regex with optional case-insensitive flag
        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)

        # Store in cache
        _REDACT_REGEX_CACHE[cache_key] = regex

    # Replace all matches with single sub() call - much faster than loop
    return regex.sub("***REDACTED***", text)


def _truncate_output(text: str, max_length: int = 500) -> str:
    """
    Truncate output text for logging to prevent log explosion.

    Args:
        text: The text to truncate
        max_length: Maximum length before truncation (default: 500)

    Returns:
        Truncated text with ellipsis if exceeds max_length
    """
    if len(text) <= max_length:
        return text

    return f"{text[:max_length]}... [truncated {len(text) - max_length} chars]"


def strip_ansi_codes(text: str) -> str:
    """
    Remove ANSI escape codes from text.

    ANSI escape codes are special character sequences used for terminal formatting
    (colors, bold, underline, etc.) that appear as scrambled characters when displayed
    in non-terminal contexts like GitHub check-run details.

    Args:
        text: Text potentially containing ANSI escape codes

    Returns:
        Clean text with all ANSI escape codes removed

    Examples:
        >>> strip_ansi_codes("\\x1b[31mRed text\\x1b[0m")
        'Red text'
        >>> strip_ansi_codes("\\x1b[1m\\x1b[32mBold green\\x1b[0m")
        'Bold green'
        >>> strip_ansi_codes("No ANSI codes here")
        'No ANSI codes here'
    """
    # Comprehensive regex pattern for ANSI escape sequences:
    # \x1B = ESC character (can also be \033)
    # (?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]) = matches all ANSI escape sequences:
    #   - [@-Z\\-_] = single-character sequences (ESC followed by one char)
    #   - \[[0-?]*[ -/]*[@-~] = CSI sequences (colors, cursor movement, etc.)
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def get_github_repo_api(github_app_api: github.Github, repository: int | str) -> Repository:
    logger = get_logger_with_params()
    logger.debug(f"Get GitHub API for repository {repository}")

    return github_app_api.get_repo(repository)


async def _cleanup_subprocess(
    sub_process: asyncio.subprocess.Process | None, *, graceful: bool = True, log_prefix: str = ""
) -> None:
    """
    Clean up a subprocess and its entire process tree to prevent zombie processes.

    Attempts to kill the process group (created via os.setsid) to ensure all child
    processes are terminated. Falls back to regular process termination if process
    group doesn't exist.

    Args:
        sub_process: The subprocess to clean up, or None
        graceful: If True, try SIGTERM first, then SIGKILL on timeout. If False, use SIGKILL immediately.
        log_prefix: Optional log prefix for warning messages

    Notes:
        Swallows all exceptions during cleanup to avoid masking the original error.
        Only cleans up if process is still running (returncode is None).
    """
    if not sub_process or sub_process.returncode is not None:
        return

    try:
        if graceful:
            # Try to kill the process group gracefully first
            try:
                os.killpg(os.getpgid(sub_process.pid), 15)  # SIGTERM
                try:
                    await asyncio.wait_for(sub_process.wait(), timeout=5)
                    return  # Successfully terminated
                except asyncio.TimeoutError:
                    # Process didn't terminate gracefully, escalate to SIGKILL
                    pass
            except (ProcessLookupError, OSError):
                # Process group might not exist, fall back to regular process termination
                sub_process.terminate()
                try:
                    await asyncio.wait_for(sub_process.wait(), timeout=5)
                    return  # Successfully terminated
                except asyncio.TimeoutError:
                    # Process didn't terminate gracefully, escalate to kill
                    pass

        # Forceful cleanup: kill the process group or process directly
        try:
            os.killpg(os.getpgid(sub_process.pid), 9)  # SIGKILL
            await sub_process.wait()
        except (ProcessLookupError, OSError):
            # Process group might not exist, fall back to regular kill
            sub_process.kill()
            await sub_process.wait()
    except Exception:
        # Best effort cleanup; do not mask original error
        pass


async def run_command(
    command: str,
    log_prefix: str,
    verify_stderr: bool = False,
    redact_secrets: list[str] | None = None,
    stdin_input: str | bytes | None = None,
    timeout: int | None = None,
    mask_sensitive: bool = True,
    **kwargs: Any,
) -> tuple[bool, str, str]:
    """
    Run command locally using create_subprocess_exec (safe from shell injection).

    Args:
        command (str): Command to run (will be split with shlex.split for safety)
        log_prefix (str): Prefix for log messages
        verify_stderr (bool, default False): Check command stderr
        redact_secrets (list[str], optional): List of sensitive strings to redact from logs only
        stdin_input (str | bytes | None, optional): Input to pass to command via stdin (for passwords, etc.)
        timeout (int | None, optional): Timeout in seconds for command execution. None means no timeout.
        mask_sensitive (bool, default True): Whether to mask sensitive data in logs. If False, logs unredacted output.

    Returns:
        tuple[bool, str, str]: (success, stdout, stderr) where stdout and stderr are UNREDACTED strings.
                               Redaction is ONLY applied to log output, not return values.
                               Callers may need to parse unredacted output for command results.

    Security:
        Uses asyncio.create_subprocess_exec (NOT shell=True) to prevent command injection.
        stdin_input is passed via pipe, not command line arguments.
        Secrets are redacted in logs but NOT in return values - callers must handle sensitive data.
    """
    logger = get_logger_with_params()
    out_decoded: str = ""
    err_decoded: str = ""
    # Don't override caller-provided pipes - use setdefault to respect provided kwargs
    kwargs.setdefault("stdout", subprocess.PIPE)
    kwargs.setdefault("stderr", subprocess.PIPE)

    # Set up stdin pipe if input is provided
    if stdin_input is not None:
        kwargs.setdefault("stdin", subprocess.PIPE)

    # Redact sensitive data from command for logging
    logged_command = _redact_secrets(command, redact_secrets, mask_sensitive=mask_sensitive)

    sub_process: asyncio.subprocess.Process | None = None
    stdout: bytes | None = None
    stderr: bytes | None = None
    try:
        logger.debug(f"{log_prefix} Running '{logged_command}' command")
        command_list = shlex.split(command)

        # Create subprocess in a new process group so we can kill the entire tree
        # This prevents zombie processes when killing processes that spawn children
        kwargs.setdefault("preexec_fn", os.setsid)
        sub_process = await asyncio.create_subprocess_exec(
            *command_list,
            **kwargs,
        )

        # Prepare stdin (convert str to bytes if needed)
        stdin_bytes = None
        if stdin_input is not None:
            stdin_bytes = stdin_input.encode("utf-8") if isinstance(stdin_input, str) else stdin_input

        # Execute with optional timeout
        try:
            if timeout:
                stdout, stderr = await asyncio.wait_for(sub_process.communicate(input=stdin_bytes), timeout=timeout)
            else:
                stdout, stderr = await sub_process.communicate(input=stdin_bytes)
        except TimeoutError:
            logger.error(f"{log_prefix} Command '{logged_command}' timed out after {timeout}s")
            await _cleanup_subprocess(sub_process, graceful=True, log_prefix=log_prefix)
            return False, "", f"Command timed out after {timeout}s"
        except Exception as comm_exc:
            # If communicate() raises any exception (not just TimeoutError),
            # we need to ensure the subprocess is cleaned up
            logger.error(f"{log_prefix} Command '{logged_command}' communicate() raised exception: {comm_exc}")
            await _cleanup_subprocess(sub_process, graceful=True, log_prefix=log_prefix)
            # Re-raise to be handled by outer exception handler
            raise
        # Ensure we always have strings, never None or bytes
        out_decoded = stdout.decode(errors="ignore") if isinstance(stdout, bytes) else (stdout or "")
        err_decoded = stderr.decode(errors="ignore") if isinstance(stderr, bytes) else (stderr or "")

        # Redact secrets ONLY for logging, keep original for return value
        # Callers may need to parse unredacted output
        out_redacted = _redact_secrets(out_decoded, redact_secrets, mask_sensitive=mask_sensitive)
        err_redacted = _redact_secrets(err_decoded, redact_secrets, mask_sensitive=mask_sensitive)

        # Truncate output for error messages to prevent log explosion (logging only)
        truncated_out = _truncate_output(out_redacted)
        truncated_err = _truncate_output(err_redacted)

        error_msg = (
            f"{log_prefix} Failed to run '{logged_command}'. "
            f"rc: {sub_process.returncode}, out: {truncated_out}, error: {truncated_err}"
        )

        if sub_process.returncode != 0:
            logger.error(error_msg)
            return False, out_decoded, err_decoded

        # From this point and onwards we are guaranteed that sub_process.returncode == 0
        if err_decoded and verify_stderr:
            logger.error(error_msg)
            return False, out_decoded, err_decoded

        return True, out_decoded, err_decoded

    except asyncio.CancelledError:
        logger.debug(f"{log_prefix} Command '{logged_command}' cancelled")
        await _cleanup_subprocess(sub_process, graceful=False, log_prefix=log_prefix)
        raise
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        logger.exception(f"{log_prefix} Failed to run '{logged_command}' command")
        await _cleanup_subprocess(sub_process, graceful=True, log_prefix=log_prefix)
        return False, out_decoded, err_decoded
    finally:
        # Final safety net: ensure subprocess is always waited for
        # This handles edge cases where exceptions might have been missed
        if sub_process and sub_process.returncode is None:
            logger.warning(f"{log_prefix} Subprocess still running in finally block, forcing cleanup")
        await _cleanup_subprocess(sub_process, graceful=False, log_prefix=log_prefix)


def get_apis_and_tokes_from_config(config: Config) -> list[tuple[github.Github, str]]:
    apis_and_tokens: list[tuple[github.Github, str]] = []
    # Guard against None tokens from config - default to empty list
    tokens = config.get_value(value="github-tokens") or []

    for _token in tokens:
        apis_and_tokens.append((github.Github(auth=github.Auth.Token(_token)), _token))

    return apis_and_tokens


def get_api_with_highest_rate_limit(config: Config, repository_name: str = "") -> tuple[github.Github, str, str]:
    """
    Get API with the highest rate limit

    Args:
        config (Config): Config object
        repository_name (str, optional): Repository name, if provided try to get token set in config repository section.

    Returns:
        tuple: API, token, api_user
    """
    logger = get_logger_with_params()

    api: github.Github | None = None
    token: str | None = None
    _api_user: str = ""

    remaining = 0

    msg = "Get API and tokens"

    if repository_name:
        msg += f" for repository {repository_name}"

    logger.debug(msg)

    apis_and_tokens = get_apis_and_tokes_from_config(config=config)
    logger.debug(f"Checking {len(apis_and_tokens)} API(s) for highest rate limit")

    for _api, _token in apis_and_tokens:
        if _api.rate_limiting[-1] == 60:
            logger.warning("API has rate limit set to 60 which indicates an invalid token, skipping")
            continue

        try:
            _api_user = _api.get_user().login
        except GithubException as ex:
            # This catches RateLimitExceededException as it's a subclass of GithubException
            logger.warning(f"Failed to get API user for API {_api}, skipping. {ex}")
            continue

        _rate_limit = _api.get_rate_limit()
        log_rate_limit(rate_limit=_rate_limit, api_user=_api_user)

        if _rate_limit.rate.remaining > remaining:
            remaining = _rate_limit.rate.remaining
            api, token, _api_user = _api, _token, _api_user
            logger.debug(f"API user {_api_user} has higher rate limit ({remaining}), updating selection")

    if not _api_user or not api or not token:
        raise NoApiTokenError("Failed to get API with highest rate limit")

    logger.info(f"API user {_api_user} selected with highest rate limit: {remaining}")
    return api, token, _api_user


def log_rate_limit(rate_limit: RateLimitOverview, api_user: str) -> None:
    logger = get_logger_with_params()

    rate_limit_str: str
    delta = rate_limit.rate.reset - datetime.datetime.now(tz=datetime.UTC)
    time_for_limit_reset = max(int(delta.total_seconds()), 0)
    below_minimum: bool = rate_limit.rate.remaining < 700

    if below_minimum:
        rate_limit_str = f"{Fore.RED}{rate_limit.rate.remaining}{Fore.RESET}"

    elif rate_limit.rate.remaining < 2000:
        rate_limit_str = f"{Fore.YELLOW}{rate_limit.rate.remaining}{Fore.RESET}"

    else:
        rate_limit_str = f"{Fore.GREEN}{rate_limit.rate.remaining}{Fore.RESET}"

    msg = (
        f"{Fore.CYAN}[{api_user}] API rate limit:{Fore.RESET} Current {rate_limit_str} of {rate_limit.rate.limit}. "
        f"Reset in {rate_limit.rate.reset} [{datetime.timedelta(seconds=time_for_limit_reset)}] "
        f"(UTC time is {datetime.datetime.now(tz=datetime.UTC)})"
    )
    logger.debug(msg)
    if below_minimum:
        logger.warning(msg)


def get_future_results(futures: list[Future]) -> None:
    """
    Process futures from repository configuration tasks.

    Args:
        futures: List of futures that return (success, message, logger_func) tuples

    Notes:
        Continues processing on exceptions to handle partial failures gracefully.
        Worker threads may crash on archived repositories or API permission issues.
    """
    logger = get_logger_with_params()

    for result in as_completed(futures):
        try:
            # CRITICAL FIX: Calling result.result() will raise exception if one exists
            # This gives us proper exception context for logger.exception()
            _, message, logger_func = result.result()
            logger_func(message)
        except Exception:
            # Proper exception context - logger.exception() can capture traceback
            logger.exception(
                "Repository configuration crashed. Check for archived repositories or API permission issues."
            )


def get_repository_color_for_log_prefix(repository_name: str, data_dir: str) -> str:
    """
    Get a consistent color for repository name in log prefixes.

    Args:
        repository_name: Repository name to get color for
        data_dir: Directory to store color mappings

    Returns:
        Colored repository name string
    """

    def _get_random_color(_colors: list[str], _json: dict[str, str]) -> str:
        color = random.choice(_colors)
        _json[repository_name] = color
        if _selected := cs(repository_name, color).render():
            return _selected
        return repository_name

    _all_colors: list[str] = []
    color_json: dict[str, str]
    _colors_to_exclude = ("blue", "white", "black", "grey")
    color_file: str = os.path.join(data_dir, "log-colors.json")

    for _color_name in cs.colors.values():
        _cname = _color_name["name"]
        if _cname.lower() in _colors_to_exclude:
            continue
        _all_colors.append(_cname)

    try:
        with open(color_file) as fd:
            color_json = json.load(fd)
    except Exception:
        color_json = {}

    if color := color_json.get(repository_name, ""):
        _cs_object = cs(repository_name, color)
        if cs.find_color(_cs_object):
            _str_color = _cs_object.render()
        else:
            _str_color = _get_random_color(_colors=_all_colors, _json=color_json)
    else:
        _str_color = _get_random_color(_colors=_all_colors, _json=color_json)

    with open(color_file, "w") as fd:
        json.dump(color_json, fd)

    if _str_color:
        _str_color = _str_color.replace("\x1b", "\033")
        return _str_color
    return repository_name


def prepare_log_prefix(
    event_type: str,
    delivery_id: str,
    repository_name: str | None = None,
    api_user: str | None = None,
    pr_number: int | None = None,
    data_dir: str | None = None,
) -> str:
    """
    Prepare standardized log prefix for consistent formatting across webhook processing.

    Args:
        event_type: GitHub event type (e.g., 'pull_request', 'check_run')
        delivery_id: GitHub delivery ID (x-github-delivery header)
        repository_name: Repository name for color coding (optional)
        api_user: API user for the request (optional)
        pr_number: Pull request number if applicable (optional)
        data_dir: Directory for storing color mappings (optional, defaults to /tmp)

    Returns:
        Formatted log prefix string
    """
    if repository_name and data_dir:
        repository_color = get_repository_color_for_log_prefix(repository_name, data_dir)
    else:
        repository_color = repository_name or ""

    # Build prefix components (sanitize to prevent log injection)
    components = [_sanitize_log_value(event_type), _sanitize_log_value(delivery_id)]
    if api_user:
        components.append(_sanitize_log_value(api_user))

    prefix = f"{repository_color} [{']['.join(components)}]"

    if pr_number:
        prefix += f"[PR {pr_number}]"

    return prefix + ":"


@contextlib.asynccontextmanager
async def git_worktree_checkout(
    repo_dir: str,
    checkout: str,
    log_prefix: str,
    mask_sensitive: bool = True,
) -> AsyncGenerator[tuple[bool, str, str, str], None]:
    """Create git worktree for isolated checkout operations.

    Creates a temporary worktree from existing cloned repository, allowing
    multiple handlers to work with different checkouts simultaneously.

    Args:
        repo_dir: Path to cloned git repository
        checkout: Branch, tag, or commit to checkout
        log_prefix: Logging prefix
        mask_sensitive: Whether to mask sensitive data in logs

    Yields:
        tuple: (success: bool, worktree_path: str, stdout: str, stderr: str)

    Example:
        async with git_worktree_checkout(repo_dir, "origin/pr/123", log_prefix) as (success, path, out, err):
            if success:
                # Use path for operations
                await run_command(f"pytest {path}/tests")
    """
    worktree_path = f"{repo_dir}-worktree-{uuid4()}"
    result: tuple[bool, str, str, str] = (False, "", "", "")

    try:
        # Create worktree
        rc, out, err = await run_command(
            command=f"git -C {repo_dir} worktree add {worktree_path} {checkout}",
            log_prefix=log_prefix,
            mask_sensitive=mask_sensitive,
        )

        if rc:
            result = (True, worktree_path, out, err)
        else:
            result = (False, worktree_path, out, err)

        yield result

    finally:
        # Cleanup: Remove worktree
        if os.path.exists(worktree_path):
            try:
                # Remove worktree from git
                await run_command(
                    command=f"git -C {repo_dir} worktree remove {worktree_path} --force",
                    log_prefix=log_prefix,
                    mask_sensitive=mask_sensitive,
                )
            except Exception:
                # Fallback: Force delete directory if git command fails
                shutil.rmtree(worktree_path, ignore_errors=True)
