from unittest import mock

from webhook_server_container.libs.github_api import GitHubApi


def test_process_hook():
    """
    Test the process_hook function of the GitHubApi class.
    This function mocks the process_comment_webhook_data method and verifies that it is called once.
    """
    with mock.patch.object(GitHubApi, "process_comment_webhook_data") as mock_process_comment:
        github_api = GitHubApi(
            hook_data={"action": "issue_comment"}, repositories_app_api={}, missing_app_repositories={}
        )
        github_api.process_hook(data="issue_comment", event_log="test")
        mock_process_comment.assert_called_once()


def test_process_pull_request_webhook_data():
    """
    Test the process_pull_request_webhook_data function of the GitHubApi class.
    This function mocks the process_opened_or_synchronize_pull_request method and verifies that it is called once.
    """
    with mock.patch.object(GitHubApi, "process_opened_or_synchronize_pull_request") as mock_process_opened:
        github_api = GitHubApi(hook_data={"action": "opened"}, repositories_app_api={}, missing_app_repositories={})
        github_api.process_pull_request_webhook_data()
        mock_process_opened.assert_called_once()


def test_process_push_webhook_data():
    """
    Test the process_push_webhook_data function of the GitHubApi class.
    This function mocks the upload_to_pypi method and verifies that it is called once.
    """
    with mock.patch.object(GitHubApi, "upload_to_pypi") as mock_upload:
        github_api = GitHubApi(
            hook_data={"ref": "refs/tags/test"}, repositories_app_api={}, missing_app_repositories={}
        )
        github_api.process_push_webhook_data()
        mock_upload.assert_called_once()


def test_process_pull_request_review_webhook_data():
    """
    Test the process_pull_request_review_webhook_data function of the GitHubApi class.
    This function mocks the manage_reviewed_by_label method and verifies that it is called once.
    """
    with mock.patch.object(GitHubApi, "manage_reviewed_by_label") as mock_manage_review:
        github_api = GitHubApi(hook_data={"action": "submitted"}, repositories_app_api={}, missing_app_repositories={})
        github_api.process_pull_request_review_webhook_data()
        mock_manage_review.assert_called_once()


def test_check_if_can_be_merged():
    """
    Test the check_if_can_be_merged function of the GitHubApi class.

    This function mocks the set_merge_check_queued method and verifies that it is called once.

    Parameters:
        None

    Returns:
        None
    """
    with mock.patch.object(GitHubApi, "set_merge_check_queued") as mock_set_merge:
        github_api = GitHubApi(hook_data={}, repositories_app_api={}, missing_app_repositories={})
        github_api.check_if_can_be_merged()
        mock_set_merge.assert_called_once()
