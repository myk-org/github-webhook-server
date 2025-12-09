import asyncio
import functools as sync_functools
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
from webhook_server.utils.helpers import format_task_fields, run_command

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
        self.logger.debug("%s ROOT Reviewers: %s", self.log_prefix, _reviewers)
        return _reviewers

    @property
    def root_approvers(self) -> list[str]:
        self._ensure_initialized()

        _approvers = self.all_repository_approvers_and_reviewers.get(".", {}).get("approvers", [])
        self.logger.debug("%s ROOT Approvers: %s", self.log_prefix, _approvers)
        return _approvers

    @sync_functools.cached_property
    def teams_and_members(self) -> dict[str, list[str]]:
        """Get teams and their members from OWNERS files.

        Each OWNERS file directory represents a team. Returns mapping of
        team names (directory paths) to their members (approvers + reviewers combined).

        Returns:
            Dict mapping team names to their members:
            {
                "sig-all": ["user1", "user2"],
                "sig-storage": ["user3", "user4"],
            }
        """
        self._ensure_initialized()

        _teams: dict[str, list[str]] = {}

        for team_path, owners_data in self.all_repository_approvers_and_reviewers.items():
            # Transform team path to sig-* format:
            # - "." (root) becomes "sig-all"
            # - "tests/storage" becomes "sig-storage" (last path component)
            if team_path == ".":
                team_name = "sig-all"
            else:
                # Get last component of path (e.g., "tests/storage" -> "storage")
                last_component = Path(team_path).name
                team_name = f"sig-{last_component}"

            # Use set for deduplication, convert to sorted list for consistent output
            _teams[team_name] = sorted(set(owners_data.get("approvers", []) + owners_data.get("reviewers", [])))

        self.logger.debug("%s Teams and members: %s", self.log_prefix, _teams)
        return _teams

    def get_user_sig_suffix(self, username: str) -> str:
        """Get SIG label suffix for a user based on their team membership.

        Args:
            username: GitHub username to look up

        Returns:
            String like "-sig-storage-sig-network" or empty if user not in any SIG.
        """
        self._ensure_initialized()

        sig_names: list[str] = []

        for team_name, members in self.teams_and_members.items():
            if username in members:
                sig_names.append(team_name)

        sig_names.sort()

        if sig_names:
            return "-" + "-".join(sig_names)
        return ""

    @property
    def allowed_users(self) -> list[str]:
        self._ensure_initialized()

        _allowed_users = self.all_repository_approvers_and_reviewers.get(".", {}).get("allowed-users", [])
        self.logger.debug("%s ROOT allowed users: %s", self.log_prefix, _allowed_users)
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
                self.logger.error("%s git diff command failed", self.log_prefix)
                return []

            # Parse output: split by newlines and filter empty lines
            changed_files = [line.strip() for line in out.splitlines() if line.strip()]

            self.logger.debug("%s Changed files: %s", self.log_prefix, changed_files)
            return changed_files

        except Exception:
            # Log error and return empty list if git diff fails
            self.logger.exception("%s Failed to get changed files via git diff", self.log_prefix)
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
            self.logger.error("%s Invalid OWNERS file %s: %s", self.log_prefix, path, e)
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
        self.logger.debug("%s Reading OWNERS file from local clone: %s", self.log_prefix, relative_path)

        try:
            # Read file content from local filesystem (wrap in thread pool for I/O)
            file_content = await asyncio.to_thread(content_path.read_text, encoding="utf-8")
            return file_content, str(relative_path)

        except OSError as ex:
            # File may have been deleted or become unreadable between rglob and read_text
            self.logger.warning(
                "%s Failed to read OWNERS file %s: %s. Skipping this file.", self.log_prefix, relative_path, ex
            )
            return None

        except UnicodeDecodeError as ex:
            # File has invalid encoding - log and skip to allow processing to continue
            self.logger.warning(
                "%s OWNERS file %s has invalid encoding: %s. Skipping this file.", self.log_prefix, relative_path, ex
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
        self.logger.debug("%s Finding OWNERS files in local clone", self.log_prefix)

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
            self.logger.debug("%s Reading OWNERS files from branch: %s", self.log_prefix, current_branch.strip())
        if log_success and log_output.strip():
            self.logger.debug("%s Latest OWNERS commit: %s", self.log_prefix, log_output.strip())

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
                self.logger.error("%s Too many OWNERS files (>%s)", self.log_prefix, max_owners_files)
                break

            relative_path = owners_file_path.relative_to(clone_path)
            self.logger.debug("%s Found OWNERS file: %s", self.log_prefix, relative_path)
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
                    "%s Unexpected exception reading OWNERS file %s: %s", self.log_prefix, relative_path_str, result
                )
                continue

            # Skip files that couldn't be read (deleted or unreadable)
            if result is None:
                continue

            # At this point, result must be a tuple (file_content, relative_path_str)
            file_content, relative_path_str = result

            self.logger.debug(
                "%s Raw OWNERS file for %s: %s bytes, %s lines",
                self.log_prefix,
                relative_path_str,
                len(file_content),
                len(file_content.splitlines()),
            )

            try:
                content = yaml.safe_load(file_content)

                self.logger.debug(
                    "%s Parsed OWNERS structure for %s - type: %s, keys: %s, content: %s",
                    self.log_prefix,
                    relative_path_str,
                    type(content),
                    list(content.keys()) if isinstance(content, dict) else "N/A",
                    content,
                )
                if self._validate_owners_content(content, relative_path_str):
                    parent_path = str(Path(relative_path_str).parent)
                    if not parent_path or parent_path == ".":
                        parent_path = "."
                    _owners[parent_path] = content

            except yaml.YAMLError:
                self.logger.exception("%s Invalid OWNERS file %s", self.log_prefix, relative_path_str)
                continue

        return _owners

    async def get_all_repository_approvers(self) -> list[str]:
        self._ensure_initialized()

        _approvers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "approvers":
                    _approvers.extend(val)

        self.logger.debug("%s All repository approvers: %s", self.log_prefix, _approvers)
        return _approvers

    async def get_all_repository_reviewers(self) -> list[str]:
        self._ensure_initialized()

        _reviewers: list[str] = []

        for value in self.all_repository_approvers_and_reviewers.values():
            for key, val in value.items():
                if key == "reviewers":
                    _reviewers.extend(val)

        self.logger.debug("%s All repository reviewers: %s", self.log_prefix, _reviewers)
        return _reviewers

    async def get_all_pull_request_approvers(self) -> list[str]:
        _approvers: list[str] = []
        changed_files = await self.owners_data_for_changed_files

        for list_of_approvers in changed_files.values():
            for _approver in list_of_approvers.get("approvers", []):
                _approvers.append(_approver)

        _approvers = list(set(_approvers))
        _approvers.sort()
        self.logger.debug("%s All pull request approvers: %s", self.log_prefix, _approvers)
        return _approvers

    async def get_all_pull_request_reviewers(self) -> list[str]:
        _reviewers: list[str] = []
        changed_files = await self.owners_data_for_changed_files

        for list_of_reviewers in changed_files.values():
            for _reviewer in list_of_reviewers.get("reviewers", []):
                _reviewers.append(_reviewer)

        _reviewers = list(set(_reviewers))
        _reviewers.sort()
        self.logger.debug("%s Pull request reviewers are: %s", self.log_prefix, _reviewers)
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
        self.logger.debug("%s Changed folders: %s", self.log_prefix, changed_folders)

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
                        "%s Matched changed folder: %s with owners dir: %s",
                        self.log_prefix,
                        changed_folder,
                        _owners_dir,
                    )
                    if require_root_approvers is None:
                        require_root_approvers = owners_data.get(ROOT_APPROVERS_KEY, True)

        if require_root_approvers or require_root_approvers is None:
            self.logger.debug("%s require root_approvers", self.log_prefix)
            data["."] = self.all_repository_approvers_and_reviewers.get(".", {})

        else:
            for _folder in changed_folders:
                for _changed_path in changed_folder_match:
                    if _folder == _changed_path or _changed_path in _folder.parents:
                        continue
                    else:
                        self.logger.debug("%s Adding root approvers for %s", self.log_prefix, _folder)
                        data["."] = self.all_repository_approvers_and_reviewers.get(".", {})
                        break

        self.logger.debug("%s Final owners data for changed files: %s", self.log_prefix, data)

        return data

    async def assign_reviewers(self, pull_request: PullRequest) -> None:
        self._ensure_initialized()

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'started')} "
            f"Starting reviewer assignment based on OWNERS files",
        )
        self.logger.info("%s Assign reviewers", self.log_prefix)

        _to_add: list[str] = list(set(self.all_pull_request_reviewers))
        self.logger.debug("%s Reviewers to add: %s", self.log_prefix, ", ".join(_to_add))

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
            # Log completion - task_status reflects the result of our action (no reviewers to assign is acceptable)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'completed')} "
                f"No reviewers to assign (completed)",
            )
            return

        assigned_count = 0
        failed_count = 0
        for reviewer in _to_add:
            if reviewer != pull_request.user.login:
                self.logger.debug("%s Adding reviewer %s", self.log_prefix, reviewer)
                try:
                    await asyncio.to_thread(pull_request.create_review_request, [reviewer])
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'processing')} "
                        f"Successfully assigned reviewer {reviewer}",
                    )
                    assigned_count += 1

                except GithubException as ex:
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'failed')} "
                        f"Failed to assign reviewer {reviewer}",
                    )
                    self.logger.debug("%s Failed to add reviewer %s. %s", self.log_prefix, reviewer, ex)
                    await asyncio.to_thread(
                        pull_request.create_issue_comment, f"{reviewer} can not be added as reviewer. {ex}"
                    )
                    failed_count += 1

        # Log completion - task_status reflects the result of our action
        if failed_count > 0:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'failed')} "
                f"Assigned {assigned_count} reviewers to PR ({failed_count} failed)",
            )
        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('owners', 'pr_management', 'completed')} "
                f"Assigned {assigned_count} reviewers to PR",
            )

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
        self.logger.debug("%s Valid users to run commands: %s", self.log_prefix, valid_users)

        if reviewed_user not in valid_users:
            for comment in [
                _comment
                for _comment in await asyncio.to_thread(pull_request.get_issue_comments)
                if _comment.user.login in allowed_user_to_approve
            ]:
                if allow_user_comment in comment.body:
                    self.logger.debug(
                        "%s %s is approved by %s to run commands", self.log_prefix, reviewed_user, comment.user.login
                    )
                    return True

            self.logger.debug("%s %s is not in %s", self.log_prefix, reviewed_user, valid_users)
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
            self.logger.debug("%s User %s permissions: %s", self.log_prefix, user.login, permissions)

            if permissions.admin or permissions.maintain:
                maintainers.append(user.login)

        self.logger.debug("%s Maintainers: %s", self.log_prefix, maintainers)
        return maintainers

    @functools.cached_property
    async def repository_collaborators(self) -> PaginatedList[NamedUser]:
        return await asyncio.to_thread(self.repository.get_collaborators)

    @functools.cached_property
    async def repository_contributors(self) -> PaginatedList[NamedUser]:
        return await asyncio.to_thread(self.repository.get_contributors)
