import os
import time

import gitlab
import yaml
from constants import ALL_LABELS_DICT, STATIC_LABELS_DICT
from github import Github
from github.GithubException import UnknownObjectException
from gitlab_api import GitLabApi
from selenium import webdriver
from selenium.common.exceptions import NoSuchElementException
from urllib3.exceptions import MaxRetryError


def _get_firefox_driver(app):
    try:
        app.logger.info("Get firefox driver")
        firefox_options = webdriver.FirefoxOptions()
        firefox_options.headless = True
        return webdriver.Remote("http://firefox:4444", options=firefox_options)
    except (ConnectionRefusedError, MaxRetryError):
        app.logger.info("Retrying to get firefox driver")
        time.sleep(5)
        return _get_firefox_driver(app=app)


def _get_ngrok_config(app):
    driver = _get_firefox_driver(app=app)
    try:
        app.logger.info("Get ngrok configuration")
        driver.get("http://ngrok:4040/status")
        ngrok_url = driver.find_element(
            "xpath",
            '//*[@id="app"]/div/div/div/div[1]/div[1]/ul/li/div/table/tbody/tr[1]/td',
        ).text
        driver.close()
        return {"url": f"{ngrok_url}/webhook_server", "content_type": "json"}
    except NoSuchElementException:
        app.logger.info("Retrying to get ngrok configuration")
        time.sleep(5)
        _get_ngrok_config(app=app)


def process_github_webhook(app, config, data, repository, use_ngrok, webhook_ip):
    token = data["token"]
    events = data.get("events", ["*"])
    app.logger.info(f"Creating webhook for {repository}")
    gapi = Github(login_or_token=token)
    try:
        repo = gapi.get_repo(repository)
    except UnknownObjectException:
        app.logger.info(f"Repository {repository} not found or token invalid")
        return

    try:
        for _hook in repo.get_hooks():
            if use_ngrok:
                hook_exists = "ngrok.io" in _hook.config["url"]
            else:
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

    except UnknownObjectException:
        return


def process_gitlab_webhook(app, config, data, repository, use_ngrok, webhook_ip):
    events = data.get("events", [])
    app.logger.info(f"Creating webhook for {repository}")
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
            if use_ngrok:
                hook_exists = "ngrok.io" in _hook.url
            else:
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

    except UnknownObjectException:
        return


def create_webhook(app):
    app.logger.info("Preparing webhook configuration")
    config_file = os.environ.get("WEBHOOK_CONFIG_FILE", "/config/config.yaml")
    with open(config_file) as fd:
        repos = yaml.safe_load(fd)

    for repo, data in repos["repositories"].items():
        webhook_ip = data["webhook_ip"]
        use_ngrok = webhook_ip == "ngrok"
        if use_ngrok:
            config = _get_ngrok_config(app=app)
        else:
            config = {"url": f"{webhook_ip}/webhook_server", "content_type": "json"}

        _type = data["type"]
        repository = data["name"]
        if _type == "github":
            process_github_webhook(
                app=app,
                config=config,
                data=data,
                repository=repository,
                use_ngrok=use_ngrok,
                webhook_ip=webhook_ip,
            )

        if _type == "gitlab":
            process_gitlab_webhook(
                app=app,
                config=config,
                data=data,
                repository=repository,
                use_ngrok=use_ngrok,
                webhook_ip=webhook_ip,
            )


# if __name__ == "__main__":
#     create_webhook()
