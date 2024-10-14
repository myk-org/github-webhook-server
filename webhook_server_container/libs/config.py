import os
from typing import Any, Dict

import yaml


class Config:
    def __init__(self) -> None:
        self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
        self.config_path: str = os.path.join(self.data_dir, "config.yaml")
        self.exists()

    def exists(self) -> None:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    @property
    def data(self) -> Dict[str, Any]:
        with open(self.config_path) as fd:
            return yaml.safe_load(fd)

    def repository_data(self, repository_name: str) -> Dict[str, Any]:
        return self.data["repositories"].get(repository_name, {})
