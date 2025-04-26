import os
from typing import Any

import yaml
from simple_logger.logger import get_logger

LOGGER = get_logger(name="config")


class Config:
    def __init__(self, repository: str | None = None, repository_full_name: str | None = None) -> None:
        self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
        self.config_path: str = os.path.join(self.data_dir, "config.yaml")
        self.exists()
        self.repository = repository
        self.repository_full_name = repository_full_name

    def exists(self) -> None:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    @property
    def root_data(self) -> dict[str, Any]:
        try:
            with open(self.config_path) as fd:
                return yaml.safe_load(fd)
        except Exception:
            LOGGER.error("Config file is empty")
            return {}

    @property
    def repository_data(self) -> dict[str, Any]:
        return self.root_data.get("repositories", {}).get(self.repository, {})

    def get_value(self, value: str, return_on_none: Any = None) -> Any:
        """
        Get value from config

        Order of getting value:
            1. Local repository file (.github-webhook-server.yaml)
            2. Repository level global config file (config.yaml)
            3. Root level global config file (config.yaml)
        """

        for scope in (self.repository_local_data, self.repository_data, self.root_data):
            if value in scope:
                value_data = scope[value]
                if value_data is not None:
                    return value_data

        return return_on_none

    @property
    def repository_local_data(self) -> dict[str, Any]:
        if self.repository and self.repository_full_name:
            from webhook_server.utils.helpers import get_api_with_highest_rate_limit, get_github_repo_api

            github_api, _, _ = get_api_with_highest_rate_limit(config=self, repository_name=self.repository)

            if github_api:
                try:
                    repo = get_github_repo_api(github_api=github_api, repository=self.repository_full_name)
                    _path = repo.get_contents(",github-webhook-server.yaml")
                    config_file = _path[0] if isinstance(_path, list) else _path
                    yaml.safe_load(config_file.decoded_content)

                except Exception:
                    return {}

        return {}
