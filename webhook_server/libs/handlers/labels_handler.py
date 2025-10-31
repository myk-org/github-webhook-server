import asyncio
from typing import TYPE_CHECKING

import webcolors
from github.GithubException import UnknownObjectException
from github.Repository import Repository
from timeout_sampler import TimeoutWatch

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
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
from webhook_server.utils.helpers import format_task_fields

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
        self.unified_api = self.github_webhook.unified_api

    async def label_exists_in_pull_request(self, pull_request: PullRequestWrapper, label: str) -> bool:
        return label in await self.pull_request_labels_names(pull_request=pull_request)

    async def pull_request_labels_names(self, pull_request: PullRequestWrapper) -> list[str]:
        labels = pull_request.get_labels()
        return [lb.name for lb in labels]

    async def _remove_label(self, pull_request: PullRequestWrapper, label: str) -> bool:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'processing')} "
            f"Removing label '{label}' from PR",
        )
        self.logger.debug(f"{self.log_prefix} Removing label {label}")
        try:
            if await self.label_exists_in_pull_request(pull_request=pull_request, label=label):
                self.logger.info(f"{self.log_prefix} Removing label {label}")
                owner, repo = self.github_webhook.owner_and_repo

                pr_id = pull_request.id
                owner, repo_name = self.github_webhook.owner_and_repo
                pull_request_data = await self.unified_api.get_pull_request_data(
                    owner=owner, name=repo, number=pull_request.number, include_labels=True
                )
                webhook_format = self.unified_api.convert_graphql_to_webhook(pull_request_data, owner, repo)
                updated_pull_request = PullRequestWrapper(owner=owner, repo_name=repo, webhook_data=webhook_format)
                label_id = [_label.id for _label in updated_pull_request.get_labels() if label == _label.name][0]
                # Remove labels and use mutation response to update wrapper
                # Pass owner/repo/number for automatic retry on stale PR node ID
                result = await self.unified_api.remove_labels(
                    pr_id, [label_id], owner=owner, repo=repo_name, number=pull_request.number
                )
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'completed')} "
                    f"Label '{label}' removed successfully"
                )

                # Extract updated labels from mutation response (avoids refetch)
                if result and "removeLabelsFromLabelable" in result:
                    updated_labels = result["removeLabelsFromLabelable"]["labelable"]["labels"]["nodes"]
                    pull_request.update_labels(updated_labels)
                    self.logger.debug(f"{self.log_prefix} Updated labels in-place from mutation response")

                return await self.wait_for_label(pull_request=pull_request, label=label, exists=False)
        except GraphQLError as ex:
            # Check if error is critical (auth/permission/rate-limit)
            error_str = str(ex).lower()
            if any(keyword in error_str for keyword in ["auth", "permission", "forbidden", "rate limit", "401", "403"]):
                self.logger.exception(f"{self.log_prefix} Critical error removing {label} label")
                raise  # Don't hide auth/permission/rate-limit errors
            else:
                # Transient error or label doesn't exist - log with full traceback for debugging
                self.logger.exception(f"{self.log_prefix} Failed to remove {label} label (may not exist)")
                return False
        except Exception:
            # Handle non-GraphQL errors with full traceback
            self.logger.exception(f"{self.log_prefix} Unexpected error removing {label} label")
            return False

        self.logger.debug(f"{self.log_prefix} Label {label} not found and cannot be removed")
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'completed')} "
            f"Label removal skipped - label '{label}' not found"
        )
        return False

    async def _add_label(self, pull_request: PullRequestWrapper, label: str) -> None:
        label = label.strip()
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'processing')} "
            f"Adding label '{label}' to PR",
        )
        self.logger.debug(f"{self.log_prefix} Adding label {label}")
        if len(label) > 49:
            self.logger.debug(f"{label} is too long, not adding.")
            return

        if await self.label_exists_in_pull_request(pull_request=pull_request, label=label):
            self.logger.debug(f"{self.log_prefix} Label {label} already assigned")
            return

        owner, repo_name = self.github_webhook.owner_and_repo

        if label in STATIC_LABELS_DICT:
            self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
            pr_id = pull_request.id
            label_id = await self.unified_api.get_label_id(owner, repo_name, label)

            if not label_id:
                try:
                    color = STATIC_LABELS_DICT[label]
                    # Optimization: Use webhook data instead of API call
                    repository_id = self.github_webhook.repository_id
                    created_label = await self.unified_api.create_label(repository_id, label, color)
                    label_id = created_label["id"]
                    self.logger.debug(f"{self.log_prefix} Created static label {label} with ID {label_id}")
                except Exception:
                    # Log error but check for critical errors
                    self.logger.exception(f"{self.log_prefix} Failed to create static label {label}")
                    # Still raise on critical errors (auth/permission/rate-limit)
                    raise

            if label_id:
                # Add labels and use mutation response to update wrapper
                result = await self.unified_api.add_labels(pr_id, [label_id])
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'completed')} "
                    f"Label '{label}' added successfully"
                )

                # Extract updated labels from mutation response (avoids refetch)
                if result and "addLabelsToLabelable" in result:
                    updated_labels = result["addLabelsToLabelable"]["labelable"]["labels"]["nodes"]
                    pull_request.update_labels(updated_labels)
                    self.logger.debug(f"{self.log_prefix} Updated labels in-place from mutation response")

            try:
                await self.wait_for_label(pull_request=pull_request, label=label, exists=True)
            except GraphQLError as ex:
                # Check if error is critical (auth/permission/rate-limit)
                error_str = str(ex).lower()
                if any(
                    keyword in error_str for keyword in ["auth", "permission", "forbidden", "rate limit", "401", "403"]
                ):
                    self.logger.exception(f"{self.log_prefix} Critical error waiting for {label} label")
                    raise  # Don't hide auth/permission/rate-limit errors
                else:
                    # Transient error or timeout - log with full traceback for debugging
                    self.logger.exception(f"{self.log_prefix} Wait for {label} label timed out or failed")
            except Exception:
                # Handle non-GraphQL errors with full traceback
                self.logger.exception(f"{self.log_prefix} Unexpected error waiting for {label} label")
            return

        color = self._get_label_color(label)
        _with_color_msg = f"repository label {label} with color {color}"

        try:
            label_id = await self.unified_api.get_label_id(owner, repo_name, label)
            if label_id:
                await self.unified_api.update_label(label_id, color)
                self.logger.debug(f"{self.log_prefix} Edit {_with_color_msg}")
            else:
                # Optimization: Use webhook data instead of API call
                await self.unified_api.create_label(self.github_webhook.repository_id, label, color)
                self.logger.debug(f"{self.log_prefix} Add {_with_color_msg}")

        except GraphQLError as ex:
            # Check if error is critical (auth/permission/rate-limit)
            error_str = str(ex).lower()
            if any(keyword in error_str for keyword in ["auth", "permission", "forbidden", "rate limit", "401", "403"]):
                self.logger.exception(f"{self.log_prefix} Critical error managing {label} label")
                raise  # Don't hide auth/permission/rate-limit errors
            else:
                # Transient error or label doesn't exist - log with full traceback for debugging
                self.logger.exception(f"{self.log_prefix} Failed to manage {label} label (may be transient)")
        except UnknownObjectException:
            # Label not found, create it (expected condition, not an error)
            self.logger.debug(f"{self.log_prefix} Label {label} not found, creating it")
            # Optimization: Use webhook data instead of API call
            await self.unified_api.create_label(self.github_webhook.repository_id, label, color)
            self.logger.debug(f"{self.log_prefix} Add {_with_color_msg}")
        except Exception:
            # Handle non-GraphQL errors with full traceback
            self.logger.exception(f"{self.log_prefix} Unexpected error managing {label} label")
            raise

        self.logger.info(f"{self.log_prefix} Adding pull request label {label}")
        pr_id = pull_request.id
        label_id = await self.unified_api.get_label_id(owner, repo_name, label)
        if label_id:
            # Add labels and use mutation response to update wrapper
            result = await self.unified_api.add_labels(pr_id, [label_id])
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'completed')} "
                f"Label '{label}' added successfully"
            )

            # Extract updated labels from mutation response (avoids refetch)
            if result and "addLabelsToLabelable" in result:
                updated_labels = result["addLabelsToLabelable"]["labelable"]["labels"]["nodes"]
                pull_request.update_labels(updated_labels)
                self.logger.debug(f"{self.log_prefix} Updated labels in-place from mutation response")

        await self.wait_for_label(pull_request=pull_request, label=label, exists=True)

    async def wait_for_label(self, pull_request: PullRequestWrapper, label: str, exists: bool) -> bool:
        self.logger.debug(f"{self.log_prefix} waiting for label {label} to {'exist' if exists else 'not exist'}")
        owner, repo_name = self.github_webhook.owner_and_repo

        # Create TimeoutWatch once outside the loop to track total elapsed time
        watch = TimeoutWatch(timeout=30)
        backoff_seconds = 0.5
        max_backoff = 5

        while watch.remaining_time() > 0:
            # First check current labels (might already be updated from mutation response)
            res = await self.label_exists_in_pull_request(pull_request=pull_request, label=label)
            if res == exists:
                return True

            # Only refetch if label not found and we have time remaining
            if watch.remaining_time() > 0:
                # Re-fetch labels to check for eventual consistency
                refreshed_pr_data = await self.unified_api.get_pull_request_data(
                    owner, repo_name, pull_request.number, include_labels=True
                )
                webhook_format = self.unified_api.convert_graphql_to_webhook(refreshed_pr_data, owner, repo_name)
                refreshed_pr = PullRequestWrapper(owner=owner, repo_name=repo_name, webhook_data=webhook_format)
                res = await self.label_exists_in_pull_request(pull_request=refreshed_pr, label=label)
                if res == exists:
                    return True

                # Exponential backoff with cap
                sleep_time = min(backoff_seconds, max_backoff, watch.remaining_time())
                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)
                    backoff_seconds = min(backoff_seconds * 2, max_backoff)

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
            for _, label_name, color_hex in thresholds:
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
                return "d3d3d3"  # lightgray hex #d3d3d3

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
            # Return static defaults directly to avoid infinite recursion
            return [
                (20, "XS", "ededed"),
                (50, "S", "0E8A16"),
                (100, "M", "F09C74"),
                (300, "L", "F5621C"),
                (500, "XL", "D93F0B"),
                (float("inf"), "XXL", "B60205"),
            ]

        return sorted_thresholds

    def get_size(self, pull_request: PullRequestWrapper) -> str:
        """Calculates size label based on additions and deletions."""

        # Handle None values by defaulting to 0
        additions = pull_request.additions if pull_request.additions is not None else 0
        deletions = pull_request.deletions if pull_request.deletions is not None else 0
        size = additions + deletions
        self.logger.debug(f"{self.log_prefix} PR size is {size} (additions: {additions}, deletions: {deletions})")

        thresholds = self._get_custom_pr_size_thresholds()

        for threshold, label_name, _ in thresholds:
            if size < threshold:
                return f"{SIZE_LABEL_PREFIX}{label_name}"

        # If we reach here, PR is larger than all thresholds, use the largest category
        if thresholds:
            _, largest_label, _ = thresholds[-1]
            return f"{SIZE_LABEL_PREFIX}{largest_label}"

        # Fallback (should not happen due to our default handling)
        return f"{SIZE_LABEL_PREFIX}XL"

    async def add_size_label(self, pull_request: PullRequestWrapper) -> None:
        """Add a size label to the pull request based on its additions and deletions."""
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'processing')} "
            f"Calculating and applying PR size label",
        )
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
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('labels', 'pr_management', 'processing')} "
            f"Applied size label '{size_label}' to PR",
        )

    async def label_by_user_comment(
        self,
        pull_request: PullRequestWrapper,
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
        self, pull_request: PullRequestWrapper, review_state: str, action: str, reviewed_user: str
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

    def wip_or_hold_labels_exists(self, labels: list[str]) -> str:
        failure_output = ""

        if HOLD_LABEL_STR in labels:
            self.logger.debug(f"{self.log_prefix} Hold label exists.")
            failure_output += "Hold label exists.\n"

        if WIP_STR in labels:
            self.logger.debug(f"{self.log_prefix} WIP label exists.")
            failure_output += "WIP label exists.\n"

        return failure_output
