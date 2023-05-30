import contextlib
from multiprocessing import Process

from constants import BUILD_CONTAINER_STR, PYTHON_MODULE_INSTALL_STR
from github import Github
from github.GithubException import UnknownObjectException
from utils import get_github_repo_api, get_repository_from_config


def get_branch_sampler(repo, branch_name):
    sec = 1
    while sec < 10:
        try:
            return repo.get_branch(branch=branch_name)
        except Exception:
            sec += 1


def skip_repo(protected_branches, repo):
    if not protected_branches or not repo or repo.private:
        return True


def set_branch_protection(app, branch, repository, required_status_checks):
    app.logger.info(
        f"Set repository {repository} branch {branch} settings [checks: {required_status_checks}]"
    )
    try:
        branch.edit_protection(
            strict=True,
            contexts=required_status_checks,
            require_code_owner_reviews=False,
            dismiss_stale_reviews=True,
            required_approving_review_count=0,
        )
    except Exception as ex:
        app.logger.info(
            f"Failed to set branch protection for {repository}/{branch}. {ex}"
        )
        return


def get_required_status_checks(repo, data, default_status_checks):
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

    return default_status_checks


def set_repositories_settings(app):
    procs = []
    app.logger.info("Set repository settings")

    for repo, data in get_repository_from_config()["repositories"].items():
        protected_branches = data.get("protected-branches", [])
        repository = data["name"]
        gapi = Github(login_or_token=data["token"])
        repo = get_github_repo_api(gapi=gapi, app=app, repository=repository)
        if skip_repo(protected_branches, repo):
            continue

        default_status_checks = [
            "WIP",
            "dpulls",
            "Inclusive Language",
            "SonarCloud Code Analysis",
            "can-be-merged",
        ]

        for branch_name, status_checks in protected_branches.items():
            branch = get_branch_sampler(repo=repo, branch_name=branch_name)
            if not branch:
                app.logger.error(f"{repository}: Failed to get branch {branch_name}")
                continue

            required_status_checks = status_checks or get_required_status_checks(
                repo=repo,
                data=data,
                default_status_checks=default_status_checks,
            )

            proc = Process(
                target=set_branch_protection,
                args=(
                    app,
                    branch,
                    repository,
                    required_status_checks,
                ),
            )
            procs.append(proc)
            proc.start()
    return procs
