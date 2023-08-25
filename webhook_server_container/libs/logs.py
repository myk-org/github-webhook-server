import json
import random

from webhook_server_container.utils.constants import FLASK_APP


class Logs:
    def __init__(self, repository_name, pull_request=None, token=None):
        self.logger = FLASK_APP.logger
        self.repository_name = repository_name
        self.pull_request = pull_request
        self.token = token
        self.log_prefix_with_color = None
        self._set_log_prefix_color()

    def _set_log_prefix_color(self):
        repo_str = "\033[1;{color}m{name}\033[1;0m"
        color_file = "/tmp/color.json"
        try:
            with open(color_file) as fd:
                color_json = json.load(fd)
        except Exception:
            color_json = {}

        color = color_json.get(self.repository_name)
        if not color:
            color = random.choice(range(31, 39))
            color_json[self.repository_name] = color

        self.log_prefix_with_color = repo_str.format(
            color=color, name=self.repository_name
        )

        with open(color_file, "w") as fd:
            json.dump(color_json, fd)

    @property
    def log_prefix(self):
        return (
            f"{self.log_prefix_with_color}[PR {self.pull_request.number}]:"
            if self.pull_request
            else f"{self.log_prefix_with_color}:"
        )

    def hash_token(self, message):
        hashed_message = message.replace(self.token, "*****")
        return hashed_message

    def app_logger_info(self, message):
        hashed_message = self.hash_token(message=message)
        self.logger.info(hashed_message)

    def app_logger_error(self, message):
        hashed_message = self.hash_token(message=message)
        self.logger.error(hashed_message)
