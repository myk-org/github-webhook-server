from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import github

from webhook_server.libs.config import Config
from webhook_server.utils.github_repository_settings import (
    set_all_in_progress_check_runs_to_queued,
    set_repositories_settings,
)
from webhook_server.utils.helpers import get_api_with_highest_rate_limit, get_logger_with_params
from webhook_server.utils.webhook import create_webhook


def get_repository_api(repository: str) -> tuple[str, github.Github | None, str]:
    config = Config(repository=repository)
    github_api, _, api_user = get_api_with_highest_rate_limit(config=config, repository_name=repository)
    return repository, github_api, api_user


def repository_and_webhook_settings(webhook_secret: str | None = None) -> None:
    logger = get_logger_with_params(name="github-repository-and-webhook-settings")

    config = Config()
    apis_dict: dict[str, dict[str, Any]] = {}

    apis: list = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config.root_data["repositories"].items():
            apis.append(
                executor.submit(
                    get_repository_api,
                    **{"repository": repo},
                )
            )

    for result in as_completed(apis):
        repository, github_api, api_user = result.result()
        apis_dict[repository] = {"api": github_api, "user": api_user}

    logger.debug(f"Repositories APIs: {apis_dict}")

    set_repositories_settings(config=config, apis_dict=apis_dict)
    set_all_in_progress_check_runs_to_queued(repo_config=config, apis_dict=apis_dict)
    create_webhook(config=config, apis_dict=apis_dict, secret=webhook_secret)
