import os

import yaml
from github.GithubException import RateLimitExceededException, UnknownObjectException


def get_github_repo_api(gapi, app, repository):
    try:
        repo = gapi.get_repo(repository)
    except (UnknownObjectException, RateLimitExceededException) as ex:
        if ex == UnknownObjectException:
            app.logger.error(f"Repository {repository}: Not found or token invalid")
        else:
            app.logger.error(f"Repository {repository}: Rate limit exceeded")
        return
    return repo


def get_repository_from_config():
    config_file = os.environ.get("WEBHOOK_CONFIG_FILE", "/config/config.yaml")
    with open(config_file) as fd:
        repos = yaml.safe_load(fd)
    return repos
