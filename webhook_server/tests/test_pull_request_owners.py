import pytest
import yaml

from webhook_server.libs.pull_request_handler import PullRequestHandler
from webhook_server.tests.conftest import ContentFile, Tree
from webhook_server.utils.constants import APPROVED_BY_LABEL_PREFIX

ALL_CHANGED_FILES = [
    "OWNERS",
    "folder1/OWNERS",
    "code/file.py",
    "README.md",
    "folder2/lib.py",
    "folder/folder4/another_file.txt",
    "folder5/file",
]


class Repository:
    def __init__(self):
        self.name = "test-repo"

    def get_git_tree(self, sha: str, recursive: bool):
        return Tree("")

    def get_contents(self, path: str, ref: str):
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
def changed_files(request, owners_file_handler):
    if hasattr(request, "param") and request.param:
        owners_file_handler.changed_files = request.param[0]

    else:
        owners_file_handler.changed_files = ALL_CHANGED_FILES


@pytest.fixture(scope="function")
def all_repository_approvers_and_reviewers(owners_file_handler):
    owners_file_handler.all_repository_approvers_and_reviewers = {
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
def all_approvers_reviewers(owners_file_handler):
    owners_file_handler.all_pull_request_approvers = [
        "folder1_approver1",
        "folder1_approver2",
        "folder4_approver1",
        "folder4_approver2",
        "folder5_approver1",
        "folder5_approver2",
        "root_approver1",
        "root_approver2",
    ]

    owners_file_handler.all_pull_request_approvers.sort()

    owners_file_handler.all_pull_request_reviewers = [
        "folder1_reviewer1",
        "folder1_reviewer2",
        "folder4_reviewer1",
        "folder4_reviewer2",
        "folder5_reviewer1",
        "folder5_reviewer2",
        "root_reviewer1",
        "root_reviewer2",
    ]

    owners_file_handler.all_pull_request_reviewers.sort()


@pytest.mark.asyncio
async def test_get_all_repository_approvers_and_reviewers(
    changed_files, process_github_webhook, owners_file_handler, pull_request, all_repository_approvers_and_reviewers
):
    process_github_webhook.repository = Repository()
    read_owners_result = await owners_file_handler.get_all_repository_approvers_and_reviewers(pull_request=pull_request)
    assert read_owners_result == owners_file_handler.all_repository_approvers_and_reviewers


@pytest.mark.asyncio
async def test_owners_data_for_changed_files(
    changed_files, process_github_webhook, owners_file_handler, all_repository_approvers_and_reviewers
):
    owners_data_changed_files_result = await owners_file_handler.owners_data_for_changed_files()
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

    assert owners_data_changed_files_result == owners_data_chaged_files_expected


@pytest.mark.asyncio
async def test_all_approvers_reviewers(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    all_approvers = await owners_file_handler.get_all_pull_request_approvers()
    assert all_approvers == owners_file_handler.all_pull_request_approvers

    all_pull_request_reviewers = await owners_file_handler.get_all_pull_request_reviewers()
    assert all_pull_request_reviewers == owners_file_handler.all_pull_request_reviewers


@pytest.mark.asyncio
async def test_check_pr_approved(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook,
        owners_file_handler=owners_file_handler,
    )
    process_github_webhook.parent_committer = "test"
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
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


@pytest.mark.asyncio
async def test_check_pr_minimum_approved(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook,
        owners_file_handler=owners_file_handler,
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.asyncio
async def test_check_pr_not_approved(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook,
        owners_file_handler=owners_file_handler,
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver1",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "root_approver1",
        "root_approver2",
        "folder4_approver1",
        "folder4_approver2",
        "folder5_approver1",
        "folder5_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.asyncio
async def test_check_pr_partial_approved(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder1_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver2",
        ]
    )
    missing_approvers = [appr.strip() for appr in check_if_pr_approved.split(":")[-1].strip().split(",")]
    missing_approvers.sort()
    expected_approvers = [
        "root_approver1",
        "root_approver2",
        "folder5_approver1",
        "folder5_approver2",
    ]
    expected_approvers.sort()
    assert missing_approvers == expected_approvers


@pytest.mark.parametrize(
    "changed_files",
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
@pytest.mark.asyncio
async def test_check_pr_approved_specific_folder(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder4_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "changed_files",
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
@pytest.mark.asyncio
async def test_check_pr_approved_nested_folder_no_owners(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "changed_files",
    [
        pytest.param([
            [
                "folder1/file",
            ]
        ])
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_check_pr_approved_specific_folder_with_root_approvers(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
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
    "changed_files",
    [
        pytest.param([
            [
                "folder5/file",
            ]
        ])
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_check_pr_approved_specific_folder_no_root_approvers(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "changed_files",
    [
        pytest.param([
            [
                "folder_with_no_owners/file",
            ]
        ])
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_check_pr_not_approved_specific_folder_without_owners(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
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
    "changed_files",
    [
        pytest.param([
            [
                "folder_with_no_owners/file",
            ]
        ])
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_check_pr_approved_specific_folder_without_owners(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "changed_files",
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
@pytest.mark.asyncio
async def test_check_pr_approved_folder_with_no_owners_and_folder_without_root_approvers(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}root_approver1",
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""


@pytest.mark.parametrize(
    "changed_files",
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
@pytest.mark.asyncio
async def test_check_pr_not_approved_folder_with_no_owners_and_folder_without_root_approvers(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
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
    "changed_files",
    [
        pytest.param([
            [
                "folder5/sub_folder5/file",
            ]
        ])
    ],
    indirect=True,
)
@pytest.mark.asyncio
async def test_check_pr_approved_subfolder_with_owners_no_root_approvers(
    changed_files,
    process_github_webhook,
    owners_file_handler,
    all_repository_approvers_and_reviewers,
    all_approvers_reviewers,
):
    owners_file_handler.all_pull_request_approvers = await owners_file_handler.get_all_pull_request_approvers()
    pull_request_handler = PullRequestHandler(
        github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
    )
    check_if_pr_approved = await pull_request_handler._check_if_pr_approved(
        labels=[
            f"{APPROVED_BY_LABEL_PREFIX}folder5_approver1",
        ]
    )
    assert check_if_pr_approved == ""
