import copy
import os
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from copy import deepcopy
from typing import Any

import github
import yaml
from github import Auth, Github, GithubIntegration
from github.Auth import AppAuth
from github.Branch import Branch
from github.Commit import Commit
from github.GithubException import GithubException, UnknownObjectException
from github.Label import Label
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.config import Config
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    BUILTIN_CHECK_NAMES,
    CAN_BE_MERGED_STR,
    CONVENTIONAL_TITLE_STR,
    DEFAULT_SUSPICIOUS_PATHS,
    IN_PROGRESS_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SECURITY_COMMITTER_IDENTITY_STR,
    SECURITY_SUSPICIOUS_PATHS_STR,
    STATIC_LABELS_DICT,
)
from webhook_server.utils.helpers import (
    get_future_results,
    get_logger_with_params,
    run_command,
)

DEFAULT_BRANCH_PROTECTION = {
    "strict": True,
    "require_code_owner_reviews": False,
    "dismiss_stale_reviews": True,
    "required_approving_review_count": 0,
    "required_linear_history": True,
    "required_conversation_resolution": True,
}

LOGGER = get_logger_with_params()
_github_app_slug_cache: dict[int, str] = {}
_github_app_slug_lock = threading.Lock()


def _get_github_repo_api(github_api: github.Github, repository: int | str) -> Repository | None:
    try:
        return github_api.get_repo(repository)
    except UnknownObjectException:
        LOGGER.error(f"Failed to get GitHub API for repository {repository}")
        return None


def get_branch_sampler(repo: Repository, branch_name: str) -> Branch:
    return repo.get_branch(branch=branch_name)


def set_branch_protection(
    branch: Branch,
    repository: Repository,
    required_status_checks: list[str],
    strict: bool,
    require_code_owner_reviews: bool,
    dismiss_stale_reviews: bool,
    required_approving_review_count: int,
    required_linear_history: bool,
    required_conversation_resolution: bool,
    api_user: str,
) -> bool:
    LOGGER.info(
        f"[API user {api_user}] - Set branch {branch} setting for {repository.name}. "
        f"enabled checks: {required_status_checks}"
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
    LOGGER.info(f"[API user {api_user}] - Set repository {repository.name} settings")
    repository.edit(delete_branch_on_merge=True, allow_auto_merge=True, allow_update_branch=True)

    if repository.private:
        LOGGER.warning(f"{repository.name}: Repository is private, skipping setting security settings")
        return

    LOGGER.info(f"[API user {api_user}] - Set repository {repository.name} security settings")
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


def get_security_status_checks(
    repository_full_name: str,
    config: Config,
    repository_config: dict[str, Any] | None = None,
) -> list[str]:
    """Security check names to require, gated on the security-checks config.

    security-checks is read with Config.get_value, so the repository-local
    .github-webhook-server.yaml content passed as `repository_config` wins over the
    repository/root config.yaml entries.
    """
    _security_checks: Any = config.get_value(value="security-checks", return_on_none={}, extra_dict=repository_config)
    if not isinstance(_security_checks, dict):
        LOGGER.warning(
            f"{repository_full_name}: security-checks must be a mapping, "
            f"got {type(_security_checks).__name__}. Using security checks defaults."
        )
        _security_checks = {}

    _mandatory: Any = _security_checks.get("mandatory", True)
    if not isinstance(_mandatory, bool):
        LOGGER.warning(
            f"{repository_full_name}: security-checks.mandatory must be boolean, "
            f"got {type(_mandatory).__name__}. Defaulting to true."
        )
        _mandatory = True

    if not _mandatory:
        return []

    security_status_checks: list[str] = []

    _suspicious_paths: Any = _security_checks.get("suspicious-paths", DEFAULT_SUSPICIOUS_PATHS)
    if not isinstance(_suspicious_paths, list):
        LOGGER.warning(
            f"{repository_full_name}: security-checks.suspicious-paths must be a list, "
            f"got {type(_suspicious_paths).__name__}. Using default suspicious paths."
        )
        _suspicious_paths = DEFAULT_SUSPICIOUS_PATHS
    else:
        # Keep in sync with `security_suspicious_paths` in webhook_server/libs/github_api.py:
        # a blank entry would make branch protection require a check the runner never reports.
        _suspicious_paths = [
            str(path).strip() for path in _suspicious_paths if isinstance(path, (str, int, float)) and str(path).strip()
        ]

    if _suspicious_paths:
        security_status_checks.append(SECURITY_SUSPICIOUS_PATHS_STR)

    _committer_identity_check: Any = _security_checks.get("committer-identity-check", True)
    if not isinstance(_committer_identity_check, bool):
        LOGGER.warning(
            f"{repository_full_name}: security-checks.committer-identity-check must be boolean, "
            f"got {type(_committer_identity_check).__name__}. Defaulting to true."
        )
        _committer_identity_check = True

    if _committer_identity_check:
        security_status_checks.append(SECURITY_COMMITTER_IDENTITY_STR)

    return security_status_checks


def get_required_status_checks(
    repo: Repository,
    data: dict[str, Any],
    default_status_checks: list[str],
    exclude_status_checks: list[str],
    *,
    config: Config,
    repository_config: dict[str, Any] | None = None,
) -> list[str]:
    if data.get("tox"):
        default_status_checks.append("tox")

    if data.get("verified-job", True):
        default_status_checks.append("verified")

    if data.get("container"):
        default_status_checks.append(BUILD_CONTAINER_STR)

    if data.get("pypi"):
        default_status_checks.append(PYTHON_MODULE_INSTALL_STR)

    if data.get("pre-commit"):
        default_status_checks.append(PRE_COMMIT_STR)

    if data.get(CONVENTIONAL_TITLE_STR):
        default_status_checks.append(CONVENTIONAL_TITLE_STR)

    try:
        repo.get_contents(".pre-commit-config.yaml")
        default_status_checks.append("pre-commit.ci - pr")
    except UnknownObjectException:
        # 404 is expected if file doesn't exist
        pass
    except GithubException as ex:
        # Handle other GitHub API errors (rate limits, permissions, etc.)
        LOGGER.warning(f"Failed to check .pre-commit-config.yaml for {repo.name}: {ex}")

    default_status_checks.extend(
        get_security_status_checks(
            repository_full_name=data.get("name", ""),
            config=config,
            repository_config=repository_config,
        )
    )

    # Deduplicate status checks while preserving order
    deduplicated: list[str] = list(dict.fromkeys(default_status_checks))

    # Remove excluded status checks
    for status_check in exclude_status_checks:
        while status_check in deduplicated:
            deduplicated.remove(status_check)

    return deduplicated


def get_user_configures_status_checks(status_checks: dict[str, Any]) -> tuple[list[str], list[str]]:
    include_status_checks: list[str] = []
    exclude_status_checks: list[str] = []
    if status_checks:
        include_status_checks = status_checks.get("include-runs", [])
        exclude_status_checks = status_checks.get("exclude-runs", [])

    return include_status_checks, exclude_status_checks


def set_repository_labels(repository: Repository, api_user: str) -> str:
    LOGGER.info(f"[API user {api_user}] - Set repository {repository.name} labels")
    repository_labels: dict[str, dict[str, Any]] = {}
    for label in repository.get_labels():
        repository_labels[label.name.lower()] = {
            "object": label,
            "color": label.color,
        }

    for label_name, label_color in STATIC_LABELS_DICT.items():
        label_lower: str = label_name.lower()
        if label_lower in repository_labels:
            repo_label: Label = repository_labels[label_lower]["object"]
            if repository_labels[label_lower]["color"] == label_color:
                continue
            else:
                LOGGER.debug(f"{repository.name}: Edit repository label {label_name} with color {label_color}")
                repo_label.edit(name=repo_label.name, color=label_color)
        else:
            LOGGER.debug(f"{repository.name}: Add repository label {label_name} with color {label_color}")
            repository.create_label(name=label_name, color=label_color)

    return f"[API user {api_user}] - {repository}: Setting repository labels is done"


def get_repo_branch_protection_rules(config: Config) -> dict[str, Any]:
    branch_protection = copy.deepcopy(DEFAULT_BRANCH_PROTECTION)
    repo_branch_protection = config.get_value(value="branch-protection", return_on_none={})
    branch_protection.update(repo_branch_protection)
    return branch_protection


async def set_repositories_settings(config: Config, apis_dict: dict[str, dict[str, Any]]) -> None:
    LOGGER.info("Processing repositories")
    config_data = config.root_data

    docker: dict[str, str] | None = config_data.get("docker")
    if docker:
        LOGGER.info("Login in to docker.io")
        docker_username: str = docker["username"]
        docker_password: str = docker["password"]
        await run_command(
            log_prefix="docker-login",
            command=f"podman login -u {docker_username} --password-stdin docker.io",
            stdin_input=docker_password,
            redact_secrets=[docker_username, docker_password],
        )

    futures = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config_data["repositories"].items():
            config = Config(repository=repo, logger=LOGGER)
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
) -> tuple[bool, str, Callable[..., Any]]:
    full_repository_name: str = data["name"]
    LOGGER.info(f"Processing repository {full_repository_name}")
    protected_branches: dict[str, Any] = config.get_value(value="protected-branches", return_on_none={})
    github_api = apis_dict[repository_name].get("api")
    api_user = apis_dict[repository_name].get("user", "")

    if not github_api:
        return False, f"{full_repository_name}: Failed to get github api", LOGGER.error

    apply_branch_protection = True
    try:
        # `repository_local_data()` is used instead of `github_api_call()` because this function is
        # synchronous and runs on a ThreadPoolExecutor worker during startup; `github_api_call()` is
        # async and the fetch would have to be hoisted to the async boundary, restructuring startup.
        # Trade-off: this read is not retried and a transient failure is not swallowed - it skips
        # branch protection for this repository instead of writing protection from partial config.
        repository_config: dict[str, Any] = config.repository_local_data(
            github_api=github_api, repository_full_name=full_repository_name, raise_on_error=True
        )
    except yaml.YAMLError as ex:
        # Never abort startup on a broken repo-local file, but do not fall back to the defaults either:
        # the runtime path (config.repository_local_data, raise_on_error=True) propagates this same error, so
        # no security status check is ever reported for this repository. Requiring checks that can never run
        # makes every PR permanently unmergeable with no visible cause, and a default we cannot trust is not
        # "no config" - it is UNKNOWN. Startup and runtime must agree here: leave branch protection untouched.
        LOGGER.error(
            f"[API user {api_user}] - {full_repository_name}: Invalid YAML in .github-webhook-server.yaml, ex: {ex}, "
            "skipping branch protection"
        )
        repository_config = {}
        apply_branch_protection = False
    except Exception:
        # We cannot tell if the repo-local config exists, so we cannot know which security-checks the
        # repository disabled. Skip only branch protection rather than writing it from incomplete config.
        LOGGER.error(
            f"[API user {api_user}] - {full_repository_name}: Failed to read .github-webhook-server.yaml, "
            "skipping branch protection"
        )
        apply_branch_protection = False

    repo = _get_github_repo_api(github_api=github_api, repository=full_repository_name)
    if not repo:
        return False, f"[API user {api_user}] - {full_repository_name}: Failed to get repository", LOGGER.error

    try:
        set_repository_labels(repository=repo, api_user=api_user)
        set_repository_settings(repository=repo, api_user=api_user)

        if repo.private:
            return (
                False,
                f"{full_repository_name}: Repository is private, skipping setting branch settings",
                LOGGER.warning,
            )

        if not apply_branch_protection:
            return True, f"{full_repository_name}: Setting repository settings is done", LOGGER.info

        futures: list[Future[Any]] = []

        with ThreadPoolExecutor() as executor:
            for branch_name, status_checks in protected_branches.items():
                LOGGER.debug(f"[API user {api_user}] - {full_repository_name}: Getting branch {branch_name}")
                branch = get_branch_sampler(repo=repo, branch_name=branch_name)

                if not branch:
                    LOGGER.error(f"[API user {api_user}] - {full_repository_name}: Failed to get branch {branch_name}")
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

                if include_status_checks:
                    # security-checks.mandatory governs this path too, dedup keeping include-runs order
                    required_status_checks: list[str] = list(
                        dict.fromkeys([
                            *include_status_checks,
                            *get_security_status_checks(
                                repository_full_name=full_repository_name,
                                config=config,
                                repository_config=repository_config,
                            ),
                        ])
                    )
                    # exclude-runs wins over include-runs, security checks included
                    required_status_checks = [
                        check for check in required_status_checks if check not in exclude_status_checks
                    ]
                else:
                    required_status_checks = get_required_status_checks(
                        repo=repo,
                        data=data,
                        default_status_checks=_default_status_checks,
                        exclude_status_checks=exclude_status_checks,
                        config=config,
                        repository_config=repository_config,
                    )
                futures.append(
                    executor.submit(
                        set_branch_protection,
                        **{
                            "branch": branch,
                            "repository": repo,
                            "required_status_checks": required_status_checks,
                            "api_user": api_user,
                        },
                        **branch_protection,
                    )
                )

        for result in as_completed(futures):
            if result.exception():
                LOGGER.error(result.exception())

    except UnknownObjectException as ex:
        return (
            False,
            f"[API user {api_user}] - {full_repository_name}: Failed to get repository settings, ex: {ex}",
            LOGGER.error,
        )

    return True, f"[API user {api_user}] - {full_repository_name}: Setting repository settings is done", LOGGER.info


def set_all_in_progress_check_runs_to_queued(repo_config: Config, apis_dict: dict[str, dict[str, Any]]) -> None:
    futures: list[Future[Any]] = []

    with ThreadPoolExecutor() as executor:
        for repo, data in repo_config.root_data["repositories"].items():
            repo_config = Config(repository=repo, logger=LOGGER)
            futures.append(
                executor.submit(
                    set_repository_check_runs_to_queued,
                    **{
                        "config_": repo_config,
                        "data": data,
                        "github_api": apis_dict[repo]["api"],
                        "check_runs": BUILTIN_CHECK_NAMES,
                        "api_user": apis_dict[repo]["user"],
                    },
                )
            )

    get_future_results(futures=futures)


def set_repository_check_runs_to_queued(
    config_: Config,
    data: dict[str, Any],
    github_api: Github,
    check_runs: frozenset[str],
    api_user: str,
) -> tuple[bool, str, Callable[..., Any]]:
    def _set_checkrun_queued(_api: Repository, _pull_request: PullRequest) -> None:
        # Avoid materializing all commits - use single-pass iteration to find last commit
        # This is O(1) memory instead of O(N) for large PRs
        last_commit: Commit | None = None
        for commit in _pull_request.get_commits():
            last_commit = commit  # Assign on each iteration to get final value
        if last_commit is None:
            LOGGER.error(f"[API user {api_user}] - {repository}: [PR:{_pull_request.number}] No commits found")
            return
        # Use REST API method directly (this is REST-only code)
        for check_run in last_commit.get_check_runs():
            if check_run.name in check_runs and check_run.status == IN_PROGRESS_STR:
                LOGGER.warning(
                    f"[API user {api_user}] - {repository}: [PR:{_pull_request.number}] "
                    f"{check_run.name} status is {IN_PROGRESS_STR}, "
                    f"Setting check run {check_run.name} to {QUEUED_STR}"
                )
                _api.create_check_run(name=check_run.name, head_sha=last_commit.sha, status=QUEUED_STR)

    repository: str = data["name"]
    repository_app_api = get_repository_github_app_api(config_=config_, repository_name=repository)
    if not repository_app_api:
        return False, f"[API user {api_user}] - {repository}: Failed to get repositories GitHub app API", LOGGER.error

    app_api = _get_github_repo_api(github_api=repository_app_api, repository=repository)
    if not app_api:
        LOGGER.error(f"[API user {api_user}] - Failed to get GitHub app API for repository {repository}")
        return False, f"[API user {api_user}] - Failed to get GitHub app API for repository {repository}", LOGGER.error

    repo = _get_github_repo_api(github_api=github_api, repository=repository)
    if not repo:
        LOGGER.error(f"[API user {api_user}] - Failed to get GitHub API for repository {repository}")
        return False, f"[API user {api_user}] - Failed to get GitHub API for repository {repository}", LOGGER.error

    LOGGER.info(f"{repository}: Set all {IN_PROGRESS_STR} check runs to {QUEUED_STR}")

    futures = []
    with ThreadPoolExecutor() as executor:
        for pull_request in repo.get_pulls(state="open"):
            futures.append(executor.submit(_set_checkrun_queued, _api=app_api, _pull_request=pull_request))

    for _ in as_completed(futures):
        ...

    return True, f"[API user {api_user}] - {repository}: Set check run status to {QUEUED_STR} is done", LOGGER.debug


def _create_github_integration(config_: Config, github_app_id: int | None = None) -> GithubIntegration:
    """Create an authenticated GithubIntegration instance using App JWT.

    Reads the private key and app ID from config to create an authenticated
    GithubIntegration. This is the shared setup for all GitHub App API operations.
    """
    with open(os.path.join(config_.data_dir, "webhook-server.private-key.pem")) as fd:
        private_key = fd.read()

    if not github_app_id:
        github_app_id = config_.get_value("github-app-id")
        if not github_app_id:
            raise ValueError("github-app-id not configured — required for GitHub App authentication")

    auth: AppAuth = Auth.AppAuth(app_id=github_app_id, private_key=private_key)
    return GithubIntegration(auth=auth)


def get_repository_github_app_api(config_: Config, repository_name: str) -> Github | None:
    LOGGER.debug("Getting repositories GitHub app API")
    app_instance = _create_github_integration(config_)
    owner: str
    repo: str
    owner, repo = repository_name.split("/")

    try:
        return app_instance.get_repo_installation(owner=owner, repo=repo).get_github_for_installation()

    except Exception:
        LOGGER.error(
            f"Repository {repository_name} not found by manage-repositories-app, "
            f"make sure the app installed (https://github.com/apps/manage-repositories-app)"
        )

        return None


def get_github_app_slug(config_: Config) -> str:
    """Get the GitHub App slug using App JWT authentication.

    Returns the app slug (e.g., 'manage-repositories-app').
    Caches the result keyed by github-app-id since the slug is immutable per app.
    Raises on failure so github_api_call() can apply retry/backoff.
    """
    github_app_id: int | None = config_.get_value("github-app-id")
    if not github_app_id:
        raise ValueError("github-app-id not configured — required for GitHub App slug lookup")

    if github_app_id in _github_app_slug_cache:
        return _github_app_slug_cache[github_app_id]

    # Perform network I/O outside the lock — concurrent calls may
    # duplicate work but won't block each other.
    LOGGER.debug("Getting GitHub App slug")
    app_instance = _create_github_integration(config_, github_app_id=github_app_id)
    slug = app_instance.get_app().slug
    if not slug:
        raise ValueError("GitHub App returned empty slug")

    with _github_app_slug_lock:
        # Another thread may have populated it while we were fetching
        if github_app_id not in _github_app_slug_cache:
            _github_app_slug_cache[github_app_id] = slug
        return _github_app_slug_cache[github_app_id]


def get_repository_github_app_token(config_: Config, repository_name: str) -> str | None:
    """Get a raw GitHub App installation token string for use with CLI tools.

    Returns the token string or None if the app is not configured/installed.
    """
    LOGGER.debug(f"Getting GitHub App installation token for {repository_name}")
    app_instance = _create_github_integration(config_)
    owner, repo = repository_name.split("/", maxsplit=1)

    try:
        installation = app_instance.get_repo_installation(owner=owner, repo=repo)
        access_token = app_instance.get_access_token(installation.id)
        return access_token.token
    except GithubException:
        LOGGER.exception(
            f"Failed to get GitHub App installation token for {repository_name}, "
            f"make sure the app is installed (https://github.com/apps/manage-repositories-app)"
        )
        return None
