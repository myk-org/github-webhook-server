from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Tuple

import github
from github.Hook import Hook

from webhook_server_container.libs.config import Config
from webhook_server_container.utils.helpers import (
    get_api_with_highest_rate_limit,
    get_future_results,
    get_github_repo_api,
    get_logger_with_params,
)

LOGGER = get_logger_with_params(name="webhook")


def process_github_webhook(
    repository_name: str, data: Dict[str, Any], webhook_ip: str, apis_dict: dict[str, dict[str, Any]]
) -> Tuple[bool, str, Callable]:
    full_repository_name: str = data["name"]
    github_api = apis_dict[repository_name].get("api")
    api_user = apis_dict[repository_name].get("user")

    if not github_api:
        return False, f"{full_repository_name}: Failed to get github api", LOGGER.error

    repo = get_github_repo_api(github_api=github_api, repository=full_repository_name)
    if not repo:
        return False, f"[API user {api_user}] - Could not find repository {full_repository_name}", LOGGER.error

    config_: Dict[str, str] = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}
    events: List[str] = data.get("events", ["*"])

    try:
        hooks: List[Hook] = list(repo.get_hooks())
    except Exception as ex:
        return (
            False,
            f"[API user {api_user}] - Could not list webhook for {full_repository_name}, check token permissions: {ex}",
            LOGGER.error,
        )

    for _hook in hooks:
        if webhook_ip in _hook.config["url"]:
            return (
                True,
                f"[API user {api_user}] - {full_repository_name}: Hook already exists - {_hook.config['url']}",
                LOGGER.info,
            )

    LOGGER.info(
        f"[API user {api_user}] - Creating webhook: {config_['url']} for {full_repository_name} with events: {events}"
    )
    repo.create_hook(name="web", config=config_, events=events, active=True)
    return True, f"[API user {api_user}] - {full_repository_name}: Create webhook is done", LOGGER.info


def get_repository_api(repository: str) -> tuple[str, github.Github | None, str]:
    config = Config(repository=repository)
    github_api, _, api_user = get_api_with_highest_rate_limit(config=config, repository_name=repository)
    return repository, github_api, api_user


def create_webhook() -> None:
    config = Config()
    LOGGER.info("Preparing webhook configuration")
    webhook_ip = config.data["webhook_ip"]
    apis_dict: dict[str, dict[str, Any]] = {}

    apis: list = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config.data["repositories"].items():
            apis.append(
                executor.submit(
                    get_repository_api,
                    **{"repository": repo},
                )
            )

    for result in as_completed(apis):
        repository, github_api, api_user = result.result()
        apis_dict[repository] = {"api": github_api, "user": api_user}

    LOGGER.debug(f"Repositories APIs: {apis_dict}")

    futures = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config.data["repositories"].items():
            futures.append(
                executor.submit(
                    process_github_webhook,
                    **{"data": data, "webhook_ip": webhook_ip, "apis_dict": apis_dict, "repository_name": repo},
                )
            )

    get_future_results(futures=futures)


if __name__ == "__main__":
    create_webhook()
