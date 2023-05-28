from multiprocessing import Process

from constants import ALL_LABELS_DICT
from github import Github
from utils import get_github_repo_api, get_repository_from_config


def process_github_webhook(app, config, data, repository, webhook_ip):
    token = data["token"]
    events = data.get("events", ["*"])
    gapi = Github(login_or_token=token)
    repo = get_github_repo_api(gapi=gapi, app=app, repository=repository)
    if not repo:
        return

    try:
        for _hook in repo.get_hooks():
            hook_exists = webhook_ip in _hook.config["url"]
            if hook_exists:
                app.logger.info(
                    f"Deleting existing webhook for {repository}: {_hook.config['url']}"
                )
                _hook.delete()

        app.logger.info(
            f"Creating webhook: {config['url']} for {repository} with events: {events}"
        )
        repo.create_hook("web", config, events, active=True)
        for label in repo.get_labels():
            label_name = label.name.lower()
            if label_name in ALL_LABELS_DICT:
                label.edit(label.name, color=ALL_LABELS_DICT[label_name])

    except Exception:
        return


def create_webhook(app):
    app.logger.info("Preparing webhook configuration")
    repos = get_repository_from_config()

    procs = []
    for repo, data in repos["repositories"].items():
        webhook_ip = data["webhook_ip"]
        config = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}
        repository = data["name"]
        _args = (
            app,
            config,
            data,
            repository,
            webhook_ip,
        )

        proc = Process(target=process_github_webhook, args=_args)
        procs.append(proc)
        proc.start()

    return procs
