import contextlib
import os
from copy import deepcopy
from multiprocessing import Process

from constants import BUILD_CONTAINER_STR, FLASK_APP, PYTHON_MODULE_INSTALL_STR
from github import Github
from github.GithubException import UnknownObjectException
from utils import get_github_repo_api, get_repository_from_config, ignore_exceptions


@ignore_exceptions(retry=10)
def get_branch_sampler(repo, branch_name):
    return repo.get_branch(branch=branch_name)


def skip_repo(protected_branches, repo):
    if not protected_branches or not repo or repo.private:
        return True


@ignore_exceptions(FLASK_APP.logger)
def set_branch_protection(branch, repository, required_status_checks):
    FLASK_APP.logger.info(
        f"Set repository {repository} branch {branch} settings [checks: {required_status_checks}]"
    )
    branch.edit_protection(strict=True)
    branch.edit_required_pull_request_reviews(
        require_code_owner_reviews=False,
        dismiss_stale_reviews=True,
        required_approving_review_count=0,
    )
    branch.edit_required_status_checks(
        strict=True,
        contexts=required_status_checks,
    )


@ignore_exceptions(FLASK_APP.logger)
def set_repository_settings(repository):
    if not repository.private:
        FLASK_APP.logger.info(f"Set repository {repository} settings")
        repository.edit(
            delete_branch_on_merge=True,
            allow_auto_merge=True,
            allow_update_branch=True,
        )

        repository._requester.requestJsonAndCheck(
            "PATCH",
            repository.url,
            input={
                "security_and_analysis": {
                    "secret_scanning": {"status": "enabled"},
                    "secret_scanning_push_protection": {"status": "enabled"},
                },
                "code-scanning": {"default-setup": {"state": "configured"}},
            },
        )


def get_required_status_checks(
    repo, data, default_status_checks, exclude_status_checks
):
    if data.get("tox"):
        default_status_checks.append("tox")

    if data.get("verified_job", True):
        default_status_checks.append("verified")

    if data.get("container"):
        default_status_checks.append(BUILD_CONTAINER_STR)

    if data.get("pypi"):
        default_status_checks.append(PYTHON_MODULE_INSTALL_STR)

    with contextlib.suppress(UnknownObjectException):
        repo.get_contents(".pre-commit-config.yaml")
        default_status_checks.append("pre-commit.ci - pr")

    for status_check in exclude_status_checks:
        if status_check in default_status_checks:
            default_status_checks.remove(status_check)

    return default_status_checks


def get_user_configures_status_checks(status_checks):
    include_status_checks = []
    exclude_status_checks = []
    if status_checks:
        include_status_checks = status_checks.get("include-runs", [])
        exclude_status_checks = status_checks.get("exclude-runs", [])

    return include_status_checks, exclude_status_checks


def set_repositories_settings():
    procs = []
    FLASK_APP.logger.info("Processing repositories")
    app_data = get_repository_from_config()
    default_status_checks = app_data.get("default-status-checks", [])
    docker = app_data.get("docker")
    if docker:
        FLASK_APP.logger.info("Login in to docker.io")
        docker_username = docker["username"]
        docker_password = docker["password"]
        os.system(f"podman login -u {docker_username} -p {docker_password} docker.io")

    for repo, data in app_data["repositories"].items():
        repository = data["name"]
        FLASK_APP.logger.info(f"Processing repository {repository}")
        protected_branches = data.get("protected-branches", {})
        gapi = Github(login_or_token=data["token"])
        repo = get_github_repo_api(gapi=gapi, repository=repository)
        set_repository_settings(repository=repo)
        if skip_repo(protected_branches, repo):
            continue

        for branch_name, status_checks in protected_branches.items():
            branch = get_branch_sampler(repo=repo, branch_name=branch_name)
            if not branch:
                FLASK_APP.logger.error(
                    f"{repository}: Failed to get branch {branch_name}"
                )
                continue

            _default_status_checks = deepcopy(default_status_checks)
            (
                include_status_checks,
                exclude_status_checks,
            ) = get_user_configures_status_checks(status_checks=status_checks)

            required_status_checks = (
                include_status_checks
                or get_required_status_checks(
                    repo=repo,
                    data=data,
                    default_status_checks=_default_status_checks,
                    exclude_status_checks=exclude_status_checks,
                )
            )

            proc = Process(
                target=set_branch_protection,
                args=(
                    branch,
                    repository,
                    required_status_checks,
                ),
            )
            procs.append(proc)
            proc.start()
    return procs
