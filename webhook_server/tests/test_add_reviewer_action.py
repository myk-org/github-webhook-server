import logging


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


def test_add_reviewer_by_user_comment(caplog, process_github_webhook):
    process_github_webhook.repository = Repository()
    process_github_webhook.pull_request = PullRequest()
    process_github_webhook._add_reviewer_by_user_comment("user1")
    caplog.set_level(logging.DEBUG)
    assert "Adding reviewer user1 by user comment" in caplog.text


def test_add_reviewer_by_user_comment_invalid_user(caplog, process_github_webhook):
    process_github_webhook.repository = Repository()
    process_github_webhook.pull_request = PullRequest()
    process_github_webhook._add_reviewer_by_user_comment("user2")
    caplog.set_level(logging.DEBUG)
    assert "not adding reviewer user2 by user comment, user2 is not part of contributers" in caplog.text
