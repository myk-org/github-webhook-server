import os
from typing import Any

import yaml


class Config:
    def __init__(self, repository: str | None = None) -> None:
        self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
        self.config_path: str = os.path.join(self.data_dir, "config.yaml")
        self.exists()
        self.repository = repository

    def exists(self) -> None:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    @property
    def data(self) -> dict[str, Any]:
        with open(self.config_path) as fd:
            return yaml.safe_load(fd)

    @property
    def repository_data(self) -> dict[str, Any]:
        return self.data["repositories"].get(self.repository, {})

    def get_value(self, value: str, return_on_none: Any = None) -> Any:
        """
        Get value from config, try first from repository and if not exists get it from root config
        """
        _val = self.repository_data.get(value)

        if _val is None:
            _val = self.data.get(value)

        return _val or return_on_none
