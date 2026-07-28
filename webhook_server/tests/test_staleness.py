"""Tests for webhook_server.utils.staleness — staleness detection and debounce."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.utils.staleness import MergeCheckDebouncer, is_stale_for_pr

# ---------------------------------------------------------------------------
# is_stale_for_pr
# ---------------------------------------------------------------------------


class TestIsStaleForPr:
    """Tests for the is_stale_for_pr staleness utility."""

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        pr = Mock()
        pr.head = Mock()
        pr.head.sha = "abc1234567890abc1234567890abc1234567890ab"  # pragma: allowlist secret
        return pr

    @pytest.fixture
    def logger(self) -> Mock:
        return Mock()

    @pytest.mark.asyncio
    async def test_not_stale_when_sha_matches(self, mock_pull_request: Mock, logger: Mock) -> None:
        """Webhook SHA matches PR HEAD — event is current, not stale."""
        with patch("webhook_server.utils.staleness.github_api_call", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = "abc1234567890abc1234567890abc1234567890ab"  # pragma: allowlist secret

            result = await is_stale_for_pr(
                pull_request=mock_pull_request,
                webhook_sha="abc1234567890abc1234567890abc1234567890ab",  # pragma: allowlist secret
                logger=logger,
                log_prefix="[TEST]",
            )

        assert result is False

    @pytest.mark.asyncio
    async def test_stale_when_sha_differs(self, mock_pull_request: Mock, logger: Mock) -> None:
        """Webhook SHA does not match PR HEAD — event is stale."""
        with patch("webhook_server.utils.staleness.github_api_call", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = "new_head_sha_0000000000000000000000000000000"

            result = await is_stale_for_pr(
                pull_request=mock_pull_request,
                webhook_sha="old_sha_000000000000000000000000000000000000",
                logger=logger,
                log_prefix="[TEST]",
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_stale_logs_info_message(self, mock_pull_request: Mock, logger: Mock) -> None:
        """When stale, an info log is emitted with both SHAs."""
        with patch("webhook_server.utils.staleness.github_api_call", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = "new_sha_000000000000000000000000000000000000"

            await is_stale_for_pr(
                pull_request=mock_pull_request,
                webhook_sha="old_sha_000000000000000000000000000000000000",
                logger=logger,
                log_prefix="[TEST]",
            )

        logger.info.assert_called_once()
        call_args = logger.info.call_args
        assert "old_sha" in str(call_args)
        assert "new_sha" in str(call_args)

    @pytest.mark.asyncio
    async def test_not_stale_does_not_log(self, mock_pull_request: Mock, logger: Mock) -> None:
        """When not stale, no info log is emitted."""
        with patch("webhook_server.utils.staleness.github_api_call", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = "same_sha_0000000000000000000000000000000000000"

            await is_stale_for_pr(
                pull_request=mock_pull_request,
                webhook_sha="same_sha_0000000000000000000000000000000000000",
                logger=logger,
                log_prefix="[TEST]",
            )

        logger.info.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_call_receives_correct_args(self, mock_pull_request: Mock, logger: Mock) -> None:
        """github_api_call is called with logger and log_prefix."""
        with patch("webhook_server.utils.staleness.github_api_call", new_callable=AsyncMock) as mock_api:
            mock_api.return_value = "sha_0000000000000000000000000000000000000000"

            await is_stale_for_pr(
                pull_request=mock_pull_request,
                webhook_sha="sha_0000000000000000000000000000000000000000",
                logger=logger,
                log_prefix="[TEST]",
            )

        mock_api.assert_called_once()
        _, kwargs = mock_api.call_args
        assert kwargs["logger"] is logger
        assert kwargs["log_prefix"] == "[TEST]"


# ---------------------------------------------------------------------------
# MergeCheckDebouncer
# ---------------------------------------------------------------------------


class TestMergeCheckDebouncer:
    """Tests for the MergeCheckDebouncer coalescing mechanism."""

    @pytest.fixture
    def logger(self) -> Mock:
        return Mock()

    @pytest.mark.asyncio
    async def test_single_call_executes_after_window(self, logger: Mock) -> None:
        """A single scheduled callback fires after the debounce window."""
        debouncer = MergeCheckDebouncer(window=0.1)
        callback = AsyncMock()

        task = asyncio.create_task(
            debouncer.schedule(
                repo_full_name="org/test-repo",
                pr_number=42,
                callback=callback,
                logger=logger,
                log_prefix="[TEST]",
            )
        )

        # Not called immediately
        await asyncio.sleep(0.02)
        callback.assert_not_called()

        # Wait for schedule to complete
        await task
        callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rapid_calls_coalesce_to_last(self, logger: Mock) -> None:
        """Multiple rapid calls for the same PR only execute the last one."""
        debouncer = MergeCheckDebouncer(window=0.15)
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        callback3 = AsyncMock()

        task1 = asyncio.create_task(
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=42, callback=callback1, logger=logger, log_prefix="[TEST]"
            )
        )
        task2 = asyncio.create_task(
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=42, callback=callback2, logger=logger, log_prefix="[TEST]"
            )
        )
        task3 = asyncio.create_task(
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=42, callback=callback3, logger=logger, log_prefix="[TEST]"
            )
        )

        # Wait for all to complete
        await asyncio.gather(task1, task2, task3)

        # Only the last callback should have been called
        callback1.assert_not_awaited()
        callback2.assert_not_awaited()
        callback3.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_different_prs_are_independent(self, logger: Mock) -> None:
        """Calls for different PRs are debounced independently."""
        debouncer = MergeCheckDebouncer(window=0.1)
        callback_pr1 = AsyncMock()
        callback_pr2 = AsyncMock()

        await asyncio.gather(
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=1, callback=callback_pr1, logger=logger, log_prefix="[TEST]"
            ),
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=2, callback=callback_pr2, logger=logger, log_prefix="[TEST]"
            ),
        )

        callback_pr1.assert_awaited_once()
        callback_pr2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_different_repos_are_independent(self, logger: Mock) -> None:
        """Same PR number in different repos are debounced independently."""
        debouncer = MergeCheckDebouncer(window=0.1)
        callback_repo1 = AsyncMock()
        callback_repo2 = AsyncMock()

        await asyncio.gather(
            debouncer.schedule(
                repo_full_name="org/repo-a",
                pr_number=42,
                callback=callback_repo1,
                logger=logger,
                log_prefix="[TEST]",
            ),
            debouncer.schedule(
                repo_full_name="org/repo-b",
                pr_number=42,
                callback=callback_repo2,
                logger=logger,
                log_prefix="[TEST]",
            ),
        )

        callback_repo1.assert_awaited_once()
        callback_repo2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_superseded_call_logs_debug(self, logger: Mock) -> None:
        """When a pending call is superseded, a debug log is emitted."""
        debouncer = MergeCheckDebouncer(window=0.2)
        callback1 = AsyncMock()
        callback2 = AsyncMock()

        await asyncio.gather(
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=42, callback=callback1, logger=logger, log_prefix="[TEST]"
            ),
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=42, callback=callback2, logger=logger, log_prefix="[TEST]"
            ),
        )

        # The second schedule should have logged a debug message about cancellation
        logger.debug.assert_called()
        debug_calls = [str(c) for c in logger.debug.call_args_list]
        assert any("cancelled" in call.lower() or "superseded" in call.lower() for call in debug_calls)

    @pytest.mark.asyncio
    async def test_pending_cleared_after_execution(self, logger: Mock) -> None:
        """After callback executes, the PR is removed from pending."""
        debouncer = MergeCheckDebouncer(window=0.05)
        callback = AsyncMock()

        await debouncer.schedule(
            repo_full_name="org/test-repo", pr_number=42, callback=callback, logger=logger, log_prefix="[TEST]"
        )

        # After execution, pending should be empty
        assert ("org/test-repo", 42) not in debouncer._pending

    @pytest.mark.asyncio
    async def test_execution_logs_info(self, logger: Mock) -> None:
        """When the debounced callback fires, an info log is emitted."""
        debouncer = MergeCheckDebouncer(window=0.05)
        callback = AsyncMock()

        await debouncer.schedule(
            repo_full_name="org/test-repo", pr_number=42, callback=callback, logger=logger, log_prefix="[TEST]"
        )

        logger.info.assert_called()
        info_calls = [str(c) for c in logger.info.call_args_list]
        assert any("merge check" in call.lower() or "pr #42" in call.lower() for call in info_calls)

    @pytest.mark.asyncio
    async def test_callback_exception_propagates(self, logger: Mock) -> None:
        """If the callback raises, the exception propagates to the caller."""
        debouncer = MergeCheckDebouncer(window=0.05)
        failing_callback = AsyncMock(side_effect=RuntimeError("merge check failed"))

        with pytest.raises(RuntimeError, match="merge check failed"):
            await debouncer.schedule(
                repo_full_name="org/test-repo",
                pr_number=42,
                callback=failing_callback,
                logger=logger,
                log_prefix="[TEST]",
            )

        # Debouncer still works for subsequent calls
        good_callback = AsyncMock()
        await debouncer.schedule(
            repo_full_name="org/test-repo", pr_number=42, callback=good_callback, logger=logger, log_prefix="[TEST]"
        )
        good_callback.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_custom_window(self, logger: Mock) -> None:
        """Custom window is respected."""
        debouncer = MergeCheckDebouncer(window=0.3)
        callback = AsyncMock()

        task = asyncio.create_task(
            debouncer.schedule(
                repo_full_name="org/test-repo", pr_number=42, callback=callback, logger=logger, log_prefix="[TEST]"
            )
        )

        # After 0.15s, not yet called (window is 0.3s)
        await asyncio.sleep(0.15)
        callback.assert_not_awaited()

        # Wait for schedule to complete
        await task
        callback.assert_awaited_once()
