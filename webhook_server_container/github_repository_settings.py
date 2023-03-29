from multiprocessing import Process

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
            require_code_owner_reviews=True,
            required_approving_review_count=1,
            dismiss_stale_reviews=True,
        )
    except Exception:
        return


def set_repositories_settings(app):
    procs = []
    app.logger.info("Set repository settings")

    for repo, data in get_repository_from_config()["repositories"].items():
        protected_branches = data.get("protected-branches", [])
        repository = data["name"]
        repo = get_github_repo_api(app=app, token=data["token"], repository=repository)
        if skip_repo(protected_branches, repo):
            continue

        default_status_checks = [
            "pre-commit.ci - pr",
            "WIP",
            "dpulls",
            "Inclusive Language",
        ]

        for branch_name, status_checks in protected_branches.items():
            branch = get_branch_sampler(repo=repo, branch_name=branch_name)
            if not branch:
                app.logger.error(f"{repository}: Failed to get branch {branch_name}")
                continue

            required_status_checks = []
            if data.get("tox"):
                required_status_checks.append("tox")

            if data.get("verified_job", True):
                required_status_checks.append("verified")

            if status_checks:
                required_status_checks.extend(status_checks)
            else:
                required_status_checks.extend(default_status_checks)

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
