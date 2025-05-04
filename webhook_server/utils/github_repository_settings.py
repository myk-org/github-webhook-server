import contextlib
import copy
import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any, Callable

import github
from github import Auth, Github, GithubIntegration
from github.Auth import AppAuth
from github.Branch import Branch
from github.Commit import Commit
from github.GithubException import UnknownObjectException
from github.Label import Label
from github.Repository import Repository

from webhook_server.libs.config import Config
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CONVENTIONAL_TITLE_STR,
    IN_PROGRESS_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    STATIC_LABELS_DICT,
    TOX_STR,
)
from webhook_server.utils.helpers import (
    get_future_results,
    get_logger_with_params,
)

DEFAULT_BRANCH_PROTECTION = {
    "strict": True,
    "require_code_owner_reviews": False,
    "dismiss_stale_reviews": True,
    "required_approving_review_count": 0,
    "required_linear_history": True,
    "required_conversation_resolution": True,
}


def _get_github_repo_api(github_api: github.Github, repository: int | str) -> Repository | None:
    logger = get_logger_with_params(name="github-repository-settings")
    try:
        return github_api.get_repo(repository)
    except UnknownObjectException:
        logger.error(f"Failed to get GitHub API for repository {repository}")
        return None


def get_branch_sampler(repo: Repository, branch_name: str) -> Branch:
    return repo.get_branch(branch=branch_name)


def set_branch_protection(
    branch: Branch,
    repository: Repository,
    required_status_checks: list[str],
    github_api: Github,
    strict: bool,
    require_code_owner_reviews: bool,
    dismiss_stale_reviews: bool,
    required_approving_review_count: int,
    required_linear_history: bool,
    required_conversation_resolution: bool,
    api_user: str,
) -> bool:
    logger = get_logger_with_params(name="github-repository-settings")

    logger.info(
        f"[API user {api_user}] - Set branch {branch} setting for {repository.name}. enabled checks: {required_status_checks}"
    )
    branch.edit_protection(
        strict=strict,
        required_conversation_resolution=required_conversation_resolution,
        contexts=required_status_checks,
        require_code_owner_reviews=require_code_owner_reviews,
        dismiss_stale_reviews=dismiss_stale_reviews,
        required_approving_review_count=required_approving_review_count,
        required_linear_history=required_linear_history,
        users_bypass_pull_request_allowances=[api_user],
        teams_bypass_pull_request_allowances=[api_user],
        apps_bypass_pull_request_allowances=[api_user],
    )

    return True


def set_repository_settings(repository: Repository, api_user: str) -> None:
    logger = get_logger_with_params(name="github-repository-settings")

    logger.info(f"[API user {api_user}] - Set repository {repository.name} settings")
    repository.edit(delete_branch_on_merge=True, allow_auto_merge=True, allow_update_branch=True)

    if repository.private:
        logger.warning(f"{repository.name}: Repository is private, skipping setting security settings")
        return

    logger.info(f"[API user {api_user}] - Set repository {repository.name} security settings")
    repository._requester.requestJsonAndCheck(
        "PATCH",
        f"{repository.url}/code-scanning/default-setup",
        input={"state": "not-configured"},
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


def get_required_status_checks(
    repo: Repository,
    data: dict[str, Any],
    default_status_checks: list[str],
    exclude_status_checks: list[str],
) -> list[str]:
    if data.get("tox"):
        default_status_checks.append("tox")

    if data.get("verified_job", True):
        default_status_checks.append("verified")

    if data.get("container"):
        default_status_checks.append(BUILD_CONTAINER_STR)

    if data.get("pypi"):
        default_status_checks.append(PYTHON_MODULE_INSTALL_STR)

    if data.get("pre-commit"):
        default_status_checks.append(PRE_COMMIT_STR)

    if data.get(CONVENTIONAL_TITLE_STR):
        default_status_checks.append(CONVENTIONAL_TITLE_STR)

    with contextlib.suppress(Exception):
        repo.get_contents(".pre-commit-config.yaml")
        default_status_checks.append("pre-commit.ci - pr")

    for status_check in exclude_status_checks:
        if status_check in default_status_checks:
            default_status_checks.remove(status_check)

    return default_status_checks


def get_user_configures_status_checks(status_checks: dict[str, Any]) -> tuple[list[str], list[str]]:
    include_status_checks: list[str] = []
    exclude_status_checks: list[str] = []
    if status_checks:
        include_status_checks = status_checks.get("include-runs", [])
        exclude_status_checks = status_checks.get("exclude-runs", [])

    return include_status_checks, exclude_status_checks


def set_repository_labels(repository: Repository, api_user: str) -> str:
    logger = get_logger_with_params(name="github-repository-settings")

    logger.info(f"[API user {api_user}] - Set repository {repository.name} labels")
    repository_labels: dict[str, dict[str, Any]] = {}
    for label in repository.get_labels():
        repository_labels[label.name.lower()] = {
            "object": label,
            "color": label.color,
        }

    for label, color in STATIC_LABELS_DICT.items():
        label_lower: str = label.lower()
        if label_lower in repository_labels:
            repo_label: Label = repository_labels[label_lower]["object"]
            if repository_labels[label_lower]["color"] == color:
                continue
            else:
                logger.debug(f"{repository.name}: Edit repository label {label} with color {color}")
                repo_label.edit(name=repo_label.name, color=color)
        else:
            logger.debug(f"{repository.name}: Add repository label {label} with color {color}")
            repository.create_label(name=label, color=color)

    return f"[API user {api_user}] - {repository}: Setting repository labels is done"


def get_repo_branch_protection_rules(config: Config) -> dict[str, Any]:
    branch_protection = copy.deepcopy(DEFAULT_BRANCH_PROTECTION)
    repo_branch_protection = config.get_value(value="branch_protection", return_on_none={})
    branch_protection.update(repo_branch_protection)
    return branch_protection


def set_repositories_settings(config: Config, apis_dict: dict[str, dict[str, Any]]) -> None:
    logger = get_logger_with_params(name="github-repository-settings")

    logger.info("Processing repositories")
    config_data = config.root_data

    docker: dict[str, str] | None = config_data.get("docker")
    if docker:
        logger.info("Login in to docker.io")
        docker_username: str = docker["username"]
        docker_password: str = docker["password"]
        os.system(f"podman login -u {docker_username} -p {docker_password} docker.io")

    futures = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config_data["repositories"].items():
            config = Config(repository=repo)
            branch_protection = get_repo_branch_protection_rules(config=config)
            futures.append(
                executor.submit(
                    set_repository,
                    **{
                        "data": data,
                        "apis_dict": apis_dict,
                        "repository_name": repo,
                        "branch_protection": branch_protection,
                        "config": config,
                    },
                )
            )

    get_future_results(futures=futures)


def set_repository(
    repository_name: str,
    data: dict[str, Any],
    apis_dict: dict[str, dict[str, Any]],
    branch_protection: dict[str, Any],
    config: Config,
) -> tuple[bool, str, Callable]:
    logger = get_logger_with_params(name="github-repository-settings")

    full_repository_name: str = data["name"]
    logger.info(f"Processing repository {full_repository_name}")
    protected_branches: dict[str, Any] = config.get_value(value="protected-branches", return_on_none={})
    github_api = apis_dict[repository_name].get("api")
    api_user = apis_dict[repository_name].get("user", "")

    if not github_api:
        return False, f"{full_repository_name}: Failed to get github api", logger.error

    repo = _get_github_repo_api(github_api=github_api, repository=full_repository_name)
    if not repo:
        return False, f"[API user {api_user}] - {full_repository_name}: Failed to get repository", logger.error

    try:
        set_repository_labels(repository=repo, api_user=api_user)
        set_repository_settings(repository=repo, api_user=api_user)

        if repo.private:
            return (
                False,
                f"{full_repository_name}: Repository is private, skipping setting branch settings",
                logger.warning,
            )

        futures: list["Future"] = []

        with ThreadPoolExecutor() as executor:
            for branch_name, status_checks in protected_branches.items():
                logger.debug(f"[API user {api_user}] - {full_repository_name}: Getting branch {branch_name}")
                branch = get_branch_sampler(repo=repo, branch_name=branch_name)

                if not branch:
                    logger.error(f"[API user {api_user}] - {full_repository_name}: Failed to get branch {branch_name}")
                    continue

                default_status_checks: list[str] = config.get_value(
                    value="default-status-checks", return_on_none=[]
                ) + [
                    CAN_BE_MERGED_STR,
                ]
                _default_status_checks = deepcopy(default_status_checks)
                (
                    include_status_checks,
                    exclude_status_checks,
                ) = get_user_configures_status_checks(status_checks=status_checks)

                required_status_checks = include_status_checks or get_required_status_checks(
                    repo=repo,
                    data=data,
                    default_status_checks=_default_status_checks,
                    exclude_status_checks=exclude_status_checks,
                )
                futures.append(
                    executor.submit(
                        set_branch_protection,
                        **{
                            "branch": branch,
                            "repository": repo,
                            "required_status_checks": required_status_checks,
                            "github_api": github_api,
                            "api_user": api_user,
                        },
                        **branch_protection,
                    )
                )

        for result in as_completed(futures):
            if result.exception():
                logger.error(result.exception())

    except UnknownObjectException as ex:
        return (
            False,
            f"[API user {api_user}] - {full_repository_name}: Failed to get repository settings, ex: {ex}",
            logger.error,
        )

    return True, f"[API user {api_user}] - {full_repository_name}: Setting repository settings is done", logger.info


def set_all_in_progress_check_runs_to_queued(repo_config: Config, apis_dict: dict[str, dict[str, Any]]) -> None:
    check_runs = (
        PYTHON_MODULE_INSTALL_STR,
        CAN_BE_MERGED_STR,
        TOX_STR,
        BUILD_CONTAINER_STR,
        PRE_COMMIT_STR,
    )
    futures: list["Future"] = []

    with ThreadPoolExecutor() as executor:
        for repo, data in repo_config.root_data["repositories"].items():
            repo_config = Config(repository=repo)
            futures.append(
                executor.submit(
                    set_repository_check_runs_to_queued,
                    **{
                        "config_": repo_config,
                        "data": data,
                        "github_api": apis_dict[repo]["api"],
                        "check_runs": check_runs,
                        "api_user": apis_dict[repo]["user"],
                    },
                )
            )

    get_future_results(futures=futures)


def set_repository_check_runs_to_queued(
    config_: Config,
    data: dict[str, Any],
    github_api: Github,
    check_runs: tuple[str],
    api_user: str,
) -> tuple[bool, str, Callable]:
    logger = get_logger_with_params(name="github-repository-settings")

    repository: str = data["name"]
    repository_app_api = get_repository_github_app_api(config_=config_, repository_name=repository)
    if not repository_app_api:
        return False, f"[API user {api_user}] - {repository}: Failed to get repositories GitHub app API", logger.error

    app_api = _get_github_repo_api(github_api=repository_app_api, repository=repository)
    if not app_api:
        logger.error(f"[API user {api_user}] - Failed to get GitHub app API for repository {repository}")
        return False, f"[API user {api_user}] - Failed to get GitHub app API for repository {repository}", logger.error

    repo = _get_github_repo_api(github_api=github_api, repository=repository)
    if not repo:
        logger.error(f"[API user {api_user}] - Failed to get GitHub API for repository {repository}")
        return False, f"[API user {api_user}] - Failed to get GitHub API for repository {repository}", logger.error

    logger.info(f"{repository}: Set all {IN_PROGRESS_STR} check runs to {QUEUED_STR}")
    for pull_request in repo.get_pulls(state="open"):
        last_commit: Commit = list(pull_request.get_commits())[-1]
        for check_run in last_commit.get_check_runs():
            if check_run.name in check_runs and check_run.status == IN_PROGRESS_STR:
                logger.warning(
                    f"[API user {api_user}] - {repository}: [PR:{pull_request.number}] {check_run.name} status is {IN_PROGRESS_STR}, "
                    f"Setting check run {check_run.name} to {QUEUED_STR}"
                )
                app_api.create_check_run(name=check_run.name, head_sha=last_commit.sha, status=QUEUED_STR)

    return True, f"[API user {api_user}] - {repository}: Set check run status to {QUEUED_STR} is done", logger.debug


def get_repository_github_app_api(config_: Config, repository_name: str) -> Github | None:
    logger = get_logger_with_params(name="github-repository-settings")

    logger.debug("Getting repositories GitHub app API")
    with open(os.path.join(config_.data_dir, "webhook-server.private-key.pem")) as fd:
        private_key = fd.read()

    github_app_id: int = config_.root_data["github-app-id"]
    auth: AppAuth = Auth.AppAuth(app_id=github_app_id, private_key=private_key)
    app_instance: GithubIntegration = GithubIntegration(auth=auth)
    owner: str
    repo: str
    owner, repo = repository_name.split("/")
    try:
        return app_instance.get_repo_installation(owner=owner, repo=repo).get_github_for_installation()
    except UnknownObjectException:
        logger.error(
            f"Repository {repository_name} not found by manage-repositories-app, "
            f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
        )
        return None
