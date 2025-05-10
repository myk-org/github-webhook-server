from typing import Any

from github.GithubException import UnknownObjectException
from timeout_sampler import TimeoutExpiredError, TimeoutSampler

from webhook_server.utils.constants import (
    ADD_STR,
    APPROVE_STR,
    APPROVED_BY_LABEL_PREFIX,
    CHANGED_REQUESTED_BY_LABEL_PREFIX,
    COMMENTED_BY_LABEL_PREFIX,
    DELETE_STR,
    DYNAMIC_LABELS_DICT,
    HOLD_LABEL_STR,
    LGTM_BY_LABEL_PREFIX,
    LGTM_STR,
    SIZE_LABEL_PREFIX,
    STATIC_LABELS_DICT,
    WIP_STR,
)


class LabelsHandler:
    def __init__(self, github_webhook: Any) -> None:
        self.github_webhook = github_webhook
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.pull_request = self.github_webhook.pull_request

    def label_exists_in_pull_request(self, label: str) -> bool:
        return any(lb for lb in self.pull_request_labels_names() if lb == label)

    def pull_request_labels_names(self) -> list[str]:
        return [lb.name for lb in self.pull_request.labels] if self.pull_request else []

    def _remove_label(self, label: str) -> bool:
        try:
            if self.label_exists_in_pull_request(label=label):
                self.logger.info(f"{self.log_prefix} Removing label {label}")
                self.pull_request.remove_from_labels(label)
                return self.wait_for_label(label=label, exists=False)
        except Exception as exp:
            self.logger.debug(f"{self.log_prefix} Failed to remove {label} label. Exception: {exp}")
            return False

        self.logger.debug(f"{self.log_prefix} Label {label} not found and cannot be removed")
        return False

    def _add_label(self, label: str) -> None:
        label = label.strip()
        if len(label) > 49:
            self.logger.debug(f"{label} is to long, not adding.")
            return

        if self.label_exists_in_pull_request(label=label):
            self.logger.debug(f"{self.log_prefix} Label {label} already assign")
            return

        if label in STATIC_LABELS_DICT:
            self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
            self.pull_request.add_to_labels(label)
            return

        _color = [DYNAMIC_LABELS_DICT[_label] for _label in DYNAMIC_LABELS_DICT if _label in label]
        self.logger.debug(f"{self.log_prefix} Label {label} was {'found' if _color else 'not found'} in labels dict")
        color = _color[0] if _color else "D4C5F9"
        _with_color_msg = f"repository label {label} with color {color}"

        try:
            _repo_label = self.repository.get_label(label)
            _repo_label.edit(name=_repo_label.name, color=color)
            self.logger.debug(f"{self.log_prefix} Edit {_with_color_msg}")

        except UnknownObjectException:
            self.logger.debug(f"{self.log_prefix} Add {_with_color_msg}")
            self.repository.create_label(name=label, color=color)

        self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
        self.pull_request.add_to_labels(label)
        self.wait_for_label(label=label, exists=True)

    def wait_for_label(self, label: str, exists: bool) -> bool:
        try:
            for sample in TimeoutSampler(
                wait_timeout=30,
                sleep=5,
                func=self.label_exists_in_pull_request,
                label=label,
            ):
                if sample == exists:
                    return True

        except TimeoutExpiredError:
            self.logger.debug(f"{self.log_prefix} Label {label} {'not found' if exists else 'found'}")

        return False

    def get_size(self) -> str:
        """Calculates size label based on additions and deletions."""

        size = self.pull_request.additions + self.pull_request.deletions

        # Define label thresholds in a more readable way
        threshold_sizes = [20, 50, 100, 300, 500]
        prefixes = ["XS", "S", "M", "L", "XL"]

        for i, size_threshold in enumerate(threshold_sizes):
            if size < size_threshold:
                _label = prefixes[i]
                return f"{SIZE_LABEL_PREFIX}{_label}"

        return f"{SIZE_LABEL_PREFIX}XXL"

    def add_size_label(self) -> None:
        """Add a size label to the pull request based on its additions and deletions."""
        size_label = self.get_size()
        if not size_label:
            self.logger.debug(f"{self.log_prefix} Size label not found")
            return

        if size_label in self.pull_request_labels_names():
            return

        exists_size_label = [label for label in self.pull_request_labels_names() if label.startswith(SIZE_LABEL_PREFIX)]

        if exists_size_label:
            self._remove_label(label=exists_size_label[0])

        self._add_label(label=size_label)

    def label_by_user_comment(
        self,
        user_requested_label: str,
        remove: bool,
        reviewed_user: str,
    ) -> None:
        self.logger.debug(
            f"{self.log_prefix} {DELETE_STR if remove else ADD_STR} "
            f"label requested by user {reviewed_user}: {user_requested_label}"
        )

        if user_requested_label in (LGTM_STR, APPROVE_STR):
            self.manage_reviewed_by_label(
                review_state=user_requested_label,
                action=DELETE_STR if remove else ADD_STR,
                reviewed_user=reviewed_user,
            )

        else:
            label_func = self._remove_label if remove else self._add_label
            label_func(label=user_requested_label)

    def manage_reviewed_by_label(self, review_state: str, action: str, reviewed_user: str) -> None:
        self.logger.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix: str = ""
        label_to_remove: str = ""

        if review_state == APPROVE_STR:
            if reviewed_user in self.github_webhook.all_pull_request_approvers:
                label_prefix = APPROVED_BY_LABEL_PREFIX
                label_to_remove = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"

            else:
                self.logger.debug(f"{self.log_prefix} {reviewed_user} not in approvers list, will not {action} label.")
                return

        elif review_state in ("approved", LGTM_STR):
            if base_dict := self.hook_data.get("issue", self.hook_data.get("pull_request")):
                pr_owner = base_dict["user"]["login"]
                if pr_owner == reviewed_user:
                    self.logger.info(f"{self.log_prefix} PR owner {pr_owner} set /lgtm, not adding label.")
                    return

            _remove_label = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"
            label_prefix = LGTM_BY_LABEL_PREFIX
            label_to_remove = _remove_label

        elif review_state == "changes_requested":
            label_prefix = CHANGED_REQUESTED_BY_LABEL_PREFIX
            _remove_label = LGTM_BY_LABEL_PREFIX
            label_to_remove = _remove_label

        elif review_state == "commented":
            label_prefix = COMMENTED_BY_LABEL_PREFIX

        if label_prefix:
            reviewer_label = f"{label_prefix}{reviewed_user}"

            if action == ADD_STR:
                self._add_label(label=reviewer_label)
                self._remove_label(label=label_to_remove)

            if action == DELETE_STR:
                self._remove_label(label=reviewer_label)
        else:
            self.logger.warning(
                f"{self.log_prefix} PR {self.pull_request.number} got unsupported review state: {review_state}"
            )

    def wip_or_hold_lables_exists(self, labels: list[str]) -> str:
        failure_output = ""

        if HOLD_LABEL_STR in labels:
            self.logger.debug(f"{self.log_prefix} Hold label exists.")
            failure_output += "Hold label exists.\n"

        if WIP_STR in labels:
            self.logger.debug(f"{self.log_prefix} WIP label exists.")
            failure_output += "WIP label exists.\n"

        return failure_output
