from multiprocessing import Process

from github import Github

from webhook_server_container.utils.constants import ALL_LABELS_DICT, FLASK_APP
from webhook_server_container.utils.helpers import (
    get_github_repo_api,
    get_repository_from_config,
    ignore_exceptions,
)


@ignore_exceptions()
def process_github_webhook(config, data, repository, webhook_ip):
    token = data["token"]
    events = data.get("events", ["*"])
    gapi = Github(login_or_token=token)
    repo = get_github_repo_api(gapi=gapi, repository=repository)
    if not repo:
        return

    for _hook in repo.get_hooks():
        hook_exists = webhook_ip in _hook.config["url"]
        if hook_exists:
            FLASK_APP.logger.info(
                f"Deleting existing webhook for {repository}: {_hook.config['url']}"
            )
            _hook.delete()

    FLASK_APP.logger.info(
        f"Creating webhook: {config['url']} for {repository} with events: {events}"
    )
    repo.create_hook("web", config, events, active=True)
    for label in repo.get_labels():
        label_name = label.name.lower()
        if label_name in ALL_LABELS_DICT:
            label.edit(label.name, color=ALL_LABELS_DICT[label_name])


def create_webhook():
    FLASK_APP.logger.info("Preparing webhook configuration")
    repos = get_repository_from_config()

    procs = []
    for repo, data in repos["repositories"].items():
        webhook_ip = data["webhook_ip"]
        config = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}
        repository = data["name"]
        _args = (
            config,
            data,
            repository,
            webhook_ip,
        )

        proc = Process(target=process_github_webhook, args=_args)
        procs.append(proc)
        proc.start()

    return procs
