import os

import pytest
import yaml
from simple_logger.logger import logging
from starlette.datastructures import Headers

from webhook_server.libs.owners_files_handler import OwnersFileHandler

os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server/tests/manifests"
from webhook_server.libs.github_api import GithubWebhook


class Tree:
    def __init__(self, path: str):
        self.type = "blob"
        self.path = path

    @property
    def tree(self):
        trees = []
        for _path in [
            "OWNERS",
            "folder1/OWNERS",
            "folder2/OWNERS",
            "folder/folder4/OWNERS",
            "folder5/OWNERS",
        ]:
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
        self.full_name = "my-org/test-repo"

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


class Label:
    def __init__(self, name: str):
        self.name = name


class PullRequest:
    def __init__(self, additions: int | None = None, deletions: int | None = None):
        self.additions = additions
        self.deletions = deletions

    class base:
        ref = "refs/heads/main"

    def create_issue_comment(self, *args, **kwargs): ...

    def create_review_request(self, *args, **kwargs): ...

    def get_files(self): ...


@pytest.fixture(scope="function")
def pull_request():
    return PullRequest()


@pytest.fixture(scope="function")
def github_webhook(mocker, request):
    base_import_path = "webhook_server.libs.github_api"

    mocker.patch(f"{base_import_path}.get_repository_github_app_api", return_value=True)
    mocker.patch("github.AuthenticatedUser", return_value=True)
    mocker.patch(f"{base_import_path}.get_api_with_highest_rate_limit", return_value=("API", "TOKEN", "USER"))
    mocker.patch(f"{base_import_path}.get_github_repo_api", return_value=Repository())
    mocker.patch(f"{base_import_path}.GithubWebhook.add_api_users_to_auto_verified_and_merged_users", return_value=None)

    process_github_webhook = GithubWebhook(
        hook_data={"repository": {"name": Repository().name, "full_name": Repository().full_name}},
        headers=Headers({"X-GitHub-Event": "test-event"}),
        logger=logging.getLogger(),
    )
    owners_file_handler = OwnersFileHandler(github_webhook=process_github_webhook)

    return process_github_webhook, owners_file_handler


@pytest.fixture(scope="function")
def process_github_webhook(github_webhook):
    return github_webhook[0]


@pytest.fixture(scope="function")
def owners_file_handler(github_webhook):
    return github_webhook[1]
