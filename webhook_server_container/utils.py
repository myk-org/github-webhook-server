import os

import yaml
from github.GithubException import UnknownObjectException


def get_github_repo_api(gapi, app, repository):
    try:
        repo = gapi.get_repo(repository)
    except UnknownObjectException:
        app.logger.info(f"Repository {repository} not found or token invalid")
        return
    return repo


def get_repository_from_config():
    config_file = os.environ.get("WEBHOOK_CONFIG_FILE", "/config/config.yaml")
    with open(config_file) as fd:
        repos = yaml.safe_load(fd)
    return repos
