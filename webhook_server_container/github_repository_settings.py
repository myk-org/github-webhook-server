import asyncio

from github.GithubException import UnknownObjectException
from github_api import GitHubApi
from utils import get_repository_from_config


async def set_branch_protection(app, branch, repository, required_status_checks):
    app.logger.info(f"Set repository {repository} branch {branch} settings")
    try:
        branch.edit_protection(
            strict=True,
            contexts=required_status_checks,
            require_code_owner_reviews=True,
            required_approving_review_count=1,
            dismiss_stale_reviews=True,
        )
    except UnknownObjectException:
        return


async def process_github_webhook(app, data):
    protected_branches = data.get("protected-branches", [])
    repository = data["name"]
    try:
        api = GitHubApi(app=app, hook_data=data)
    except UnknownObjectException:
        app.logger.info(f"Repository {repository} not found or token invalid")
        return

    tasks = []
    for branch_name in protected_branches:
        branch = api.repository.get_branch(branch=branch_name)
        required_status_checks = [
            "pre-commit.ci - pr",
            "WIP",
            "dpulls",
            "SonarCloud Code Analysis",
            "Inclusive Language",
            "Verified",
        ]
        if data.get("tox"):
            required_status_checks.append("tox")

        tasks.append(
            asyncio.create_task(
                set_branch_protection(
                    app=app,
                    branch=branch,
                    repository=repository,
                    required_status_checks=required_status_checks,
                )
            )
        )
    for coro in asyncio.as_completed(tasks):
        await coro


async def set_repository_settings(app):
    app.logger.info("Set repository settings")
    repos = get_repository_from_config()

    tasks = []
    for repo, data in repos["repositories"].items():
        tasks.append(
            asyncio.create_task(
                process_github_webhook(
                    app=app,
                    data=data,
                )
            )
        )

    for coro in asyncio.as_completed(tasks):
        await coro
