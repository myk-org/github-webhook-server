import asyncio
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, Any, Coroutine

import yaml
from github.ContentFile import ContentFile
from github.GithubException import GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.graphql_wrappers import PullRequestWrapper
from webhook_server.utils.constants import COMMAND_ADD_ALLOWED_USER_STR, ROOT_APPROVERS_KEY
from webhook_server.utils.helpers import format_task_fields

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


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
        """
        return self.repository.full_name.split("/")

    async def initialize(self, pull_request: PullRequest | PullRequestWrapper) -> "OwnersFileHandler":
        self.changed_files = await self.list_changed_files(pull_request=pull_request)
        self.all_repository_approvers_and_reviewers = await self.get_all_repository_approvers_and_reviewers(
            pull_request=pull_request
        )
        self.all_repository_approvers = await self.get_all_repository_approvers()
        self.all_repository_reviewers = await self.get_all_repository_reviewers()
        self.all_pull_request_approvers = await self.get_all_pull_request_approvers()
        self.all_pull_request_reviewers = await self.get_all_pull_request_reviewers()

        # Cache collaborators and contributors during initialization
        owner, repo_name = self._get_owner_and_repo()
        self._repository_collaborators = await self.unified_api.get_collaborators(owner, repo_name)
        self._repository_contributors = await self.unified_api.get_contributors(owner, repo_name)

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
            RuntimeError: If initialize() has not been called yet.
        """
        if not hasattr(self, "changed_files"):
            raise RuntimeError("OwnersFileHandler.initialize() must be called before using this method")

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
        """Validate OWNERS file content structure."""
        try:
            if not isinstance(content, dict):
                raise ValueError("OWNERS file must contain a dictionary")

            for key in ["approvers", "reviewers"]:
                if key in content:
                    if not isinstance(content[key], list):
                        raise ValueError(f"{key} must be a list")

                    if not all(isinstance(_elm, str) for _elm in content[key]):
                        raise ValueError(f"All {key} must be strings")

            return True

        except ValueError as e:
            self.logger.error(f"{self.log_prefix} Invalid OWNERS file {path}: {e}")
            return False

    async def _get_file_content(self, content_path: str, pull_request: PullRequestWrapper) -> tuple[ContentFile, str]:
        self.logger.debug(f"{self.log_prefix} Get OWNERS file from {content_path}")

        owner, repo_name = self._get_owner_and_repo()
        _path = await self.unified_api.get_contents(owner, repo_name, content_path, pull_request.base.ref)

        if isinstance(_path, list):
            if not _path:
                raise FileNotFoundError(f"OWNERS file not found at {content_path} in ref {pull_request.base.ref}")
            _path = _path[0]

        return _path, content_path

    async def get_all_repository_approvers_and_reviewers(
        self, pull_request: PullRequestWrapper
    ) -> dict[str, dict[str, Any]]:
        # Dictionary mapping OWNERS file paths to their approvers and reviewers
        _owners: dict[str, dict[str, Any]] = {}
        tasks: list[Coroutine[Any, Any, Any]] = []

        owners_count = 0

        self.logger.debug(f"{self.log_prefix} Get git tree")
        owner, repo_name = self._get_owner_and_repo()
        tree = await self.unified_api.get_git_tree(owner, repo_name, pull_request.base.ref, recursive=True)

        for element in tree.tree:
            if element.type == "blob" and element.path.endswith("OWNERS"):
                owners_count += 1
                if owners_count > self.max_owners_files:
                    self.logger.error(
                        f"{self.log_prefix} Too many OWNERS files (>{self.max_owners_files}), "
                        "stopping processing to avoid performance issues"
                    )
                    break

                content_path = element.path
                self.logger.debug(f"{self.log_prefix} Found OWNERS file: {content_path}")
                tasks.append(self._get_file_content(content_path, pull_request))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            # Skip exceptions from failed OWNERS file fetches
            if isinstance(result, Exception):
                exception_type = type(result).__name__
                self.logger.exception(
                    f"{self.log_prefix} Failed to fetch OWNERS file: [{exception_type}] {result}",
                    exc_info=(type(result), result, result.__traceback__),
                )
                continue
            # Type narrowing: result is tuple[ContentFile, str] after exception check
            _path, _content_path = result  # type: ignore[misc]

            try:
                content = yaml.safe_load(_path.decoded_content)
                if self._validate_owners_content(content, _content_path):
                    parent_path = str(Path(_content_path).parent)
                    if not parent_path:
                        parent_path = "."
                    _owners[parent_path] = content

            except yaml.YAMLError as exp:
                self.logger.error(f"{self.log_prefix} Invalid OWNERS file {_content_path}: {exp}")
                continue

        return _owners

    async def get_all_repository_approvers(self) -> list[str]:
        self._ensure_initialized()

        _approvers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "approvers":
                    _approvers.extend(val)

        self.logger.debug(f"{self.log_prefix} All repository approvers: {_approvers}")
        return _approvers

    async def get_all_repository_reviewers(self) -> list[str]:
        """
        Get all reviewers from repository OWNERS files.

        Returns:
            List of reviewer usernames
        """
        self._ensure_initialized()

        _reviewers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "reviewers":
                    _reviewers.extend(val)

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
        self.logger.debug(f"Pull request reviewers are: {_reviewers}")
        return _reviewers

    async def owners_data_for_changed_files(self) -> dict[str, dict[str, Any]]:
        self._ensure_initialized()

        data: dict[str, dict[str, Any]] = {}

        changed_folders = {Path(cf).parent for cf in self.changed_files}
        self.logger.debug(f"Changed folders: {changed_folders}")

        changed_folder_match: list[Path] = []

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
                    if require_root_approvers is None:
                        require_root_approvers = owners_data.get(ROOT_APPROVERS_KEY, True)

        if require_root_approvers or require_root_approvers is None:
            self.logger.debug(
                f"{self.log_prefix} Including root OWNERS approvers/reviewers (not disabled by {ROOT_APPROVERS_KEY})"
            )
            data["."] = self.all_repository_approvers_and_reviewers.get(".", {})

        else:
            for _folder in changed_folders:
                for _changed_path in changed_folder_match:
                    if _folder == _changed_path or _changed_path in _folder.parents:
                        continue
                    else:
                        self.logger.debug(f"Adding root approvers for {_folder}")
                        data["."] = self.all_repository_approvers_and_reviewers.get(".", {})
                        break

        self.logger.debug(f"Final owners data for changed files: {data}")
        return data

    async def assign_reviewers(self, pull_request: PullRequestWrapper) -> None:
        self._ensure_initialized()

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'started')} Starting reviewer assignment based on OWNERS files",
        )
        self.logger.info(f"{self.log_prefix} Assign reviewers")

        _to_add: list[str] = list(set(self.all_pull_request_reviewers))
        self.logger.debug(f"{self.log_prefix} Reviewers to add: {', '.join(_to_add)}")

        if _to_add:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} Assigning {len(_to_add)} reviewers to PR",
            )
        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} No reviewers to assign",
            )
            return

        # Filter out PR author from reviewers list
        reviewers_to_request = [r for r in _to_add if r != pull_request.user.login]

        if not reviewers_to_request:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} No reviewers to assign (all were PR author)",
            )
            return

        # Batch review request in one mutation instead of looping
        try:
            self.logger.debug(f"{self.log_prefix} Batch requesting reviews from: {', '.join(reviewers_to_request)}")
            await self.github_webhook.request_pr_reviews(pull_request, reviewers_to_request)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'completed')} Successfully assigned {len(reviewers_to_request)} reviewers",
            )

        except (GithubException, GraphQLError) as ex:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} Failed to assign reviewers in batch",
            )
            self.logger.debug(
                f"{self.log_prefix} Batch review request failed with traceback:\n{traceback.format_exc()}"
            )
            # Use GraphQL add_comment mutation
            error_type = type(ex).__name__
            await self.unified_api.add_comment(
                pull_request.id,
                f"Failed to assign reviewers {', '.join(reviewers_to_request)}: [{error_type}] {ex}",
            )

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'completed')} Reviewer assignment completed",
        )

    async def is_user_valid_to_run_commands(
        self, pull_request: PullRequest | PullRequestWrapper, reviewed_user: str
    ) -> bool:
        self._ensure_initialized()

        _allowed_user_to_approve = await self.get_all_repository_maintainers() + self.all_repository_approvers
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
            await self.github_webhook.add_pr_comment(pull_request, comment_msg)
            return False

        return True

    @property
    def valid_users_to_run_commands(self) -> set[str]:
        self._ensure_initialized()
        return self._valid_users_to_run_commands

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
