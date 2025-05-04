import yaml

import pytest
from webhook_server.tests.conftest import ContentFile, Tree
from webhook_server.utils.constants import APPROVED_BY_LABEL_PREFIX


class Repository:
    def __init__(self):
        self.name = "test-repo"

    def get_git_tree(self, sha: str, recursive: bool):
        return Tree("")

    def get_contents(self, path: str, ref):
        owners_data = yaml.dump({
            "approvers": ["root_approver1", "root_approver2"],
            "reviewers": ["root_reviewer1", "root_reviewer2"],
        })

        folder1_owners_data = yaml.dump({
            "approvers": ["folder1_approver1", "folder1_approver2"],
            "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
        })

        folder4_owners_data = yaml.dump({
            "approvers": ["folder4_approver1", "folder4_approver2"],
            "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
        })

        folder5_owners_data = yaml.dump({
            "root-approvers": False,
            "approvers": ["folder5_approver1", "folder5_approver2"],
            "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
        })
        if path == "OWNERS":
            return ContentFile(owners_data)

        elif path == "folder1/OWNERS":
            return ContentFile(folder1_owners_data)

        elif path == "folder2/OWNERS":
            return ContentFile(yaml.dump({}))

        elif path == "folder/folder4/OWNERS":
            return ContentFile(folder4_owners_data)

        elif path == "folder":
            return ContentFile(yaml.dump({}))

        elif path == "folder5/OWNERS":
            return ContentFile(folder5_owners_data)


@pytest.fixture(scope="function")
def all_approvers_and_reviewers(process_github_webhook):
    process_github_webhook.all_approvers_and_reviewers = {
        ".": {"approvers": ["root_approver1", "root_approver2"], "reviewers": ["root_reviewer1", "root_reviewer2"]},
        "folder1": {
            "approvers": ["folder1_approver1", "folder1_approver2"],
            "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
        },
        "folder2": {},
        "folder/folder4": {
            "approvers": ["folder4_approver1", "folder4_approver2"],
            "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
        },
        "folder5": {
            "approvers": ["folder5_approver1", "folder5_approver2"],
            "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
            "root-approvers": False,
        },
    }


@pytest.fixture(scope="function")
def all_approvers_reviewers(process_github_webhook):
    process_github_webhook.all_approvers = [
        "folder1_approver1",
        "folder1_approver2",
        "folder4_approver1",
        "folder4_approver2",
        "folder5_approver1",
        "folder5_approver2",
        "root_approver1",
        "root_approver2",
    ]

    process_github_webhook.all_approvers.sort()

    process_github_webhook.all_reviewers = [
        "folder1_reviewer1",
        "folder1_reviewer2",
        "folder4_reviewer1",
        "folder4_reviewer2",
        "folder5_reviewer1",
        "folder5_reviewer2",
        "root_reviewer1",
        "root_reviewer2",
    ]

    process_github_webhook.all_reviewers.sort()


def test_get_all_approvers_and_reviewers(process_github_webhook, all_approvers_and_reviewers):
    process_github_webhook.repository = Repository()
    read_owners_result = process_github_webhook.get_all_approvers_and_reviewers()
    assert read_owners_result == process_github_webhook.all_approvers_and_reviewers


def test_owners_data_for_changed_files(process_github_webhook, all_approvers_and_reviewers):
    owners_data_chaged_files_result = process_github_webhook.owners_data_for_changed_files()
    owners_data_chaged_files_expected = {
        "folder5": {
            "approvers": ["folder5_approver1", "folder5_approver2"],
            "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
            "root-approvers": False,
        },
        "folder1": {
            "approvers": ["folder1_approver1", "folder1_approver2"],
            "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
        },
        ".": {"approvers": ["root_approver1", "root_approver2"], "reviewers": ["root_reviewer1", "root_reviewer2"]},
        "folder2": {},
        "folder/folder4": {
            "approvers": ["folder4_approver1", "folder4_approver2"],
            "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
        },
    }

    assert owners_data_chaged_files_result == owners_data_chaged_files_expected


def test_all_approvers_reviewers(process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers):
    all_approvers = process_github_webhook.get_all_approvers()
    assert all_approvers == process_github_webhook.all_approvers

    all_reviewers = process_github_webhook.get_all_reviewers()
    assert all_reviewers == process_github_webhook.all_reviewers


def test_check_pr_approved(process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()

    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}root_approver2",
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver2",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver2",
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver2",
        ]
    )
    assert check_if_pr_approved == ""


def test_check_pr_minimum_approved(process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""


def test_check_pr_not_approved(process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "folder1_approver1",
        "folder1_approver2",
        "folder4_approver1",
        "folder4_approver2",
        "folder5_approver1",
        "folder5_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


def test_check_pr_partial_approved(process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}root_approver2",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "folder1_approver1",
        "folder1_approver2",
        "folder4_approver1",
        "folder4_approver2",
        "folder5_approver1",
        "folder5_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "file_in_root",
                "folder2",
                "folder/folder4/another_file.txt",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_specific_folder(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "file_in_root",
                "folder2",
                "folder/another_file.txt",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_nested_folder_no_owners(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder1/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_specific_folder_with_root_approvers(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver1",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "root_approver1",
        "root_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder5/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_specific_folder_no_root_approvers(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder_with_no_owners/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_not_approved_specific_folder_without_owners(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "root_approver1",
        "root_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder_with_no_owners/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_specific_folder_without_owners(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder_with_no_owners/file",
                "folder5/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_folder_with_no_owners_and_folder_without_root_approvers(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder_with_no_owners/file",
                "folder5/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_not_approved_folder_with_no_owners_and_folder_without_root_approvers(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "root_approver1",
        "root_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder1/sub_folder1/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_not_approved_subfolder_with_owners(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "folder1_approver1",
        "folder1_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.parametrize(
    "process_github_webhook",
    [
        pytest.param([
            [
                "folder5/sub_folder5/file",
            ]
        ])
    ],
    indirect=True,
)
def test_check_pr_approved_subfolder_with_owners_no_root_approvers(
    process_github_webhook, all_approvers_and_reviewers, all_approvers_reviewers
):
    process_github_webhook.all_approvers = process_github_webhook.get_all_approvers()
    check_if_pr_approved = process_github_webhook._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""
