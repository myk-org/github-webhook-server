import contextlib
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy

from github.GithubException import UnknownObjectException

from webhook_server_container.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    FLASK_APP,
    IN_PROGRESS_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    STATIC_LABELS_DICT,
    TOX_STR,
)
from webhook_server_container.utils.helpers import get_github_repo_api, ignore_exceptions


@ignore_exceptions(logger=FLASK_APP.logger)
def get_branch_sampler(repo, branch_name):
    return repo.get_branch(branch=branch_name)


def skip_repo(protected_branches, repo):
    _private = repo.private
    if not protected_branches or not repo or _private:
        if _private:
            FLASK_APP.logger.info(f"{repo.name} skipped, repository is private")
        return True


@ignore_exceptions(logger=FLASK_APP.logger)
def set_branch_protection(branch, repository, required_status_checks, github_api):
    api_user = github_api.get_user().login
    FLASK_APP.logger.info(
        f"Set repository {repository.name} {branch} settings. enabled checks: {required_status_checks}"
    )
    branch.edit_protection(
        strict=True,
        required_conversation_resolution=True,
        contexts=required_status_checks,
        require_code_owner_reviews=False,
        dismiss_stale_reviews=True,
        required_approving_review_count=0,
        required_linear_history=True,
        users_bypass_pull_request_allowances=[api_user],
        teams_bypass_pull_request_allowances=[api_user],
        apps_bypass_pull_request_allowances=[api_user],
    )


@ignore_exceptions(logger=FLASK_APP.logger)
def set_repository_settings(repository):
    FLASK_APP.logger.info(f"Set repository {repository.name} settings")
    repository.edit(delete_branch_on_merge=True, allow_auto_merge=True, allow_update_branch=True)

    FLASK_APP.logger.info(f"Set repository {repository.name} security settings")
    repository._requester.requestJsonAndCheck(
        "PATCH", f"{repository.url}/code-scanning/default-setup", input={"state": "not-configured"}
    )
    repository._requester.requestJsonAndCheck(
        "PATCH",
        repository.url,
        input={
            "security_and_analysis": {
                "secret_scanning": {"status": "enabled"},
                "secret_scanning_push_protection": {"status": "enabled"},
            }
        },
    )


def get_required_status_checks(repo, data, default_status_checks, exclude_status_checks):
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


def set_repository_labels(repository):
    FLASK_APP.logger.info(f"Set repository {repository.name} labels")
    repository_labels = {}
    for label in repository.get_labels():
        repository_labels[label.name.lower()] = {"object": label, "color": label.color}

    for label, color in STATIC_LABELS_DICT.items():
        label_lower = label.lower()
        if label_lower in repository_labels:
            repo_label = repository_labels[label_lower]["object"]
            if repository_labels[label_lower]["color"] == color:
                continue
            else:
                FLASK_APP.logger.info(f"{repository.name}: Edit repository label {label} with color {color}")
                repo_label.edit(name=repo_label.name, color=color)
        else:
            FLASK_APP.logger.info(f"{repository.name}: Add repository label {label} with color {color}")
            repository.create_label(name=label, color=color)

    return f"{repository}: Setting repository labels is done"


def set_repositories_settings(config, github_api):
    FLASK_APP.logger.info("Processing repositories")
    config_data = config.data
    default_status_checks = config_data.get("default-status-checks", [])
    docker = config_data.get("docker")
    if docker:
        FLASK_APP.logger.info("Login in to docker.io")
        docker_username = docker["username"]
        docker_password = docker["password"]
        os.system(f"podman login -u {docker_username} -p {docker_password} docker.io")

    futures = []
    with ThreadPoolExecutor() as executor:
        for _, data in config_data["repositories"].items():
            futures.append(executor.submit(set_repository, data, github_api, default_status_checks))

    for result in as_completed(futures):
        if result.exception():
            FLASK_APP.logger.error(result.exception())
        FLASK_APP.logger.info(result.result())


def set_repository(data, github_api, default_status_checks):
    repository = data["name"]
    FLASK_APP.logger.info(f"Processing repository {repository}")
    protected_branches = data.get("protected-branches", {})
    repo = get_github_repo_api(github_api=github_api, repository=repository)
    if not repo:
        FLASK_APP.logger.error(f"{repository}: Failed to get repository")
        return

    if skip_repo(protected_branches=protected_branches, repo=repo):
        return

    try:
        set_repository_settings(repository=repo)

        set_repository_labels(repository=repo)

        for branch_name, status_checks in protected_branches.items():
            FLASK_APP.logger.info(f"{repository}: Getting branch {branch_name}")
            branch = get_branch_sampler(repo=repo, branch_name=branch_name)
            if not branch:
                FLASK_APP.logger.error(f"{repository}: Failed to get branch {branch_name}")
                continue

            _default_status_checks = deepcopy(default_status_checks)
            (include_status_checks, exclude_status_checks) = get_user_configures_status_checks(
                status_checks=status_checks
            )

            required_status_checks = include_status_checks or get_required_status_checks(
                repo=repo,
                data=data,
                default_status_checks=_default_status_checks,
                exclude_status_checks=exclude_status_checks,
            )

            set_branch_protection(
                branch=branch, repository=repo, required_status_checks=required_status_checks, github_api=github_api
            )
    except UnknownObjectException:
        FLASK_APP.logger.error(f"{repository}: Failed to get repository settings")

    return f"{repository}: Setting repository settings is done"


def set_all_in_progress_check_runs_to_queued(config, repositories_app_api, missing_app_repositories, github_api):
    check_runs = (PYTHON_MODULE_INSTALL_STR, CAN_BE_MERGED_STR, TOX_STR, BUILD_CONTAINER_STR)
    futures = []
    with ThreadPoolExecutor() as executor:
        for _, data in config.data["repositories"].items():
            futures.append(
                executor.submit(
                    set_repository_check_runs_to_queued,
                    data,
                    missing_app_repositories,
                    repositories_app_api,
                    github_api,
                    check_runs,
                )
            )

    for result in as_completed(futures):
        if result.exception():
            FLASK_APP.logger.error(result.exception())
        FLASK_APP.logger.info(result.result())


def set_repository_check_runs_to_queued(data, missing_app_repositories, repositories_app_api, github_api, check_runs):
    repository = data["name"]
    if repository in missing_app_repositories:
        return

    app_api = get_github_repo_api(github_api=repositories_app_api[repository], repository=repository)
    repo = get_github_repo_api(github_api=github_api, repository=repository)
    FLASK_APP.logger.info(f"{repository}: Set all {IN_PROGRESS_STR} check runs to {QUEUED_STR}")
    for pull_request in repo.get_pulls(state="open"):
        last_commit = list(pull_request.get_commits())[-1]
        for check_run in last_commit.get_check_runs():
            if check_run.name in check_runs and check_run.status == IN_PROGRESS_STR:
                FLASK_APP.logger.info(
                    f"{repository}: {check_run.name} status is {IN_PROGRESS_STR}, "
                    f"Setting check run {check_run.name} to {QUEUED_STR}"
                )
                app_api.create_check_run(name=check_run.name, head_sha=last_commit.sha, status=QUEUED_STR)

    return f"{repository}: Set check run status to {QUEUED_STR} is done"
