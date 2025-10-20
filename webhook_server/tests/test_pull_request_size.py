import pytest

from webhook_server.libs.graphql.graphql_wrappers import PullRequestWrapper
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
    pr_data = {
        "id": "PR_test",
        "number": 123,
        "title": "Test PR",
        "body": "",
        "state": "OPEN",
        "createdAt": "2024-01-01T00:00:00Z",
        "updatedAt": "2024-01-01T00:00:00Z",
        "closedAt": None,
        "mergedAt": None,
        "merged": False,
        "mergeable": "MERGEABLE",
        "permalink": "https://github.com/test/repo/pull/123",
        "additions": additions,
        "deletions": deletions,
        "author": {"login": "test-user"},
        "baseRef": {"name": "main", "target": {"oid": "abc123"}},
        "headRef": {"name": "feature", "target": {"oid": "def456"}},
    }
    pull_request = PullRequestWrapper(pr_data)
    lables_handler = LabelsHandler(github_webhook=process_github_webhook, owners_file_handler=owners_file_handler)
    result = lables_handler.get_size(pull_request=pull_request)

    assert result == f"{SIZE_LABEL_PREFIX}{expected_label}"
