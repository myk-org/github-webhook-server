import os

import yaml


class Config:
    def __init__(self):
        self.data_dir = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/webhook_server")
        self.config_path = os.path.join(self.data_dir, "config.yaml")
        self.exists()

    def exists(self):
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    @property
    def data(self):
        return self.get_data_from_config()

    def get_data_from_config(self):
        with open(self.config_path) as fd:
            return yaml.safe_load(fd)

    def get_repository(self, repository_name):
        return self.data["repositories"].get(repository_name)
