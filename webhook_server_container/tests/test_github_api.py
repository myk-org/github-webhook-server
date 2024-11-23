import pytest
from starlette.datastructures import Headers

from simple_logger.logger import logging
from stringcolor.ops import os
import yaml
from webhook_server_container.libs.github_api import ProcessGithubWehook
from webhook_server_container.utils.constants import APPROVED_BY_LABEL_PREFIX, SIZE_LABEL_PREFIX


class Label:
    def __init__(self, name: str):
        self.name = name


class Tree:
    def __init__(self, path: str):
        self.type = "blob"
        self.path = path

    @property
    def tree(self):
        trees = []
        for _path in ["OWNERS", "test1/OWNERS", "code/file.py", "README.md"]:
            trees.append(Tree(_path))
        return trees


class ContentFile:
    def __init__(self, content: str):
        self.content = content

    @property
    def decoded_content(self):
        return self.content


class Repository:
    def __init__(self):
        self.name = "test-repo"

    def get_git_tree(self, sha: str, recursive: bool):
        return Tree("")

    def get_contents(self, path: str):
        owners_data = yaml.dump({"approvers": ["approver1", "approver2"], "reviewers": ["reviewer1", "reviewer2"]})

        test1_owners_data = yaml.dump({
            "approvers": ["approver3", "approver4"],
            "reviewers": ["reviewer3", "reviewer4"],
        })

        if path == "OWNERS":
            return ContentFile(owners_data)
        elif path == "test1/OWNERS":
            return ContentFile(test1_owners_data)


class PullRequest:
    def __init__(self, additions: int, deletions: int, labels: list[str] | None = None):
        self.additions = additions
        self.deletions = deletions
        self.labels = labels or []

    @property
    def lables(self) -> list[Label]:
        _lables = []
        for label in self.labels:
            _lables.append(Label(label))

        return _lables


@pytest.fixture(scope="function")
def process_github_webhook(mocker):
    base_import_path = "webhook_server_container.libs.github_api"
    os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server_container/tests/manifests"

    mocker.patch(f"{base_import_path}.get_repository_github_app_api", return_value=True)
    mocker.patch("github.AuthenticatedUser", return_value=True)
    mocker.patch(f"{base_import_path}.get_api_with_highest_rate_limit", return_value=("API", "TOKEN"))
    mocker.patch(f"{base_import_path}.get_github_repo_api", return_value=Repository())

    process_github_webhook = ProcessGithubWehook(
        {"repository": {"name": Repository().name}}, Headers({"X-GitHub-Event": "test-event"}), logging.getLogger()
    )
    process_github_webhook.pull_request_branch = "main"
    process_github_webhook.changed_files = ["OWNERS", "test1/OWNERS", "code/file.py", "README.md"]
    return process_github_webhook


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


def test_get_approvers_and_reviewers(mocker, process_github_webhook):
    process_github_webhook.repository = Repository()
    read_owners_result = process_github_webhook.get_approvers_and_reviewers()
    process_github_webhook.approvers_and_reviewers = {
        ".": {"approvers": ["approver1", "approver2"], "reviewers": ["reviewer1", "reviewer2"]},
        "test1": {"approvers": ["approver3", "approver4"], "reviewers": ["reviewer3", "reviewer4"]},
    }
    assert read_owners_result == process_github_webhook.approvers_and_reviewers

    owners_data_chaged_files_result = process_github_webhook.owners_data_for_changed_files()
    owners_data_chaged_files_expected = {
        "approvers": [
            ["approver1", "approver2"],
            ["approver1", "approver2"],
            ["approver3", "approver4"],
            ["approver1", "approver2"],
        ],
        "reviewers": [
            ["reviewer1", "reviewer2"],
            ["reviewer1", "reviewer2"],
            ["reviewer3", "reviewer4"],
            ["reviewer1", "reviewer2"],
        ],
    }
    # owners_data_chaged_files_result["approvers"].sort()
    # owners_data_chaged_files_result["reviewers"].sort()
    owners_data_chaged_files_expected["approvers"].sort()
    owners_data_chaged_files_expected["reviewers"].sort()
    assert owners_data_chaged_files_result == owners_data_chaged_files_expected

    all_approvers = process_github_webhook.get_all_approvers()
    # all_approvers.sort()
    assert all_approvers == ["approver1", "approver2", "approver3", "approver4"]
    process_github_webhook.all_approvers = all_approvers

    all_reviewers = process_github_webhook.get_all_reviewers()
    # all_reviewers.sort()
    assert all_reviewers == ["reviewer1", "reviewer2", "reviewer3", "reviewer4"]
    process_github_webhook.all_reviewers = all_reviewers

    pr_approved_all_result = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}approver1",
            f"{APPROVED_BY_LABEL_PREFIX}approver2",
            f"{APPROVED_BY_LABEL_PREFIX}approver3",
            f"{APPROVED_BY_LABEL_PREFIX}approver4",
        ]
    )
    assert pr_approved_all_result == ""

    pr_approved_minimum_result = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}approver1",
            f"{APPROVED_BY_LABEL_PREFIX}approver3",
        ]
    )
    assert pr_approved_minimum_result == ""

    pr_not_approved_result = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}approver1",
        ]
    )
    assert pr_not_approved_result == "Missing lgtm/approved from approvers: approver2, approver3, approver4\n"
