import os

import yaml
from simple_logger.logger import logging
from starlette.datastructures import Headers

import pytest
from webhook_server.libs.github_api import ProcessGithubWehook

ALL_CHANGED_FILES = [
    "OWNERS",
    "folder1/OWNERS",
    "code/file.py",
    "README.md",
    "folder2/lib.py",
    "folder/folder4/another_file.txt",
    "folder5/file",
]


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

    def get_contents(self, path: str):
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
def process_github_webhook(mocker, request):
    base_import_path = "webhook_server.libs.github_api"
    os.environ["WEBHOOK_SERVER_DATA_DIR"] = "webhook_server/tests/manifests"

    mocker.patch(f"{base_import_path}.get_repository_github_app_api", return_value=True)
    mocker.patch("github.AuthenticatedUser", return_value=True)
    mocker.patch(f"{base_import_path}.get_api_with_highest_rate_limit", return_value=("API", "TOKEN", "USER"))
    mocker.patch(f"{base_import_path}.get_github_repo_api", return_value=Repository())

    process_github_webhook = ProcessGithubWehook(
        hook_data={"repository": {"name": Repository().name, "full_name": Repository().full_name}},
        headers=Headers({"X-GitHub-Event": "test-event"}),
        logger=logging.getLogger(),
    )
    process_github_webhook.pull_request_branch = "main"
    if hasattr(request, "param") and request.param:
        process_github_webhook.changed_files = request.param[0]

    else:
        process_github_webhook.changed_files = ALL_CHANGED_FILES

    return process_github_webhook
