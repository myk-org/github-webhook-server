import logging
from unittest.mock import AsyncMock

import pytest

from webhook_server.libs.handlers.issue_comment_handler import IssueCommentHandler


class User:
    def __init__(self, username):
        self.login = username


class Repository:
    def __init__(self):
        self.name = "test-repo"
        self.full_name = "my-org/test-repo"

    def get_contributors(self):
        return [User("user1")]


@pytest.mark.asyncio
async def test_add_reviewer_by_user_comment(caplog, process_github_webhook, owners_file_handler, pull_request):
    # Set log level BEFORE the action
    caplog.set_level(logging.DEBUG)

    process_github_webhook.repository = Repository()

    # Mock unified_api to prevent real GraphQL calls
    process_github_webhook.unified_api = AsyncMock()
    process_github_webhook.unified_api.get_user_id.return_value = "U_123"
    process_github_webhook.unified_api.request_reviews.return_value = None
    process_github_webhook.unified_api.get_contributors.return_value = [User("user1")]

    # Mock the request_pr_reviews method that gets called in the review-request path
    process_github_webhook.request_pr_reviews = AsyncMock()

    issue_comment_handler = IssueCommentHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    await issue_comment_handler._add_reviewer_by_user_comment(pull_request=pull_request, reviewer="user1")

    # Assert the review-request path was executed (lines 256-267 in issue_comment_handler.py)
    assert "Adding reviewer user1 by user comment" in caplog.text
    assert "Repo contributors are:" in caplog.text

    # Assert that request_pr_reviews was called with correct arguments
    # This verifies the review-request logic at line 267 was executed
    process_github_webhook.request_pr_reviews.assert_called_once()
    call_args = process_github_webhook.request_pr_reviews.call_args
    # Verify the PullRequestWrapper and reviewer list were passed as positional arguments
    pr_wrapper_arg = call_args.args[0]  # First positional argument is PullRequestWrapper
    reviewers_arg = call_args.args[1]  # Second positional argument is reviewers list
    assert reviewers_arg == ["user1"]
    # Verify PullRequestWrapper has the correct node ID from the fixture
    assert pr_wrapper_arg.id == "PR_kgDOTestId"


@pytest.mark.asyncio
async def test_add_reviewer_by_user_comment_invalid_user(
    caplog, process_github_webhook, owners_file_handler, pull_request
):
    # Set log level BEFORE the action
    caplog.set_level(logging.DEBUG)

    process_github_webhook.repository = Repository()

    # Mock unified_api to prevent real GraphQL calls
    process_github_webhook.unified_api = AsyncMock()
    process_github_webhook.unified_api.get_contributors.return_value = [User("user1")]

    issue_comment_handler = IssueCommentHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    await issue_comment_handler._add_reviewer_by_user_comment(pull_request=pull_request, reviewer="user2")
    assert "not adding reviewer user2 by user comment, user2 is not part of contributors" in caplog.text
