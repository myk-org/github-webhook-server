from github import UnknownObjectException

from webhook_server_container.libs.logs import Logs
from webhook_server_container.libs.repositories_config import RepositoriesConfig
from webhook_server_container.utils.constants import (
    DYNAMIC_LABELS_DICT,
    SIZE_LABEL_PREFIX,
    STATIC_LABELS_DICT,
)
from webhook_server_container.utils.helpers import (
    decorate_all_in_module,
    sleep_if_rate_limit_is_low,
)


decorate_all_in_module(".", sleep_if_rate_limit_is_low)


class Labels(RepositoriesConfig):
    def __init__(
        self, hook_data, github_event, repositories_app_api, missing_app_repositories
    ):
        super().__init__(
            hook_data=hook_data,
            github_event=github_event,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )

        log = Logs(repository_name=self.repository_name, token=self.token)
        self.logger = log.logger
        self.log_prefix = log.log_prefix

        self.logger.info(f"{self.log_prefix} Check rate limit")

    def label_exists_in_pull_request(self, label, pull_request):
        return any(
            lb
            for lb in self.pull_request_labels_names(pull_request=pull_request)
            if lb == label
        )

    @staticmethod
    def pull_request_labels_names(pull_request):
        return [lb.name for lb in pull_request.labels]

    def remove_label(self, label, pull_request):
        if self.label_exists_in_pull_request(label=label, pull_request=pull_request):
            self.logger.info(f"{self.log_prefix} Removing label {label}")
            return pull_request.remove_from_labels(label)

        self.logger.warning(
            f"{self.log_prefix} Label {label} not found and cannot be removed"
        )

    def add_label(self, label, pull_request):
        label = label.strip()
        if len(label) > 49:
            self.logger.warning(f"{label} is to long, not adding.")
            return

        if self.label_exists_in_pull_request(label=label, pull_request=pull_request):
            self.logger.info(
                f"{self.log_prefix} Label {label} already assign to PR {pull_request.number}"
            )
            return

        if label in STATIC_LABELS_DICT:
            self.logger.info(
                f"{self.log_prefix} Adding pull request label {label} to {pull_request.number}"
            )
            return pull_request.add_to_labels(label)

        _color = [
            DYNAMIC_LABELS_DICT[_label]
            for _label in DYNAMIC_LABELS_DICT
            if _label in label
        ]
        self.logger.info(
            f"{self.log_prefix} Label {label} was "
            f"{'found' if _color else 'not found'} in labels dict"
        )
        color = _color[0] if _color else "D4C5F9"
        self.logger.info(f"{self.log_prefix} Adding label {label} with color {color}")

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            self.logger.info(
                f"{self.log_prefix} "
                f"Edit repository label {label} with color {color}"
            )
        except UnknownObjectException:
            self.logger.info(
                f"{self.log_prefix} Add repository label {label} with color {color}"
            )
            self.repository.create_label(name=label, color=color)

        self.logger.info(
            f"{self.log_prefix} Adding pull request label {label} to {pull_request.number}"
        )
        return pull_request.add_to_labels(label)

    def add_size_label(self, pull_request):
        size = pull_request.additions + pull_request.deletions
        if size < 20:
            _label = "XS"

        elif size < 50:
            _label = "S"

        elif size < 100:
            _label = "M"

        elif size < 300:
            _label = "L"

        elif size < 500:
            _label = "XL"

        else:
            _label = "XXL"

        size_label = f"{SIZE_LABEL_PREFIX}{_label}"

        if size_label in self.pull_request_labels_names(pull_request=pull_request):
            return

        exists_size_label = [
            label
            for label in self.pull_request_labels_names(pull_request=pull_request)
            if label.startswith(SIZE_LABEL_PREFIX)
        ]

        if exists_size_label:
            self.remove_label(label=exists_size_label[0], pull_request=pull_request)

        self.add_label(label=size_label, pull_request=pull_request)
