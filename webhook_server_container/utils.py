import os

import yaml


def get_repository_from_config():
    config_file = os.environ.get("WEBHOOK_CONFIG_FILE", "/config/config.yaml")
    with open(config_file) as fd:
        repos = yaml.safe_load(fd)
    return repos
