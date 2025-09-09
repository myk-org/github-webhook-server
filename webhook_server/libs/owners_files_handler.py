import asyncio
from pathlib import Path
from typing import TYPE_CHECKING, Any, Coroutine

import yaml
from asyncstdlib import functools
from github.ContentFile import ContentFile
from github.GithubException import GithubException
from github.NamedUser import NamedUser
from github.PaginatedList import PaginatedList
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.utils.constants import COMMAND_ADD_ALLOWED_USER_STR

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class OwnersFileHandler:
    def __init__(self, github_webhook: "GithubWebhook") -> None:
        self.github_webhook = github_webhook
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository

    async def initialize(self, pull_request: PullRequest) -> "OwnersFileHandler":
        self.changed_files = await self.list_changed_files(pull_request=pull_request)
        self.all_repository_approvers_and_reviewers = await self.get_all_repository_approvers_and_reviewers(
            pull_request=pull_request
        )
        self.all_repository_approvers = await self.get_all_repository_approvers()
        self.all_repository_reviewers = await self.get_all_repository_reviewers()
        self.all_pull_request_approvers = await self.get_all_pull_request_approvers()
        self.all_pull_request_reviewers = await self.get_all_pull_request_reviewers()

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
        changed_files = [_file.filename for _file in await asyncio.to_thread(pull_request.get_files)]
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

    async def _get_file_content(self, content_path: str, pull_request: PullRequest) -> tuple[ContentFile, str]:
        self.logger.debug(f"{self.log_prefix} Get OWNERS file from {content_path}")

        _path = await asyncio.to_thread(self.repository.get_contents, content_path, pull_request.base.ref)

        if isinstance(_path, list):
            _path = _path[0]

        return _path, content_path

    @functools.lru_cache
    async def get_all_repository_approvers_and_reviewers(self, pull_request: PullRequest) -> dict[str, dict[str, Any]]:
        # Dictionary mapping OWNERS file paths to their approvers and reviewers
        _owners: dict[str, dict[str, Any]] = {}
        tasks: list[Coroutine[Any, Any, Any]] = []

        max_owners_files = 1000  # Configurable limit
        owners_count = 0

        self.logger.debug(f"{self.log_prefix} Get git tree")
        tree = await asyncio.to_thread(self.repository.get_git_tree, pull_request.base.ref, recursive=True)

        for element in tree.tree:
            if element.type == "blob" and element.path.endswith("OWNERS"):
                owners_count += 1
                if owners_count > max_owners_files:
                    self.logger.error(f"{self.log_prefix} Too many OWNERS files (>{max_owners_files})")
                    break

                content_path = element.path
                self.logger.debug(f"{self.log_prefix} Found OWNERS file: {content_path}")
                tasks.append(self._get_file_content(content_path, pull_request))

        results = await asyncio.gather(*tasks)

        for result in results:
            _path, _content_path = result

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
        changed_files = await self.owners_data_for_changed_files()

        for list_of_approvers in changed_files.values():
            for _approver in list_of_approvers.get("approvers", []):
                _approvers.append(_approver)

        _approvers = list(set(_approvers))
        _approvers.sort()
        self.logger.debug(f"{self.log_prefix} All pull request approvers: {_approvers}")
        return _approvers

    async def get_all_pull_request_reviewers(self) -> list[str]:
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
                        require_root_approvers = owners_data.get("root-approvers", True)

        if require_root_approvers or require_root_approvers is None:
            self.logger.debug(f"{self.log_prefix} require root_approvers")
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

    async def assign_reviewers(self, pull_request: PullRequest) -> None:
        self._ensure_initialized()

        self.logger.step(f"{self.log_prefix} Starting reviewer assignment based on OWNERS files")  # type: ignore
        self.logger.info(f"{self.log_prefix} Assign reviewers")

        _to_add: list[str] = list(set(self.all_pull_request_reviewers))
        self.logger.debug(f"{self.log_prefix} Reviewers to add: {', '.join(_to_add)}")

        if _to_add:
            self.logger.step(f"{self.log_prefix} Assigning {len(_to_add)} reviewers to PR")  # type: ignore
        else:
            self.logger.step(f"{self.log_prefix} No reviewers to assign")  # type: ignore
            return

        for reviewer in _to_add:
            if reviewer != pull_request.user.login:
                self.logger.debug(f"{self.log_prefix} Adding reviewer {reviewer}")
                try:
                    await asyncio.to_thread(pull_request.create_review_request, [reviewer])
                    self.logger.step(f"{self.log_prefix} Successfully assigned reviewer {reviewer}")  # type: ignore

                except GithubException as ex:
                    self.logger.step(f"{self.log_prefix} Failed to assign reviewer {reviewer}")  # type: ignore
                    self.logger.debug(f"{self.log_prefix} Failed to add reviewer {reviewer}. {ex}")
                    await asyncio.to_thread(
                        pull_request.create_issue_comment, f"{reviewer} can not be added as reviewer. {ex}"
                    )

        self.logger.step(f"{self.log_prefix} Reviewer assignment completed")  # type: ignore

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
        self.logger.debug(f"Valid users to run commands: {valid_users}")

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
        return [val.login for val in contributors]

    async def get_all_repository_collaborators(self) -> list[str]:
        collaborators = await self.repository_collaborators
        return [val.login for val in collaborators]

    async def get_all_repository_maintainers(self) -> list[str]:
        maintainers: list[str] = []

        for user in await self.repository_collaborators:
            permissions = user.permissions
            self.logger.debug(f"User {user.login} permissions: {permissions}")

            if permissions.admin or permissions.maintain:
                maintainers.append(user.login)

        self.logger.debug(f"Maintainers: {maintainers}")
        return maintainers

    @functools.cached_property
    async def repository_collaborators(self) -> PaginatedList[NamedUser]:
        return await asyncio.to_thread(self.repository.get_collaborators)

    @functools.cached_property
    async def repository_contributors(self) -> PaginatedList[NamedUser]:
        return await asyncio.to_thread(self.repository.get_contributors)
