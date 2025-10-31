import pytest

from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.utils.constants import SIZE_LABEL_PREFIX


@pytest.mark.parametrize(
    "additions, deletions, expected_label",
    [
        (0, 0, "XS"),
        (18, 1, "XS"),
        (48, 1, "S"),
        (98, 1, "M"),
        (298, 1, "L"),
        (498, 1, "XL"),
        (1000, 1, "XXL"),
    ],
)
def test_get_size_thresholds(process_github_webhook, owners_file_handler, additions, deletions, expected_label):
    # Create a PullRequestWrapper with the necessary data
    webhook_data = {
        "node_id": "PR_test",
        "number": 123,
        "title": "Test PR",
        "body": "",
        "state": "open",
        "merged": False,
        "mergeable": True,
        "html_url": "https://github.com/test-owner/test-repo/pull/123",
        "additions": additions,
        "deletions": deletions,
        "user": {"login": "test-user"},
        "base": {"ref": "main", "sha": "abc123", "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"}},
        "head": {"ref": "feature", "sha": "def456", "repo": {"owner": {"login": "test-owner"}, "name": "test-repo"}},
    }
    pull_request = PullRequestWrapper("test-owner", "test-repo", webhook_data)
    lables_handler = LabelsHandler(github_webhook=process_github_webhook, owners_file_handler=owners_file_handler)
    result = lables_handler.get_size(pull_request=pull_request)

    assert result == f"{SIZE_LABEL_PREFIX}{expected_label}"
