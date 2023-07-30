from multiprocessing import Process

from github import Github

from webhook_server_container.utils.constants import FLASK_APP
from webhook_server_container.utils.helpers import (
    get_github_repo_api,
    get_repository_from_config,
    ignore_exceptions,
)


@ignore_exceptions()
def process_github_webhook(data):
    token = data["token"]
    repository = data["name"]
    gapi = Github(login_or_token=token)
    repo = get_github_repo_api(gapi=gapi, repository=repository)
    if not repo:
        FLASK_APP.logger.error(f"Could not find repository {repository}")
        return

    webhook_ip = data["webhook_ip"]
    config = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}
    events = data.get("events", ["*"])

    try:
        hooks = list(repo.get_hooks())
    except Exception as ex:
        FLASK_APP.logger.error(f"Could not create webhook for {repository}: {ex}")
        return

    for _hook in hooks:
        hook_exists = webhook_ip in _hook.config["url"]
        if hook_exists:
            FLASK_APP.logger.info(
                f"Deleting existing webhook for {repository}: {_hook.config['url']}"
            )
            _hook.delete()

    FLASK_APP.logger.info(
        f"Creating webhook: {config['url']} for {repository} with events: {events}"
    )
    repo.create_hook(name="web", config=config, events=events, active=True)


def create_webhook():
    FLASK_APP.logger.info("Preparing webhook configuration")
    repos = get_repository_from_config()

    procs = []
    for repo, data in repos["repositories"].items():
        proc = Process(target=process_github_webhook, kwargs={"data": data})
        procs.append(proc)
        proc.start()

    return procs
