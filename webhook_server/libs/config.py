import os
from logging import Logger
from typing import Any

import github
import yaml
from github.GithubException import UnknownObjectException
from simple_logger.logger import get_logger


class Config:
    def __init__(
        self,
        logger: Logger | None = None,
        repository: str | None = None,
    ) -> None:
        self.logger = logger or get_logger(name="config")
        self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
        self.config_path: str = os.path.join(self.data_dir, "config.yaml")
        self.repository = repository
        self.exists()
        self.repositories_exists()

    def exists(self) -> None:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    def repositories_exists(self) -> None:
        if not self.root_data.get("repositories"):
            raise ValueError(f"Config {self.config_path} does not have `repositories`")

    @property
    def root_data(self) -> dict[str, Any]:
        try:
            with open(self.config_path) as fd:
                return yaml.safe_load(fd) or {}
        except FileNotFoundError:
            # Since existence is validated in __init__, this indicates a race condition.
            # Re-raise to propagate the error rather than returning empty dict.
            self.logger.exception(f"Config file not found: {self.config_path}")
            raise
        except yaml.YAMLError:
            self.logger.exception(f"Config file has invalid YAML syntax: {self.config_path}")
            raise  # Don't continue with invalid config
        except PermissionError:
            self.logger.exception(f"Permission denied reading config file: {self.config_path}")
            raise
        except Exception:
            self.logger.exception(f"Failed to load config file {self.config_path}")
            raise

    @property
    def repository_data(self) -> dict[str, Any]:
        return self.root_data["repositories"].get(self.repository, {})

    def repository_local_data(self, github_api: github.Github, repository_full_name: str) -> dict[str, Any]:
        if self.repository and repository_full_name:
            try:
                # Directly use github_api.get_repo instead of importing get_github_repo_api
                # to avoid circular dependency with helpers.py
                self.logger.debug(f"Get GitHub API for repository {repository_full_name}")
                repo = github_api.get_repo(repository_full_name)
                try:
                    _path = repo.get_contents(".github-webhook-server.yaml")
                except UnknownObjectException:
                    return {}

                config_file = _path[0] if isinstance(_path, list) else _path
                repo_config = yaml.safe_load(config_file.decoded_content)
                return repo_config

            except yaml.YAMLError:
                self.logger.exception(f"Repository {repository_full_name} config has invalid YAML syntax")
                raise  # Don't continue with invalid config

            except Exception:
                self.logger.exception(f"Repository {repository_full_name} config file not found or error")
                return {}

        self.logger.error("self.repository or self.repository_full_name is not defined")
        return {}

    def get_value(self, value: str, return_on_none: Any = None, extra_dict: dict[str, Any] | None = None) -> Any:
        """
        Get value from config

        Order of getting value:
            1. Local repository file (.github-webhook-server.yaml)
            2. Repository level global config file (config.yaml)
            3. Root level global config file (config.yaml)
        """
        if extra_dict and extra_dict.get(value):
            value = extra_dict[value]
            if value is not None:
                return value

        for scope in (self.repository_data, self.root_data):
            if value in scope:
                value_data = scope[value]
                if value_data is not None:
                    return value_data

        return return_on_none
