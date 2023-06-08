import os
from functools import wraps
from time import sleep

import yaml
from constants import FLASK_APP
from github.GithubException import RateLimitExceededException, UnknownObjectException


def get_github_repo_api(gapi, repository):
    try:
        repo = gapi.get_repo(repository)
    except (UnknownObjectException, RateLimitExceededException) as ex:
        if ex == UnknownObjectException:
            FLASK_APP.logger.error(
                f"Repository {repository}: Not found or token invalid"
            )
        else:
            FLASK_APP.logger.error(f"Repository {repository}: Rate limit exceeded")
        return
    return repo


def get_repository_from_config():
    config_file = os.environ.get("WEBHOOK_CONFIG_FILE", "/config/config.yaml")
    with open(config_file) as fd:
        repos = yaml.safe_load(fd)
    return repos


def extract_key_from_dict(key, _dict):
    if isinstance(_dict, dict):
        for _key, _val in _dict.items():
            if _key == key:
                yield _val
            if isinstance(_val, dict):
                for result in extract_key_from_dict(key, _val):
                    yield result
            elif isinstance(_val, list):
                for _item in _val:
                    for result in extract_key_from_dict(key, _item):
                        yield result


def ignore_exceptions(logger=None, retry=None):
    def wrapper(func):
        @wraps(func)
        def inner(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as ex:
                if retry:
                    for _ in range(0, retry):
                        try:
                            return func(*args, **kwargs)
                        except Exception:
                            sleep(1)

                if logger:
                    logger.info(ex)
                return None

        return inner

    return wrapper
