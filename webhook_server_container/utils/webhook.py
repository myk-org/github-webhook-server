from concurrent.futures import ThreadPoolExecutor, as_completed
import os

from github import Github
from simple_logger.logger import get_logger

from webhook_server_container.libs.config import Config
from webhook_server_container.utils.helpers import (
    get_api_with_highest_rate_limit,
    get_github_repo_api,
)
from pyhelper_utils.general import ignore_exceptions


LOGGER = get_logger(name="webhook", filename=os.environ.get("WEBHOOK_SERVER_LOG_FILE"))


@ignore_exceptions(logger=LOGGER)
def process_github_webhook(data, github_api, webhook_ip):
    repository = data["name"]
    repo = get_github_repo_api(github_api=github_api, repository=repository)
    if not repo:
        LOGGER.error(f"Could not find repository {repository}")
        return

    config = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}
    events = data.get("events", ["*"])

    try:
        hooks = list(repo.get_hooks())
    except Exception as ex:
        LOGGER.error(f"Could not list webhook for {repository}, check token permissions: {ex}")
        return

    for _hook in hooks:
        if webhook_ip in _hook.config["url"]:
            return f"{repository}: Hook already exists - {_hook.config['url']}"

    LOGGER.info(f"Creating webhook: {config['url']} for {repository} with events: {events}")
    repo.create_hook(name="web", config=config, events=events, active=True)
    return f"{repository}: Create webhook is done"


def create_webhook(config_: Config, github_api: Github) -> None:
    LOGGER.info("Preparing webhook configuration")
    webhook_ip = config_.data["webhook_ip"]

    futures = []
    with ThreadPoolExecutor() as executor:
        for repo, data in config_.data["repositories"].items():
            futures.append(
                executor.submit(
                    process_github_webhook,
                    **{"data": data, "github_api": github_api, "webhook_ip": webhook_ip},
                )
            )

    for result in as_completed(futures):
        if result.exception():
            LOGGER.error(result.exception())
        LOGGER.info(result.result())


if __name__ == "__main__":
    config = Config()
    api, _ = get_api_with_highest_rate_limit(config=config)
    create_webhook(config_=config, github_api=api)
