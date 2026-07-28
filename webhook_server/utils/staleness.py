"""Staleness detection and debounce utilities for webhook processing.

Provides:
- ``is_stale_for_pr``: Compare webhook payload SHA against a PR's current HEAD SHA.
- ``MergeCheckDebouncer``: Coalescing debounce for ``check_if_can_be_merged`` calls.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from github.PullRequest import PullRequest

from webhook_server.utils.github_retry import github_api_call


async def is_stale_for_pr(
    *,
    pull_request: PullRequest,
    webhook_sha: str,
    logger: logging.Logger,
    log_prefix: str,
) -> bool:
    """Check whether a webhook event targets a commit that is no longer the PR HEAD.

    Fetches the PR's *current* ``head.sha`` from the API (one API call) and
    compares it to the SHA carried in the webhook payload.

    Args:
        pull_request: The resolved PR object.
        webhook_sha: The commit SHA from the webhook payload
                     (``check_run.head_sha``, ``status.sha``, or ``pull_request.after``).
        logger: Logger for diagnostic messages.
        log_prefix: Prefix for log lines.

    Returns:
        ``True`` if the webhook SHA does **not** match the PR's current HEAD
        (i.e. the event is stale and should be skipped).
    """
    current_head: str = await github_api_call(
        lambda: pull_request.head.sha,
        logger=logger,
        log_prefix=log_prefix,
    )

    if current_head == webhook_sha:
        return False

    logger.info(
        "%s Stale webhook detected: payload SHA %s != current PR HEAD %s — skipping",
        log_prefix,
        webhook_sha[:7],
        current_head[:7],
    )
    return True


# ---------------------------------------------------------------------------
# Debouncer for check_if_can_be_merged
# ---------------------------------------------------------------------------

_DEBOUNCE_WINDOW: float = 3.0  # seconds


class MergeCheckDebouncer:
    """Coalesce rapid ``check_if_can_be_merged`` calls for the same PR.

    When multiple check_run / status / review-thread events arrive in a burst
    for the same PR, only the **last** trigger within a configurable window
    actually executes the merge-eligibility evaluation.

    The caller of :meth:`schedule` **blocks** until the debounced callback
    completes (or until the call is superseded by a newer event for the same
    PR, in which case :meth:`schedule` returns silently).  This keeps the
    caller's clone directory alive for the duration.

    Usage (module-level singleton)::

        _merge_debouncer = MergeCheckDebouncer()

        await _merge_debouncer.schedule(
            repo_full_name="org/repo",
            pr_number=42,
            callback=my_async_func,
            logger=logger,
            log_prefix="[TEST]",
        )
    """

    def __init__(self, window: float = _DEBOUNCE_WINDOW) -> None:
        self._window = window
        # (repo_full_name, pr_number) → pending asyncio.Task
        self._pending: dict[tuple[str, int], asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()
        # Tasks cancelled internally by schedule() supersession.
        # Used to distinguish supersession from external shutdown cancellation.
        self._superseded: set[int] = set()

    async def schedule(
        self,
        repo_full_name: str,
        pr_number: int,
        callback: Callable[[], Awaitable[None]],
        logger: logging.Logger,
        log_prefix: str,
    ) -> None:
        """Schedule (or reschedule) a merge-eligibility check for *pr_number*.

        If a check is already pending for this PR, it is cancelled and
        replaced by a new one that will fire after *window* seconds of quiet.

        The caller **awaits** the debounced task so that the webhook's clone
        directory stays alive.  If this call is superseded by a newer
        ``schedule()``, the ``await`` returns silently (the newer call takes
        over execution).  External cancellation (shutdown/timeout) is
        re-raised to propagate properly.
        """
        key = (repo_full_name, pr_number)

        async with self._lock:
            existing = self._pending.pop(key, None)
            if existing and not existing.done():
                # Mark as internally superseded before cancelling
                self._superseded.add(id(existing))
                existing.cancel()
                logger.debug(
                    "%s Debounce: cancelled pending merge check for %s PR #%d (superseded)",
                    log_prefix,
                    repo_full_name,
                    pr_number,
                )

            task = asyncio.create_task(
                self._delayed_run(key, callback, logger, log_prefix),
            )
            self._pending[key] = task

        # Await the task so the caller's clone dir stays alive.
        try:
            await task
        except asyncio.CancelledError:
            if id(task) in self._superseded:
                # Internal supersession — swallow and return silently
                self._superseded.discard(id(task))
                logger.debug(
                    "%s Debounce: merge check for %s PR #%d superseded by newer event",
                    log_prefix,
                    repo_full_name,
                    pr_number,
                )
            else:
                # External cancellation (shutdown/timeout) — propagate
                raise

    async def _delayed_run(
        self,
        key: tuple[str, int],
        callback: Callable[[], Awaitable[None]],
        logger: logging.Logger,
        log_prefix: str,
    ) -> None:
        """Wait for the debounce window, then execute *callback*."""
        current_task = asyncio.current_task()

        try:
            await asyncio.sleep(self._window)
        except asyncio.CancelledError:
            # Clean up _pending for the cancelled task
            async with self._lock:
                if self._pending.get(key) is current_task:
                    self._pending.pop(key, None)
            # Always re-raise — schedule() distinguishes internal vs external
            raise

        # Remove ourselves from _pending before executing so a new event
        # during execution doesn't try to cancel us mid-run.
        async with self._lock:
            if self._pending.get(key) is current_task:
                self._pending.pop(key, None)

        repo_full_name, pr_number = key
        logger.info(
            "%s Debounce: executing merge check for %s PR #%d after %.1fs quiet window",
            log_prefix,
            repo_full_name,
            pr_number,
            self._window,
        )
        await callback()
