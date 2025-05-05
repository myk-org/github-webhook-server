from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from github.Hook import Hook

from webhook_server.libs.config import Config
from webhook_server.utils.helpers import (
    get_future_results,
    get_github_repo_api,
    get_logger_with_params,
)

LOGGER = get_logger_with_params(name="webhook")


def process_github_webhook(
    repository_name: str,
    data: dict[str, Any],
    webhook_ip: str,
    apis_dict: dict[str, dict[str, Any]],
    secret: str | None = None,
) -> tuple[bool, str, Callable]:
    full_repository_name: str = data["name"]
    github_api = apis_dict[repository_name].get("api")
    api_user = apis_dict[repository_name].get("user")

    if not github_api:
        return False, f"{full_repository_name}: Failed to get github api", LOGGER.error

    repo = get_github_repo_api(github_api=github_api, repository=full_repository_name)
    if not repo:
        return False, f"[API user {api_user}] - Could not find repository {full_repository_name}", LOGGER.error

    config_: dict[str, str] = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}

    if secret:
        config_["secret"] = secret

    events: list[str] = data.get("events", ["*"])

    try:
        hooks: list[Hook] = list(repo.get_hooks())
    except Exception as ex:
        return (
            False,
            f"[API user {api_user}] - Could not list webhook for {full_repository_name}, check token permissions: {ex}",
            LOGGER.error,
        )

    for _hook in hooks:
        if webhook_ip in _hook.config["url"]:
            secret_presence_mismatch = bool(_hook.config.get("secret")) != bool(secret)
            if secret_presence_mismatch:
                LOGGER.info(f"[API user {api_user}] - {full_repository_name}: Deleting old webhook")
                _hook.delete()

            else:
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


def create_webhook(config: Config, apis_dict: dict[str, dict[str, Any]], secret: str | None = None) -> None:
    LOGGER.info("Preparing webhook configuration")
    webhook_ip = config.root_data["webhook_ip"]

    futures = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config.root_data["repositories"].items():
            futures.append(
                executor.submit(
                    process_github_webhook,
                    **{
                        "data": data,
                        "webhook_ip": webhook_ip,
                        "apis_dict": apis_dict,
                        "repository_name": repo,
                        "secret": secret,
                    },
                )
            )

    get_future_results(futures=futures)
