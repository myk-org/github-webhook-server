import pytest

from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.tests.conftest import PullRequest
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
    pull_request = PullRequest(additions=additions, deletions=deletions)
    lables_handler = LabelsHandler(github_webhook=process_github_webhook, owners_file_handler=owners_file_handler)
    result = lables_handler.get_size(pull_request=pull_request)

    assert result == f"{SIZE_LABEL_PREFIX}{expected_label}"
