import asyncio
import shlex
from collections.abc import Coroutine
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from asyncstdlib import functools
from github.GithubException import GithubException
from github.NamedUser import NamedUser
from github.PaginatedList import PaginatedList
from github.Permissions import Permissions
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.utils.constants import COMMAND_ADD_ALLOWED_USER_STR, ROOT_APPROVERS_KEY
from webhook_server.utils.helpers import run_command

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class OwnersFileHandler:
    def __init__(self, github_webhook: "GithubWebhook") -> None:
        self.github_webhook = github_webhook
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository

    async def initialize(self, pull_request: PullRequest) -> "OwnersFileHandler":
        """Initialize handler with PR data (optimized with parallel operations).

        Phase 1: Fetch independent data in parallel (changed files + OWNERS data)
        Phase 2: Process derived data in parallel (approvers + reviewers)
        """

        # Phase 1: Parallel data fetching - independent GitHub API operations
        self.changed_files, self.all_repository_approvers_and_reviewers = await asyncio.gather(
            self.list_changed_files(pull_request=pull_request),
            self.get_all_repository_approvers_and_reviewers(),
        )

        # Phase 2: Parallel data processing - all depend on phase 1 but independent of each other
        (
            self.all_repository_approvers,
            self.all_repository_reviewers,
            self.all_pull_request_approvers,
            self.all_pull_request_reviewers,
        ) = await asyncio.gather(
            self.get_all_repository_approvers(),
            self.get_all_repository_reviewers(),
            self.get_all_pull_request_approvers(),
            self.get_all_pull_request_reviewers(),
        )

        return self

    def _ensure_initialized(self) -> None:
        if not hasattr(self, "changed_files"):
            raise RuntimeError("OwnersFileHandler.initialize() must be called before using this method")

    @property
    def root_reviewers(self) -> list[str]:
        self._ensure_initialized()

        _reviewers = self.all_repository_approvers_and_reviewers.get(".", {}).get("reviewers", [])
        self.logger.debug(f"{self.log_prefix} ROOT Reviewers: {_reviewers}")
        return _reviewers

    @property
    def root_approvers(self) -> list[str]:
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

    async def list_changed_files(self, pull_request: PullRequest) -> list[str]:
        """List changed files in the PR using git diff on cloned repository.

        Uses local git diff command instead of GitHub API to reduce API calls.
        The repository is already cloned to self.github_webhook.clone_repo_dir.

        Args:
            pull_request: PyGithub PullRequest object

        Returns:
            List of changed file paths relative to repository root
        """
        try:
            # Get base and head SHAs (wrap property accesses in asyncio.to_thread)
            base_sha, head_sha = await asyncio.gather(
                asyncio.to_thread(lambda: pull_request.base.sha),
                asyncio.to_thread(lambda: pull_request.head.sha),
            )

            # Run git diff command on cloned repository
            # Quote clone_repo_dir to handle paths with spaces or special characters
            git_diff_command = (
                f"git -C {shlex.quote(self.github_webhook.clone_repo_dir)} diff --name-only {base_sha}...{head_sha}"
            )

            success, out, _ = await run_command(
                command=git_diff_command,
                log_prefix=self.log_prefix,
                verify_stderr=False,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )

            # Check success flag - return empty list if git diff failed
            if not success:
                self.logger.error(f"{self.log_prefix} git diff command failed")
                return []

            # Parse output: split by newlines and filter empty lines
            changed_files = [line.strip() for line in out.splitlines() if line.strip()]

            self.logger.debug(f"{self.log_prefix} Changed files: {changed_files}")
            return changed_files

        except Exception:
            # Log error and return empty list if git diff fails
            self.logger.exception(f"{self.log_prefix} Failed to get changed files via git diff")
            return []

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

    async def _get_file_content_from_local(
        self, content_path: Path, base_path: Path | None = None
    ) -> tuple[str, str] | None:
        """Read OWNERS file from local cloned repository.

        Args:
            content_path: Path object pointing to OWNERS file in clone_repo_dir
            base_path: Base path to compute relative path from (defaults to clone_repo_dir)

        Returns:
            Tuple of (file_content, relative_path_str) or None if file is unreadable
        """
        _base_path = base_path if base_path else Path(self.github_webhook.clone_repo_dir)
        relative_path = content_path.relative_to(_base_path)
        self.logger.debug(f"{self.log_prefix} Reading OWNERS file from local clone: {relative_path}")

        try:
            # Read file content from local filesystem (wrap in thread pool for I/O)
            file_content = await asyncio.to_thread(content_path.read_text, encoding="utf-8")
            return file_content, str(relative_path)

        except OSError as ex:
            # File may have been deleted or become unreadable between rglob and read_text
            self.logger.warning(
                f"{self.log_prefix} Failed to read OWNERS file {relative_path}: {ex}. Skipping this file."
            )
            return None

        except UnicodeDecodeError as ex:
            # File has invalid encoding - log and skip to allow processing to continue
            self.logger.warning(
                f"{self.log_prefix} OWNERS file {relative_path} has invalid encoding: {ex}. Skipping this file."
            )
            return None

    async def get_all_repository_approvers_and_reviewers(self) -> dict[str, dict[str, Any]]:
        """Get all repository approvers and reviewers from OWNERS files.

        Reads OWNERS files from local cloned repository.
        The clone is already checked out to the base branch by _clone_repository.

        Returns:
            Dictionary mapping OWNERS file paths to their approvers and reviewers
        """
        # Dictionary mapping OWNERS file paths to their approvers and reviewers
        _owners: dict[str, dict[str, Any]] = {}
        tasks: list[Coroutine[Any, Any, Any]] = []

        max_owners_files = 1000  # Intentionally hardcoded limit to prevent runaway processing
        owners_count = 0

        # Clone is already checked out to base branch by _clone_repository

        clone_path = Path(self.github_webhook.clone_repo_dir)

        # Find all OWNERS files via filesystem walk
        self.logger.debug(f"{self.log_prefix} Finding OWNERS files in local clone")

        # Run both git commands in parallel (RULE #0)
        git_branch_cmd = f"git -C {shlex.quote(str(clone_path))} branch --show-current"
        git_log_cmd = f"git -C {shlex.quote(str(clone_path))} log -1 --format=%H%x20%s -- OWNERS"

        branch_task = run_command(
            command=git_branch_cmd,
            log_prefix=self.log_prefix,
            verify_stderr=False,
            mask_sensitive=self.github_webhook.mask_sensitive,
        )
        log_task = run_command(
            command=git_log_cmd,
            log_prefix=self.log_prefix,
            verify_stderr=False,
            mask_sensitive=self.github_webhook.mask_sensitive,
        )

        (branch_success, current_branch, _), (log_success, log_output, _) = await asyncio.gather(branch_task, log_task)

        if branch_success and current_branch.strip():
            self.logger.debug(f"{self.log_prefix} Reading OWNERS files from branch: {current_branch.strip()}")
        if log_success and log_output.strip():
            self.logger.debug(f"{self.log_prefix} Latest OWNERS commit: {log_output.strip()}")

        # Use rglob to recursively find all OWNERS files
        def find_owners_files() -> list[Path]:
            return [
                p
                for p in clone_path.rglob("OWNERS")
                if not any(part.startswith(".") for part in p.relative_to(clone_path).parts)
            ]

        owners_files = await asyncio.to_thread(find_owners_files)

        for owners_file_path in owners_files:
            owners_count += 1
            if owners_count > max_owners_files:
                self.logger.error(f"{self.log_prefix} Too many OWNERS files (>{max_owners_files})")
                break

            relative_path = owners_file_path.relative_to(clone_path)
            self.logger.debug(f"{self.log_prefix} Found OWNERS file: {relative_path}")
            tasks.append(self._get_file_content_from_local(owners_file_path))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for idx, result in enumerate(results):
            # Handle unexpected exceptions from _get_file_content_from_local
            if isinstance(result, BaseException):
                # Get the relative path from the original owners_files list for logging
                relative_path_str = (
                    str(owners_files[idx].relative_to(clone_path)) if idx < len(owners_files) else "unknown"
                )
                self.logger.exception(
                    f"{self.log_prefix} Unexpected exception reading OWNERS file {relative_path_str}: {result}"
                )
                continue

            # Skip files that couldn't be read (deleted or unreadable)
            if result is None:
                continue

            # At this point, result must be a tuple (file_content, relative_path_str)
            file_content, relative_path_str = result

            self.logger.debug(
                f"{self.log_prefix} Raw OWNERS file for {relative_path_str}: "
                f"{len(file_content)} bytes, {len(file_content.splitlines())} lines"
            )

            try:
                content = yaml.safe_load(file_content)

                self.logger.debug(
                    f"{self.log_prefix} Parsed OWNERS structure for {relative_path_str} - "
                    f"type: {type(content)}, keys: {list(content.keys()) if isinstance(content, dict) else 'N/A'}, "
                    f"content: {content}"
                )
                if self._validate_owners_content(content, relative_path_str):
                    parent_path = str(Path(relative_path_str).parent)
                    if not parent_path or parent_path == ".":
                        parent_path = "."
                    _owners[parent_path] = content

            except yaml.YAMLError:
                self.logger.exception(f"{self.log_prefix} Invalid OWNERS file {relative_path_str}")
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
        self._ensure_initialized()

        _reviewers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "reviewers":
                    _reviewers.extend(val)

        self.logger.debug(f"{self.log_prefix} All repository reviewers: {_reviewers}")
        return _reviewers

    async def get_all_pull_request_approvers(self) -> list[str]:
        _approvers: list[str] = []
        changed_files = await self.owners_data_for_changed_files

        for list_of_approvers in changed_files.values():
            for _approver in list_of_approvers.get("approvers", []):
                _approvers.append(_approver)

        _approvers = list(set(_approvers))
        _approvers.sort()
        self.logger.debug(f"{self.log_prefix} All pull request approvers: {_approvers}")
        return _approvers

    async def get_all_pull_request_reviewers(self) -> list[str]:
        _reviewers: list[str] = []
        changed_files = await self.owners_data_for_changed_files

        for list_of_reviewers in changed_files.values():
            for _reviewer in list_of_reviewers.get("reviewers", []):
                _reviewers.append(_reviewer)

        _reviewers = list(set(_reviewers))
        _reviewers.sort()
        self.logger.debug(f"{self.log_prefix} Pull request reviewers are: {_reviewers}")
        return _reviewers

    @functools.cached_property
    async def owners_data_for_changed_files(self) -> dict[str, dict[str, Any]]:
        """Get OWNERS data for directories containing changed files.

        Uses @functools.cached_property to cache results and avoid redundant computation
        of folder matching logic across multiple calls during initialization.
        """
        self._ensure_initialized()

        data: dict[str, dict[str, Any]] = {}

        changed_folders = {Path(cf).parent for cf in self.changed_files}
        self.logger.debug(f"{self.log_prefix} Changed folders: {changed_folders}")

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
            self.logger.debug(f"{self.log_prefix} require root_approvers")
            data["."] = self.all_repository_approvers_and_reviewers.get(".", {})

        else:
            for _folder in changed_folders:
                for _changed_path in changed_folder_match:
                    if _folder == _changed_path or _changed_path in _folder.parents:
                        continue
                    else:
                        self.logger.debug(f"{self.log_prefix} Adding root approvers for {_folder}")
                        data["."] = self.all_repository_approvers_and_reviewers.get(".", {})
                        break

        self.logger.debug(f"{self.log_prefix} Final owners data for changed files: {data}")

        return data

    async def assign_reviewers(self, pull_request: PullRequest) -> None:
        self._ensure_initialized()

        self.logger.info(f"{self.log_prefix} Assign reviewers")

        _to_add: list[str] = list(set(self.all_pull_request_reviewers))
        self.logger.debug(f"{self.log_prefix} Reviewers to add: {', '.join(_to_add)}")

        if not _to_add:
            return

        assigned_count = 0
        failed_count = 0
        for reviewer in _to_add:
            if reviewer != pull_request.user.login:
                self.logger.debug(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    await asyncio.to_thread(pull_request.create_review_request, [reviewer])
                    assigned_count += 1

                except GithubException as ex:
                    self.logger.debug(f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}")
                    await asyncio.to_thread(
                        pull_request.create_issue_comment, f"{reviewer} can not be added as reviewer. {ex}"
                    )
                    failed_count += 1

    async def is_user_valid_to_run_commands(self, pull_request: PullRequest, reviewed_user: str) -> bool:
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
        valid_users = await self.valid_users_to_run_commands
        self.logger.debug(f"{self.log_prefix} Valid users to run commands: {valid_users}")

        if reviewed_user not in valid_users:
            for comment in [
                _comment
                for _comment in await asyncio.to_thread(pull_request.get_issue_comments)
                if _comment.user.login in allowed_user_to_approve
            ]:
                if allow_user_comment in comment.body:
                    self.logger.debug(
                        f"{self.log_prefix} {reviewed_user} is approved by {comment.user.login} to run commands"
                    )
                    return True

            self.logger.debug(f"{self.log_prefix} {reviewed_user} is not in {valid_users}")
            await asyncio.to_thread(pull_request.create_issue_comment, comment_msg)
            return False

        return True

    @functools.cached_property
    async def valid_users_to_run_commands(self) -> set[str]:
        self._ensure_initialized()

        repository_collaborators = await self.get_all_repository_collaborators()
        repository_contributors = await self.get_all_repository_contributors()

        return set((
            *repository_collaborators,
            *repository_contributors,
            *self.all_repository_approvers,
            *self.all_pull_request_reviewers,
        ))

    async def get_all_repository_contributors(self) -> list[str]:
        contributors = await self.repository_contributors
        return await asyncio.to_thread(lambda: [val.login for val in contributors])

    async def get_all_repository_collaborators(self) -> list[str]:
        collaborators = await self.repository_collaborators
        return await asyncio.to_thread(lambda: [val.login for val in collaborators])

    async def get_all_repository_maintainers(self) -> list[str]:
        maintainers: list[str] = []

        # Fix #1: Convert PaginatedList to list in thread pool to avoid blocking during iteration
        collaborators = await self.repository_collaborators
        collaborators_list = await asyncio.to_thread(lambda: list(collaborators))

        for user in collaborators_list:
            # Fix #2: Wrap permissions access in thread pool (property makes blocking API call)
            def get_user_permissions(u: NamedUser = user) -> Permissions:
                return u.permissions

            permissions = await asyncio.to_thread(get_user_permissions)
            self.logger.debug(f"{self.log_prefix} User {user.login} permissions: {permissions}")

            if permissions.admin or permissions.maintain:
                maintainers.append(user.login)

        self.logger.debug(f"{self.log_prefix} Maintainers: {maintainers}")
        return maintainers

    @functools.cached_property
    async def repository_collaborators(self) -> PaginatedList[NamedUser]:
        return await asyncio.to_thread(self.repository.get_collaborators)

    @functools.cached_property
    async def repository_contributors(self) -> PaginatedList[NamedUser]:
        return await asyncio.to_thread(self.repository.get_contributors)
