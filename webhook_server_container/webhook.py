import os
from multiprocessing import Process

import gitlab
from constants import ALL_LABELS_DICT, STATIC_LABELS_DICT
from github.GithubException import UnknownObjectException
from gitlab_api import GitLabApi
from utils import get_github_repo_api, get_repository_from_config


def process_github_webhook(app, config, data, repository, webhook_ip):
    token = data["token"]
    events = data.get("events", ["*"])
    repo = get_github_repo_api(app=app, token=token, repository=repository)
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


def process_gitlab_webhook(app, config, data, repository, webhook_ip):
    events = data.get("events", [])
    container_gitlab_config = "/python-gitlab/python-gitlab.cfg"
    if os.path.isfile(container_gitlab_config):
        config_files = [container_gitlab_config]
    else:
        config_files = [os.path.join(os.path.expanduser("~"), "python-gitlab.cfg")]

    gitlab_api = gitlab.Gitlab.from_config(config_files=config_files)
    gitlab_api.auth()
    try:
        project_id = data["project_id"]
        project = gitlab_api.projects.get(project_id)

    except UnknownObjectException:
        app.logger.info(f"Repository {repository} not found or token invalid")
        return

    try:
        for _hook in project.hooks.list():
            hook_exists = webhook_ip in _hook.url
            if hook_exists:
                app.logger.info(
                    f"Deleting existing webhook for {repository}: {_hook.url}"
                )
                _hook.delete()

        app.logger.info(
            f"Creating webhook: {config['url']} for {repository} with events: {events}"
        )
        hook_data = {event: True for event in events}
        hook_data["url"] = config["url"]
        hook_data["enable_ssl_verification"] = False
        project.hooks.create(hook_data)

        for label_name, label_color in STATIC_LABELS_DICT.items():
            label_color = f"#{label_color}"
            GitLabApi.add_update_label(
                project=project, label_color=label_color, label_name=label_name
            )

    except Exception:
        return


def create_webhook(app):
    app.logger.info("Preparing webhook configuration")
    repos = get_repository_from_config()

    procs = []
    for repo, data in repos["repositories"].items():
        webhook_ip = data["webhook_ip"]
        config = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}

        _type = data["type"]
        repository = data["name"]
        _args = (
            app,
            config,
            data,
            repository,
            webhook_ip,
        )

        if _type == "github":
            proc = Process(target=process_github_webhook, args=_args)
            procs.append(proc)
            proc.start()

        elif _type == "gitlab":
            proc = Process(target=process_gitlab_webhook, args=_args)
            procs.append(proc)
            proc.start()

    return procs
