import logging

import pytest

from webhook_server.libs.issue_comment_handler import IssueCommentHandler


class User:
    def __init__(self, username):
        self.login = username


class Repository:
    def __init__(self):
        self.name = "test-repo"

    def get_contributors(self):
        return [User("user1")]


class PullRequest:
    def __init__(self):
        pass

    def create_issue_comment(self, _):
        return

    def create_review_request(self, _):
        return


@pytest.mark.asyncio
async def test_add_reviewer_by_user_comment(caplog, process_github_webhook, owners_file_handler, pull_request):
    # Set log level BEFORE the action
    caplog.set_level(logging.DEBUG)

    process_github_webhook.repository = Repository()
    process_github_webhook.pull_request = PullRequest()
    issue_comment_handler = IssueCommentHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    await issue_comment_handler._add_reviewer_by_user_comment(pull_request=pull_request, reviewer="user1")
    assert "Adding reviewer user1 by user comment" in caplog.text


@pytest.mark.asyncio
async def test_add_reviewer_by_user_comment_invalid_user(
    caplog, process_github_webhook, owners_file_handler, pull_request
):
    # Set log level BEFORE the action
    caplog.set_level(logging.DEBUG)

    process_github_webhook.repository = Repository()
    process_github_webhook.pull_request = PullRequest()
    issue_comment_handler = IssueCommentHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    await issue_comment_handler._add_reviewer_by_user_comment(pull_request=pull_request, reviewer="user2")
    assert "not adding reviewer user2 by user comment, user2 is not part of contributers" in caplog.text
