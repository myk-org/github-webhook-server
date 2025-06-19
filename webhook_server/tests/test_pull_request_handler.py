import pytest
from unittest.mock import AsyncMock, Mock

from webhook_server.libs.pull_request_handler import PullRequestHandler


class TestCreateIssueForNewPR:
    """Test the create-issue-for-new-pr configuration option."""

    @pytest.mark.asyncio
    async def test_create_issue_when_enabled(self) -> None:
        """Test that issue is created when create-issue-for-new-pr is enabled."""
        # Mock github_webhook
        mock_webhook = Mock()
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.logger = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.create_issue = AsyncMock()

        # Mock owners_file_handler
        mock_owners_handler = Mock()

        # Mock pull request
        mock_pr = Mock()
        mock_pr.title = "Test PR"
        mock_pr.number = 123
        mock_pr.user.login = "testuser"

        # Create handler and test
        handler = PullRequestHandler(mock_webhook, mock_owners_handler)
        await handler.create_issue_for_new_pull_request(mock_pr)

        # Verify issue was created
        mock_webhook.repository.create_issue.assert_called_once()
        call_args = mock_webhook.repository.create_issue.call_args
        assert call_args[1]["title"] == "Test PR - 123"
        assert call_args[1]["body"] == "[Auto generated]\nNumber: [#123]"
        assert call_args[1]["assignee"] == "testuser"

    @pytest.mark.asyncio
    async def test_create_issue_when_disabled(self) -> None:
        """Test that issue is not created when create-issue-for-new-pr is disabled."""
        # Mock github_webhook
        mock_webhook = Mock()
        mock_webhook.create_issue_for_new_pr = False
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.logger = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.create_issue = AsyncMock()

        # Mock owners_file_handler
        mock_owners_handler = Mock()

        # Mock pull request
        mock_pr = Mock()
        mock_pr.title = "Test PR"

        # Create handler and test
        handler = PullRequestHandler(mock_webhook, mock_owners_handler)
        await handler.create_issue_for_new_pull_request(mock_pr)

        # Verify issue was not created
        mock_webhook.repository.create_issue.assert_not_called()
        mock_webhook.logger.info.assert_called_with("[TEST] Issue creation for new PRs is disabled for this repository")

    @pytest.mark.asyncio
    async def test_create_issue_for_auto_verified_user(self) -> None:
        """Test that issue is not created for auto-verified users even when enabled."""
        # Mock github_webhook
        mock_webhook = Mock()
        mock_webhook.create_issue_for_new_pr = True
        mock_webhook.parent_committer = "autouser"
        mock_webhook.auto_verified_and_merged_users = ["autouser"]
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.logger = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.create_issue = AsyncMock()

        # Mock owners_file_handler
        mock_owners_handler = Mock()

        # Mock pull request
        mock_pr = Mock()
        mock_pr.title = "Test PR"

        # Create handler and test
        handler = PullRequestHandler(mock_webhook, mock_owners_handler)
        await handler.create_issue_for_new_pull_request(mock_pr)

        # Verify issue was not created
        mock_webhook.repository.create_issue.assert_not_called()
        mock_webhook.logger.info.assert_called_with(
            "[TEST] Committer autouser is part of ['autouser'], will not create issue."
        )

    @pytest.mark.asyncio
    async def test_create_issue_uses_global_config_when_repo_not_set(self) -> None:
        """Test that global create-issue-for-new-pr setting is used when repository doesn't override it."""
        # Mock github_webhook with global setting
        mock_webhook = Mock()
        mock_webhook.create_issue_for_new_pr = False  # Global setting
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.logger = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.create_issue = AsyncMock()

        # Mock owners_file_handler
        mock_owners_handler = Mock()

        # Mock pull request
        mock_pr = Mock()
        mock_pr.title = "Test PR"

        # Create handler and test
        handler = PullRequestHandler(mock_webhook, mock_owners_handler)
        await handler.create_issue_for_new_pull_request(mock_pr)

        # Verify issue was not created (using global setting)
        mock_webhook.repository.create_issue.assert_not_called()
        mock_webhook.logger.info.assert_called_with("[TEST] Issue creation for new PRs is disabled for this repository")

    @pytest.mark.asyncio
    async def test_create_issue_repo_config_overrides_global(self) -> None:
        """Test that repository-specific create-issue-for-new-pr setting overrides global setting."""
        # Mock github_webhook with repository override
        mock_webhook = Mock()
        mock_webhook.create_issue_for_new_pr = True  # Repository overrides global False
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.logger = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.create_issue = AsyncMock()

        # Mock owners_file_handler
        mock_owners_handler = Mock()

        # Mock pull request
        mock_pr = Mock()
        mock_pr.title = "Test PR"
        mock_pr.number = 123
        mock_pr.user.login = "testuser"

        # Create handler and test
        handler = PullRequestHandler(mock_webhook, mock_owners_handler)
        await handler.create_issue_for_new_pull_request(mock_pr)

        # Verify issue was created (repository setting overrides global)
        mock_webhook.repository.create_issue.assert_called_once()
        call_args = mock_webhook.repository.create_issue.call_args
        assert call_args[1]["title"] == "Test PR - 123"
        assert call_args[1]["body"] == "[Auto generated]\nNumber: [#123]"
        assert call_args[1]["assignee"] == "testuser"

    @pytest.mark.asyncio
    async def test_create_issue_from_github_webhook_server_yaml(self) -> None:
        """Test that create-issue-for-new-pr setting from .github-webhook-server.yaml is used."""
        # Mock github_webhook with .github-webhook-server.yaml setting
        mock_webhook = Mock()
        mock_webhook.create_issue_for_new_pr = False  # From .github-webhook-server.yaml
        mock_webhook.parent_committer = "testuser"
        mock_webhook.auto_verified_and_merged_users = []
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.logger = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.create_issue = AsyncMock()

        # Mock owners_file_handler
        mock_owners_handler = Mock()

        # Mock pull request
        mock_pr = Mock()
        mock_pr.title = "Test PR"

        # Create handler and test
        handler = PullRequestHandler(mock_webhook, mock_owners_handler)
        await handler.create_issue_for_new_pull_request(mock_pr)

        # Verify issue was not created (using .github-webhook-server.yaml setting)
        mock_webhook.repository.create_issue.assert_not_called()
        mock_webhook.logger.info.assert_called_with("[TEST] Issue creation for new PRs is disabled for this repository")
