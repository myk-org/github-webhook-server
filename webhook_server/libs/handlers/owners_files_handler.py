import asyncio
import traceback
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import yaml
from github.GithubException import GithubException
from github.Repository import Repository
from gql.transport.exceptions import TransportError, TransportQueryError, TransportServerError

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.graphql_wrappers import PullRequestWrapper
from webhook_server.utils.constants import COMMAND_ADD_ALLOWED_USER_STR, ROOT_APPROVERS_KEY
from webhook_server.utils.helpers import format_task_fields

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class OwnersFileNotInitializedError(RuntimeError):
    """Raised when OwnersFileHandler is used before initialization."""


class OwnersFileNotFoundError(FileNotFoundError):
    """Raised when OWNERS file is not found at expected path."""


class OwnersFileHandler:
    def __init__(self, github_webhook: "GithubWebhook") -> None:
        self.github_webhook = github_webhook
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.unified_api = self.github_webhook.unified_api
        self.config = self.github_webhook.config
        self.max_owners_files = self.config.get_value("max-owners-files", return_on_none=1000)

    def _get_owner_and_repo(self) -> tuple[str, str]:
        """Extract owner and repository name from full repository name.

        Returns:
            Tuple of (owner, repo_name).

        Raises:
            ValueError: If repository full_name is malformed.
        """
        parts = self.repository.full_name.split("/", 1)
        if len(parts) != 2:
            raise ValueError(  # noqa: TRY003
                f"Invalid repository full_name format: {self.repository.full_name}"
            )
        return parts[0], parts[1]

    async def initialize(self, pull_request: PullRequestWrapper) -> "OwnersFileHandler":
        self.changed_files = await self.list_changed_files(pull_request=pull_request)
        self.all_repository_approvers_and_reviewers = await self.get_all_repository_approvers_and_reviewers(
            pull_request=pull_request
        )
        self.all_repository_approvers = await self.get_all_repository_approvers()
        self.all_repository_reviewers = await self.get_all_repository_reviewers()
        self.all_pull_request_approvers = await self.get_all_pull_request_approvers()
        self.all_pull_request_reviewers = await self.get_all_pull_request_reviewers()

        # Use pre-fetched repository data from webhook processing (no API calls)
        # Convert raw GraphQL dict data to objects with .login and .permissions attributes
        # Fallback to fetching if repository_data is not available (edge case)
        if not hasattr(self.github_webhook, "repository_data") or not self.github_webhook.repository_data:
            owner, repo_name = self._get_owner_and_repo()
            self.github_webhook.repository_data = await self.unified_api.get_comprehensive_repository_data(
                owner, repo_name
            )

        collaborators_data = self.github_webhook.repository_data["collaborators"]["edges"]
        self._repository_collaborators = [
            SimpleNamespace(
                login=collab["node"]["login"],
                permissions=SimpleNamespace(
                    admin=(collab["permission"] == "ADMIN"), maintain=(collab["permission"] == "MAINTAIN")
                ),
            )
            for collab in collaborators_data
        ]

        contributors_data = self.github_webhook.repository_data["mentionableUsers"]["nodes"]
        self._repository_contributors = [SimpleNamespace(login=contrib["login"]) for contrib in contributors_data]

        # Cache valid users to avoid repeated API calls
        self._valid_users_to_run_commands = {
            *{val.login for val in self._repository_collaborators},
            *{val.login for val in self._repository_contributors},
            *self.all_repository_approvers,
            *self.all_pull_request_reviewers,
        }

        return self

    def _ensure_initialized(self) -> None:
        """Verify that initialize() has been called before using instance methods.

        Raises:
            OwnersFileNotInitializedError: If initialize() has not been called yet.
        """
        if not hasattr(self, "changed_files"):
            raise OwnersFileNotInitializedError("initialize() must be called first")

    @property
    def root_reviewers(self) -> list[str]:
        """Get reviewers from the root OWNERS file.

        Returns:
            List of reviewer usernames from the root (.) OWNERS file, or empty list if not defined.
        """
        self._ensure_initialized()

        _reviewers = self.all_repository_approvers_and_reviewers.get(".", {}).get("reviewers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Reviewers: {_reviewers}")
        return _reviewers

    @property
    def root_approvers(self) -> list[str]:
        """Get approvers from the root OWNERS file.

        Returns:
            List of approver usernames from the root (.) OWNERS file, or empty list if not defined.
        """
        self._ensure_initialized()

        _approvers = self.all_repository_approvers_and_reviewers.get(".", {}).get("approvers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Approvers: {_approvers}")
        return _approvers

    @property
    def allowed_users(self) -> list[str]:
        """Get allowed users from the root OWNERS file.

        Returns:
            List of allowed usernames from the root (.) OWNERS file, or empty list if not defined.
            These users are integrated into command validation via is_user_valid_to_run_commands.
        """
        self._ensure_initialized()

        _allowed_users = self.all_repository_approvers_and_reviewers.get(".", {}).get("allowed-users", [])
        self.logger.debug(f"{self.log_prefix} ROOT allowed users: {_allowed_users}")
        return _allowed_users

    async def list_changed_files(self, pull_request: PullRequestWrapper) -> list[str]:
        # Use unified_api for get_files
        owner, repo_name = self._get_owner_and_repo()
        files = await self.unified_api.get_pull_request_files(owner, repo_name, pull_request.number)
        changed_files = [_file.filename for _file in files]
        self.logger.debug(f"{self.log_prefix} Changed files: {changed_files}")
        return changed_files

    def _validate_owners_content(self, content: Any, path: str) -> bool:
        """Validate OWNERS file content structure.

        Returns False with warning logs instead of raising exceptions for control flow.
        """
        if not isinstance(content, dict):
            self.logger.warning(f"{self.log_prefix} Invalid OWNERS file {path}: content must be a dictionary")
            return False

        for key in ["approvers", "reviewers"]:
            if key in content:
                if not isinstance(content[key], list):
                    self.logger.warning(f"{self.log_prefix} Invalid OWNERS file {path}: {key} must be a list")
                    return False

                if not all(isinstance(_elm, str) for _elm in content[key]):
                    self.logger.warning(f"{self.log_prefix} Invalid OWNERS file {path}: all {key} must be strings")
                    return False

        return True

    async def _get_file_content(self, content_path: str, pull_request: PullRequestWrapper) -> tuple[str, str]:
        """Fetch OWNERS file content using GraphQL API.

        Args:
            content_path: Path to OWNERS file in repository
            pull_request: Pull request wrapper with base ref information

        Returns:
            Tuple of (file_content_string, content_path)

        Raises:
            OwnersFileNotFoundError: If file not found at path
        """
        self.logger.debug(f"{self.log_prefix} Get OWNERS file from {content_path}")

        owner, repo_name = self._get_owner_and_repo()
        # Use GraphQL get_file_contents which returns decoded string directly
        file_content = await self.unified_api.get_file_contents(owner, repo_name, content_path, pull_request.base.ref)

        if not file_content:
            raise OwnersFileNotFoundError(f"Not found at {content_path} in ref {pull_request.base.ref}")

        return file_content, content_path

    async def get_all_repository_approvers_and_reviewers(
        self, pull_request: PullRequestWrapper
    ) -> dict[str, dict[str, Any]]:
        # Dictionary mapping OWNERS file paths to their approvers and reviewers
        _owners: dict[str, dict[str, Any]] = {}
        tasks: list[Coroutine[Any, Any, Any]] = []

        owners_count = 0

        self.logger.debug(f"{self.log_prefix} Get git tree")
        owner, repo_name = self._get_owner_and_repo()
        tree = await self.unified_api.get_git_tree(owner, repo_name, pull_request.base.ref)

        for element in tree["tree"]:
            if element["type"] == "blob" and element["path"].endswith("OWNERS"):
                owners_count += 1
                if owners_count > self.max_owners_files:
                    self.logger.error(
                        f"{self.log_prefix} Too many OWNERS files (>{self.max_owners_files}), "
                        "stopping processing to avoid performance issues"
                    )
                    break

                content_path = element["path"]
                self.logger.debug(f"{self.log_prefix} Found OWNERS file: {content_path}")
                tasks.append(self._get_file_content(content_path, pull_request))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            # Skip exceptions from failed OWNERS file fetches
            if isinstance(result, Exception):
                exc_info = (type(result), result, result.__traceback__)
                self.logger.error(f"{self.log_prefix} Failed to fetch OWNERS file", exc_info=exc_info)
                continue
            # Type narrowing: result is tuple[str, str] after exception check (file_content, content_path)
            file_content, _content_path = result  # type: ignore[misc]

            try:
                content = yaml.safe_load(file_content)
                if self._validate_owners_content(content, _content_path):
                    parent_path = str(Path(_content_path).parent)
                    if not parent_path:
                        parent_path = "."
                    _owners[parent_path] = content

            except yaml.YAMLError:
                self.logger.exception(f"{self.log_prefix} Invalid YAML in OWNERS file {_content_path}")
                continue

        return _owners

    async def get_all_repository_approvers(self) -> list[str]:
        self._ensure_initialized()

        _approvers = [
            approver
            for value in self.all_repository_approvers_and_reviewers.values()
            if "approvers" in value
            for approver in value["approvers"]
        ]

        self.logger.debug(f"{self.log_prefix} All repository approvers: {_approvers}")
        return _approvers

    async def get_all_repository_reviewers(self) -> list[str]:
        """
        Get all reviewers from repository OWNERS files.

        Returns:
            List of reviewer usernames
        """
        self._ensure_initialized()

        _reviewers = [
            reviewer
            for value in self.all_repository_approvers_and_reviewers.values()
            if "reviewers" in value
            for reviewer in value["reviewers"]
        ]

        self.logger.debug(f"{self.log_prefix} All repository reviewers: {_reviewers}")
        return _reviewers

    async def get_all_pull_request_approvers(self) -> list[str]:
        """
        Get all approvers required for the current pull request based on changed files.

        Returns:
            Sorted list of unique approver usernames
        """
        _approvers: list[str] = []
        changed_files = await self.owners_data_for_changed_files()

        for list_of_approvers in changed_files.values():
            for _approver in list_of_approvers.get("approvers", []):
                _approvers.append(_approver)

        _approvers = list(set(_approvers))
        _approvers.sort()
        self.logger.debug(f"{self.log_prefix} All pull request approvers: {_approvers}")
        return _approvers

    async def get_all_pull_request_reviewers(self) -> list[str]:
        """
        Get all reviewers required for the current pull request based on changed files.

        Returns:
            Sorted list of unique reviewer usernames
        """
        _reviewers: list[str] = []
        changed_files = await self.owners_data_for_changed_files()

        for list_of_reviewers in changed_files.values():
            for _reviewer in list_of_reviewers.get("reviewers", []):
                _reviewers.append(_reviewer)

        _reviewers = list(set(_reviewers))
        _reviewers.sort()
        self.logger.debug(f"{self.log_prefix} Pull request reviewers are: {_reviewers}")
        return _reviewers

    async def owners_data_for_changed_files(self) -> dict[str, dict[str, Any]]:
        self._ensure_initialized()

        data: dict[str, dict[str, Any]] = {}

        changed_folders = {Path(cf).parent for cf in self.changed_files}
        self.logger.debug(f"{self.log_prefix} Changed folders: {changed_folders}")

        changed_folder_match: list[Path] = []

        # Track if ANY matched folder requires root approvers
        # Default to None (no matches yet), then True if any folder requires it
        require_root_approvers: bool | None = None

        for owners_dir, owners_data in self.all_repository_approvers_and_reviewers.items():
            if owners_dir == ".":
                continue

            _owners_dir = Path(owners_dir)

            for changed_folder in changed_folders:
                if changed_folder == _owners_dir or _owners_dir in changed_folder.parents:
                    data[owners_dir] = owners_data
                    changed_folder_match.append(_owners_dir)
                    self.logger.debug(
                        f"{self.log_prefix} Matched changed folder: {changed_folder} with owners dir: {_owners_dir}"
                    )
                    # Include root if ANY matched folder requires it (not just first match)
                    folder_requires_root = owners_data.get(ROOT_APPROVERS_KEY, True)
                    if require_root_approvers is None:
                        require_root_approvers = folder_requires_root
                    elif folder_requires_root:
                        # If any folder requires root, override False from previous matches
                        require_root_approvers = True

        if require_root_approvers or require_root_approvers is None:
            self.logger.debug(
                f"{self.log_prefix} Including root OWNERS approvers/reviewers (not disabled by {ROOT_APPROVERS_KEY})"
            )
            data["."] = self.all_repository_approvers_and_reviewers.get(".", {})

        else:
            # Check if all changed folders are covered by matched OWNERS files
            all_covered = all(
                any(
                    _folder == _changed_path or _changed_path in _folder.parents
                    for _changed_path in changed_folder_match
                )
                for _folder in changed_folders
            )
            if not all_covered:
                self.logger.debug(f"{self.log_prefix} Adding root approvers for uncovered folders")
                data["."] = self.all_repository_approvers_and_reviewers.get(".", {})

        self.logger.debug(f"{self.log_prefix} Final owners data for changed files: {data}")
        return data

    async def assign_reviewers(self, pull_request: PullRequestWrapper) -> None:
        self._ensure_initialized()

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'started')} "
            f"Starting reviewer assignment based on OWNERS files",
        )
        self.logger.info(f"{self.log_prefix} Assign reviewers")

        _to_add: list[str] = list(set(self.all_pull_request_reviewers))
        self.logger.debug(f"{self.log_prefix} Reviewers to add: {', '.join(_to_add)}")

        if _to_add:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} "
                f"Assigning {len(_to_add)} reviewers to PR",
            )
        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} "
                f"No reviewers to assign",
            )
            return

        # Filter out PR author from reviewers list
        reviewers_to_request = [r for r in _to_add if r != pull_request.user.login]

        if not reviewers_to_request:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} "
                f"No reviewers to assign (all were PR author)",
            )
            return

        # Batch review request in one mutation instead of looping
        try:
            self.logger.debug(f"{self.log_prefix} Batch requesting reviews from: {', '.join(reviewers_to_request)}")
            await self.github_webhook.unified_api.request_pr_reviews(pull_request, reviewers_to_request)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'completed')} "
                f"Successfully assigned {len(reviewers_to_request)} reviewers",
            )

        except (
            GithubException,
            GraphQLError,
            TransportError,
            TransportQueryError,
            TransportServerError,
        ) as ex:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} "
                f"Failed to assign reviewers in batch",
            )
            self.logger.debug(
                f"{self.log_prefix} Batch review request failed with traceback:\n{traceback.format_exc()}"
            )
            # Best-effort error comment - don't let this failure mask the original exception
            try:
                error_type = type(ex).__name__
                # Sanitized message - no exception details in PR comment
                # Use add_pr_comment since we already have PullRequestWrapper - avoids extra GraphQL lookup
                await self.github_webhook.unified_api.add_pr_comment(
                    pull_request, f"Failed to assign reviewers {', '.join(reviewers_to_request)}: [{error_type}]"
                )
            except (
                GithubException,
                GraphQLError,
                TransportError,
                TransportQueryError,
                TransportServerError,
            ):
                self.logger.debug(f"{self.log_prefix} Failed to post error comment about reviewer assignment failure")

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'completed')} "
            f"Reviewer assignment completed",
        )

    async def is_user_valid_to_run_commands(self, pull_request: PullRequestWrapper, reviewed_user: str) -> bool:
        self._ensure_initialized()

        # Include ROOT allowed-users in approval flow
        _allowed_user_to_approve = (
            await self.get_all_repository_maintainers() + self.all_repository_approvers + self.allowed_users
        )
        allowed_user_to_approve = list(set(_allowed_user_to_approve))
        allow_user_comment = f"/{COMMAND_ADD_ALLOWED_USER_STR} @{reviewed_user}"

        comment_msg = f"""
{reviewed_user} is not allowed to run retest commands.
maintainers can allow it by comment `{allow_user_comment}`
Maintainers:
 - {"\n - ".join(allowed_user_to_approve)}
"""
        valid_users = self.valid_users_to_run_commands
        self.logger.debug(f"Valid users to run commands: {valid_users}")

        if reviewed_user not in valid_users:
            # Use unified_api for get_issue_comments
            owner, repo_name = self._get_owner_and_repo()
            comments = await self.unified_api.get_issue_comments(owner, repo_name, pull_request.number)
            for comment in [_comment for _comment in comments if _comment.user.login in allowed_user_to_approve]:
                if allow_user_comment in comment.body:
                    self.logger.debug(
                        f"{self.log_prefix} {reviewed_user} is approved by {comment.user.login} to run commands"
                    )
                    return True

            self.logger.debug(f"{self.log_prefix} {reviewed_user} is not in {valid_users}")
            await self.github_webhook.unified_api.add_pr_comment(pull_request, comment_msg)
            return False

        return True

    @property
    def valid_users_to_run_commands(self) -> set[str]:
        self._ensure_initialized()
        return self._valid_users_to_run_commands.copy()

    async def get_all_repository_contributors(self) -> list[str]:
        self._ensure_initialized()
        return [val.login for val in self._repository_contributors]

    async def get_all_repository_collaborators(self) -> list[str]:
        self._ensure_initialized()
        return [val.login for val in self._repository_collaborators]

    async def get_all_repository_maintainers(self) -> list[str]:
        self._ensure_initialized()
        maintainers: list[str] = []

        for user in self._repository_collaborators:
            permissions = user.permissions
            self.logger.debug(f"User {user.login} permissions: {permissions}")

            if permissions.admin or permissions.maintain:
                maintainers.append(user.login)

        self.logger.debug(f"Maintainers: {maintainers}")
        return maintainers
