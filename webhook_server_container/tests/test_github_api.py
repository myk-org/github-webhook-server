import pytest
from starlette.datastructures import Headers

from simple_logger.logger import logging
from stringcolor.ops import os
from webhook_server_container.libs.github_api import ProcessGithubWehook
from webhook_server_container.utils.constants import SIZE_LABEL_PREFIX


class Repository:
    def __init__(self):
        self.name = "test-repo"


class PullRequest:
    def __init__(self, additions: int, deletions: int):
        self.additions = additions
        self.deletions = deletions


@pytest.fixture(scope="function")
def process_github_webhook(mocker):
    base_import_path = "webhook_server_container.libs.github_api"
    os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server_container/tests/manifests"

    mocker.patch(f"{base_import_path}.get_repository_github_app_api", return_value=True)
    mocker.patch("github.AuthenticatedUser", return_value=True)
    mocker.patch(f"{base_import_path}.get_api_with_highest_rate_limit", return_value=("API", "TOKEN"))
    mocker.patch(f"{base_import_path}.get_github_repo_api", return_value=Repository())

    return ProcessGithubWehook(
        {"repository": {"name": Repository().name}}, Headers({"X-GitHub-Event": "test-event"}), logging.getLogger()
    )


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
def test_get_size_thresholds(process_github_webhook, additions, deletions, expected_label):
    process_github_webhook.pull_request = PullRequest(additions=additions, deletions=deletions)
    result = process_github_webhook.get_size()

    assert result == f"{SIZE_LABEL_PREFIX}{expected_label}"
