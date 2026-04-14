"""Retry wrapper for GitHub API calls made via asyncio.to_thread.

PyGithub is synchronous and must be wrapped with asyncio.to_thread() for
non-blocking operation. When GitHub's API returns transient HTTP 500/502/503/504
errors, urllib3's built-in retries exhaust quickly and raise ConnectionError
or ResponseError. This module provides application-level retry with
exponential backoff as a drop-in replacement for asyncio.to_thread().

Usage::

    from webhook_server.utils.github_retry import github_api_call

    # Method calls with arguments
    await github_api_call(
        pull_request.create_issue_comment,
        body="hello",
        logger=self.logger,
        log_prefix=self.log_prefix,
    )

    # Property access via lambda
    await github_api_call(
        lambda: pull_request.draft,
        logger=self.logger,
        log_prefix=self.log_prefix,
    )
"""

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from github.GithubException import BadCredentialsException, GithubException, UnknownObjectException
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import MaxRetryError, ResponseError

_RETRYABLE_STATUS_CODES = frozenset({500, 502, 503, 504})
_PERMANENT_STATUS_CODES = frozenset({401, 403, 404, 422})
_RETRYABLE_SUBSTRINGS = ("500 error responses", "Max retries exceeded")

_MAX_RETRIES = 4
_BASE_DELAY = 2


def _is_retryable(ex: Exception) -> bool:
    """Determine whether an exception is retryable."""
    if isinstance(ex, (BadCredentialsException, UnknownObjectException)):
        return False

    if isinstance(ex, GithubException):
        if ex.status in _PERMANENT_STATUS_CODES:
            return False
        if ex.status in _RETRYABLE_STATUS_CODES:
            return True
        return False

    if isinstance(ex, (RequestsConnectionError, MaxRetryError, ResponseError)):
        return True

    error_str = str(ex)
    return any(substring in error_str for substring in _RETRYABLE_SUBSTRINGS)


async def github_api_call[T](
    func: Callable[..., T],
    *args: Any,
    logger: logging.Logger,
    log_prefix: str,
    **kwargs: Any,
) -> T:
    """Execute a GitHub API call via asyncio.to_thread with retry on transient errors.

    Drop-in replacement for ``asyncio.to_thread(func, *args, **kwargs)`` that
    retries on transient GitHub API errors (HTTP 500, 502, 503, 504) with exponential
    backoff.

    Args:
        func: The callable to execute in a thread. Can be a bound method
              (e.g. ``pull_request.create_issue_comment``) or a lambda
              (e.g. ``lambda: pull_request.draft``).
        *args: Positional arguments forwarded to *func*.
        logger: Logger instance used for retry warning messages.
        log_prefix: Prefix string prepended to retry warning messages
                    (``self.log_prefix`` from the caller).
        **kwargs: Keyword arguments forwarded to *func*.

    Returns:
        The return value of *func*.

    Raises:
        The original exception after all retries are exhausted, or immediately
        for non-retryable errors (401, 403, 404, 422) and
        ``asyncio.CancelledError``.
    """
    # Note: retries may re-execute non-idempotent operations (e.g., create_issue_comment)
    # if GitHub returned a transient error after partial completion. This is an accepted
    # tradeoff — rare duplicate side effects are preferable to hard failures.
    last_exception: Exception | None = None

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await asyncio.to_thread(func, *args, **kwargs)
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            last_exception = ex

            if not _is_retryable(ex):
                raise

            if attempt == _MAX_RETRIES:
                break

            delay = _BASE_DELAY * (2**attempt)
            logger.warning(
                "%sGitHub API call failed (attempt %d/%d), retrying in %ds: %s: %s",
                f"{log_prefix} " if log_prefix else "",
                attempt + 1,
                _MAX_RETRIES + 1,
                delay,
                type(ex).__name__,
                ex,
            )
            await asyncio.sleep(delay)

    assert last_exception is not None  # noqa: S101
    raise last_exception
