import asyncio
from typing import TYPE_CHECKING

import webcolors
from github.GithubException import UnknownObjectException
from github.PullRequest import PullRequest
from github.Repository import Repository
from timeout_sampler import TimeoutWatch

from webhook_server.libs.owners_files_handler import OwnersFileHandler
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

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class LabelsHandler:
    def __init__(self, github_webhook: "GithubWebhook", owners_file_handler: OwnersFileHandler) -> None:
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository

    async def label_exists_in_pull_request(self, pull_request: PullRequest, label: str) -> bool:
        return label in await self.pull_request_labels_names(pull_request=pull_request)

    async def pull_request_labels_names(self, pull_request: PullRequest) -> list[str]:
        labels = await asyncio.to_thread(pull_request.get_labels)
        return [lb.name for lb in labels]

    async def _remove_label(self, pull_request: PullRequest, label: str) -> bool:
        self.logger.step(f"{self.log_prefix} Removing label '{label}' from PR")  # type: ignore
        self.logger.debug(f"{self.log_prefix} Removing label {label}")
        try:
            if await self.label_exists_in_pull_request(pull_request=pull_request, label=label):
                self.logger.info(f"{self.log_prefix} Removing label {label}")
                await asyncio.to_thread(pull_request.remove_from_labels, label)
                return await self.wait_for_label(pull_request=pull_request, label=label, exists=False)
        except Exception as exp:
            self.logger.debug(f"{self.log_prefix} Failed to remove {label} label. Exception: {exp}")
            return False

        self.logger.debug(f"{self.log_prefix} Label {label} not found and cannot be removed")
        return False

    async def _add_label(self, pull_request: PullRequest, label: str) -> None:
        label = label.strip()
        self.logger.step(f"{self.log_prefix} Adding label '{label}' to PR")  # type: ignore
        self.logger.debug(f"{self.log_prefix} Adding label {label}")
        if len(label) > 49:
            self.logger.debug(f"{label} is too long, not adding.")
            return

        if await self.label_exists_in_pull_request(pull_request=pull_request, label=label):
            self.logger.debug(f"{self.log_prefix} Label {label} already assign")
            return

        if label in STATIC_LABELS_DICT:
            self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
            await asyncio.to_thread(pull_request.add_to_labels, label)
            return

        color = self._get_label_color(label)
        _with_color_msg = f"repository label {label} with color {color}"

        try:
            _repo_label = await asyncio.to_thread(self.repository.get_label, label)
            await asyncio.to_thread(_repo_label.edit, name=_repo_label.name, color=color)
            self.logger.debug(f"{self.log_prefix} Edit {_with_color_msg}")

        except UnknownObjectException:
            self.logger.debug(f"{self.log_prefix} Add {_with_color_msg}")
            await asyncio.to_thread(self.repository.create_label, name=label, color=color)

        self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
        await asyncio.to_thread(pull_request.add_to_labels, label)
        await self.wait_for_label(pull_request=pull_request, label=label, exists=True)

    async def wait_for_label(self, pull_request: PullRequest, label: str, exists: bool) -> bool:
        self.logger.debug(f"{self.log_prefix} waiting for label {label} to {'exists' if exists else 'not exists'}")
        while TimeoutWatch(timeout=30).remaining_time() > 0:
            res = await self.label_exists_in_pull_request(pull_request=pull_request, label=label)
            if res == exists:
                return True

            await asyncio.sleep(5)

        self.logger.debug(f"{self.log_prefix} Label {label} {'not found' if exists else 'found'}")
        return False

    def _get_label_color(self, label: str) -> str:
        """Get the appropriate color for a label.

        For size labels with custom thresholds, uses the custom color.
        For other dynamic labels, uses the DYNAMIC_LABELS_DICT.
        Falls back to default color if not found.
        """
        if label.startswith(SIZE_LABEL_PREFIX):
            size_name = label[len(SIZE_LABEL_PREFIX) :]

            thresholds = self._get_custom_pr_size_thresholds()
            for threshold, label_name, color_hex in thresholds:
                if label_name == size_name:
                    return color_hex

            # If not found in custom thresholds, check static labels dict
            # (for backward compatibility with static size labels)
            if label in STATIC_LABELS_DICT:
                return STATIC_LABELS_DICT[label]

        _color = [DYNAMIC_LABELS_DICT[_label] for _label in DYNAMIC_LABELS_DICT if _label in label]
        if _color:
            return _color[0]

        return "D4C5F9"

    def _get_color_hex(self, color_name: str, default_color: str = "lightgray") -> str:
        """Convert CSS3 color name to hex value, with fallback to default."""
        try:
            # Remove '#' prefix if present and convert to hex
            return webcolors.name_to_hex(color_name).lstrip("#")
        except ValueError:
            # Invalid color name, use default
            self.logger.debug(f"{self.log_prefix} Invalid color name '{color_name}', using default '{default_color}'")
            try:
                return webcolors.name_to_hex(default_color).lstrip("#")
            except ValueError:
                # Fallback to hardcoded hex if default color name fails
                return "d3d3d3"  # lightgray hex

    def _get_custom_pr_size_thresholds(self) -> list[tuple[int | float, str, str]]:
        """Get custom PR size thresholds from configuration with fallback to static defaults.

        Returns:
            List of tuples (threshold, label_name, color_hex) sorted by threshold.
        """
        custom_config = self.github_webhook.config.get_value("pr-size-thresholds", return_on_none=None)

        if not custom_config:
            return [
                (20, "XS", "ededed"),
                (50, "S", "0E8A16"),
                (100, "M", "F09C74"),
                (300, "L", "F5621C"),
                (500, "XL", "D93F0B"),
                (float("inf"), "XXL", "B60205"),
            ]

        thresholds = []
        for label_name, config in custom_config.items():
            threshold = config.get("threshold")
            if threshold is None or not isinstance(threshold, int) or threshold <= 0:
                self.logger.warning(f"{self.log_prefix} Invalid threshold for '{label_name}': {threshold}")
                continue

            color_name = config.get("color", "lightgray")
            color_hex = self._get_color_hex(color_name)

            thresholds.append((threshold, label_name, color_hex))

        # Sort by threshold value and ensure we have at least one threshold
        sorted_thresholds = sorted(thresholds, key=lambda x: x[0])

        if not sorted_thresholds:
            self.logger.warning(f"{self.log_prefix} No valid custom thresholds found, using static defaults")
            return self._get_custom_pr_size_thresholds()  # Recursive call will return static defaults

        return sorted_thresholds

    def get_size(self, pull_request: PullRequest) -> str:
        """Calculates size label based on additions and deletions."""

        # Handle None values by defaulting to 0
        additions = pull_request.additions if pull_request.additions is not None else 0
        deletions = pull_request.deletions if pull_request.deletions is not None else 0
        size = additions + deletions
        self.logger.debug(f"{self.log_prefix} PR size is {size} (additions: {additions}, deletions: {deletions})")

        # Get custom or default thresholds
        thresholds = self._get_custom_pr_size_thresholds()

        # Find the appropriate size category
        for threshold, label_name, _ in thresholds:
            if size < threshold:
                return f"{SIZE_LABEL_PREFIX}{label_name}"

        # If we reach here, PR is larger than all thresholds, use the largest category
        if thresholds:
            _, largest_label, _ = thresholds[-1]
            return f"{SIZE_LABEL_PREFIX}{largest_label}"

        # Fallback (should not happen due to our default handling)
        return f"{SIZE_LABEL_PREFIX}XL"

    async def add_size_label(self, pull_request: PullRequest) -> None:
        """Add a size label to the pull request based on its additions and deletions."""
        self.logger.step(f"{self.log_prefix} Calculating and applying PR size label")  # type: ignore
        size_label = self.get_size(pull_request=pull_request)
        self.logger.debug(f"{self.log_prefix} size label is {size_label}")
        if not size_label:
            self.logger.debug(f"{self.log_prefix} Size label not found")
            return

        if size_label in await self.pull_request_labels_names(pull_request=pull_request):
            return

        exists_size_label = [
            label
            for label in await self.pull_request_labels_names(pull_request=pull_request)
            if label.startswith(SIZE_LABEL_PREFIX)
        ]

        if exists_size_label:
            self.logger.debug(f"{self.log_prefix} Found existing size label {exists_size_label}, removing it.")
            await self._remove_label(pull_request=pull_request, label=exists_size_label[0])

        await self._add_label(pull_request=pull_request, label=size_label)
        self.logger.step(f"{self.log_prefix} Applied size label '{size_label}' to PR")  # type: ignore

    async def label_by_user_comment(
        self,
        pull_request: PullRequest,
        user_requested_label: str,
        remove: bool,
        reviewed_user: str,
    ) -> None:
        self.logger.debug(
            f"{self.log_prefix} {DELETE_STR if remove else ADD_STR} "
            f"label requested by user {reviewed_user}: {user_requested_label}"
        )

        if user_requested_label in (LGTM_STR, APPROVE_STR):
            await self.manage_reviewed_by_label(
                pull_request=pull_request,
                review_state=user_requested_label,
                action=DELETE_STR if remove else ADD_STR,
                reviewed_user=reviewed_user,
            )

        else:
            label_func = self._remove_label if remove else self._add_label
            await label_func(pull_request=pull_request, label=user_requested_label)

    async def manage_reviewed_by_label(
        self, pull_request: PullRequest, review_state: str, action: str, reviewed_user: str
    ) -> None:
        self.logger.info(
            f"{self.log_prefix} "
            f"Processing label for review from {reviewed_user}. "
            f"review_state: {review_state}, action: {action}"
        )
        label_prefix: str = ""
        label_to_remove: str = ""
        self.logger.debug(f"{self.log_prefix} label_prefix is {label_prefix}, label_to_remove is {label_to_remove}")

        if review_state == APPROVE_STR:
            if (
                reviewed_user
                in self.owners_file_handler.all_pull_request_approvers + self.owners_file_handler.root_approvers
            ):
                label_prefix = APPROVED_BY_LABEL_PREFIX
                label_to_remove = f"{CHANGED_REQUESTED_BY_LABEL_PREFIX}{reviewed_user}"
                self.logger.debug(
                    f"{self.log_prefix} User {reviewed_user} is approver, setting label prefix to "
                    f"{label_prefix} and label to remove to {label_to_remove}"
                )

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
            self.logger.debug(
                f"{self.log_prefix} Setting label prefix to {label_prefix} and label to remove to {label_to_remove}"
            )

        elif review_state == "changes_requested":
            label_prefix = CHANGED_REQUESTED_BY_LABEL_PREFIX
            _remove_label = LGTM_BY_LABEL_PREFIX
            label_to_remove = _remove_label
            self.logger.debug(
                f"{self.log_prefix} Setting label prefix to {label_prefix} and label to remove to {label_to_remove}"
            )

        elif review_state == "commented":
            label_prefix = COMMENTED_BY_LABEL_PREFIX
            self.logger.debug(f"{self.log_prefix} Setting label prefix to {label_prefix}")

        if label_prefix:
            reviewer_label = f"{label_prefix}{reviewed_user}"

            if action == ADD_STR:
                self.logger.debug(f"{self.log_prefix} Adding reviewer label {reviewer_label}")
                await self._add_label(pull_request=pull_request, label=reviewer_label)
                await self._remove_label(pull_request=pull_request, label=label_to_remove)

            if action == DELETE_STR:
                self.logger.debug(f"{self.log_prefix} Removing reviewer label {reviewer_label}")
                await self._remove_label(pull_request=pull_request, label=reviewer_label)
        else:
            self.logger.warning(
                f"{self.log_prefix} PR {pull_request.number} got unsupported review state: {review_state}"
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
