from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch

import pytest
import yaml
from github.GithubException import GithubException, UnknownObjectException

from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler


def _named_user(login: str, *, admin: bool = False, maintain: bool = False) -> Mock:
    """A `NamedUser` stand-in carrying the permissions the collaborator listing reports."""
    user = Mock()
    user.login = login
    user.permissions.admin = admin
    user.permissions.maintain = maintain
    return user


def _vouch_comment(login: str, body: str = "/add-allowed-user @newuser") -> Mock:
    comment = Mock()
    comment.user.login = login
    comment.body = body
    return comment


async def _run_vouch(
    handler: OwnersFileHandler,
    pull_request: Mock,
    *,
    comments: list[Mock],
    collaborators: list[Mock] | None = None,
    root_approvers: list[str] | None = None,
    owners: dict[str, dict[str, list[str]]] | None = None,
    reviewed_user: str = "newuser",
) -> tuple[bool, Mock]:
    """Run the vouch path against a fake collaborator listing; returns (result, comment mock)."""
    handler.changed_files = ["file1.py"]
    handler.all_repository_approvers_and_reviewers = (
        owners if owners is not None else {".": {"approvers": root_approvers or []}}
    )
    handler.all_repository_approvers = root_approvers or []
    handler.all_pull_request_reviewers = []
    with (
        patch.object(handler, "get_all_repository_maintainers", return_value=[]),
        patch.object(handler, "get_all_repository_collaborators", return_value=[]),
        patch.object(handler, "get_all_repository_contributors", return_value=[]),
        patch.object(handler.repository, "get_collaborators", return_value=collaborators or []),
        patch.object(pull_request, "get_issue_comments", return_value=comments),
        patch.object(pull_request, "create_issue_comment") as create_comment,
    ):
        result = await handler.is_user_valid_to_run_commands(pull_request, reviewed_user)
    return result, create_comment


class TestOwnersFileHandler:
    """Test suite for OwnersFileHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.ctx = None
        return mock_webhook

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.base.ref = "main"
        mock_pr.user.login = "test-user"
        return mock_pr

    @pytest.fixture
    def owners_file_handler(self, mock_github_webhook: Mock) -> OwnersFileHandler:
        """Create an OwnersFileHandler instance."""
        return OwnersFileHandler(mock_github_webhook)

    @pytest.mark.asyncio
    async def test_initialize(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test the initialize method."""
        with patch.object(owners_file_handler, "list_changed_files", new=AsyncMock()) as mock_list_files:
            with patch.object(
                owners_file_handler, "get_all_repository_approvers_and_reviewers", new=AsyncMock()
            ) as mock_get_all:
                with patch.object(
                    owners_file_handler, "get_all_repository_approvers", new=AsyncMock()
                ) as mock_get_approvers:
                    with patch.object(
                        owners_file_handler, "get_all_repository_reviewers", new=AsyncMock()
                    ) as mock_get_reviewers:
                        with patch.object(
                            owners_file_handler, "get_all_pull_request_approvers", new=AsyncMock()
                        ) as mock_get_pr_approvers:
                            with patch.object(
                                owners_file_handler, "get_all_pull_request_reviewers", new=AsyncMock()
                            ) as mock_get_pr_reviewers:
                                mock_list_files.return_value = ["file1.py", "file2.py"]
                                mock_get_all.return_value = {".": {"approvers": ["user1"], "reviewers": ["user2"]}}
                                mock_get_approvers.return_value = ["user1"]
                                mock_get_reviewers.return_value = ["user2"]
                                mock_get_pr_approvers.return_value = ["user1"]
                                mock_get_pr_reviewers.return_value = ["user2"]

                                result = await owners_file_handler.initialize()

                                assert result == owners_file_handler
                                assert owners_file_handler.changed_files == ["file1.py", "file2.py"]
                                assert owners_file_handler.all_repository_approvers_and_reviewers == {
                                    ".": {"approvers": ["user1"], "reviewers": ["user2"]}
                                }
                                assert owners_file_handler.all_repository_approvers == ["user1"]
                                assert owners_file_handler.all_repository_reviewers == ["user2"]
                                assert owners_file_handler.all_pull_request_approvers == ["user1"]
                                assert owners_file_handler.all_pull_request_reviewers == ["user2"]

    @pytest.mark.asyncio
    async def test_ensure_initialized_not_initialized(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _ensure_initialized raises error when not initialized."""
        with pytest.raises(
            RuntimeError, match="OwnersFileHandler.initialize\\(\\) must be called before using this method"
        ):
            owners_file_handler._ensure_initialized()

    @pytest.mark.asyncio
    async def test_ensure_initialized_initialized(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _ensure_initialized doesn't raise error when initialized."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler._ensure_initialized()  # Should not raise

    @pytest.mark.asyncio
    async def test_list_changed_files(self, owners_file_handler: OwnersFileHandler, tmp_path: Path) -> None:
        """Test list_changed_files reads SHAs from GithubWebhook instance."""
        # SHAs are stored on the GithubWebhook instance during process()
        owners_file_handler.github_webhook.pr_base_sha = "base123abc"
        owners_file_handler.github_webhook.pr_head_sha = "head456def"

        # Set up handler properties
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)
        owners_file_handler.github_webhook.mask_sensitive = True

        # Mock run_command to return git diff output
        with patch(
            "webhook_server.libs.handlers.owners_files_handler.run_command", new=AsyncMock()
        ) as mock_run_command:
            mock_run_command.return_value = (True, "file1.py\nfile2.py\n", "")

            result = await owners_file_handler.list_changed_files()

            # Verify result
            assert result == ["file1.py", "file2.py"]

            # Verify run_command was called with correct git command
            mock_run_command.assert_called_once_with(
                command=f"git -C {tmp_path} diff --name-only base123abc...head456def",
                log_prefix="[TEST]",
                verify_stderr=False,
                mask_sensitive=True,
            )

    def test_validate_owners_content_valid(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content with valid content."""
        valid_content = {"approvers": ["user1", "user2"], "reviewers": ["user3", "user4"]}
        assert owners_file_handler._validate_owners_content(valid_content, "test/path") is True

    def test_validate_owners_content_not_dict(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content with non-dict content."""
        invalid_content = ["user1", "user2"]
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_validate_owners_content_approvers_not_list(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content with approvers not being a list."""
        invalid_content = {"approvers": "user1", "reviewers": ["user3", "user4"]}
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_validate_owners_content_reviewers_not_list(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content with reviewers not being a list."""
        invalid_content = {"approvers": ["user1", "user2"], "reviewers": "user3"}
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_validate_owners_content_approvers_not_strings(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content with approvers containing non-strings."""
        invalid_content = {"approvers": ["user1", 123], "reviewers": ["user3", "user4"]}
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_validate_owners_content_reviewers_not_strings(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content with reviewers containing non-strings."""
        invalid_content = {"approvers": ["user1", "user2"], "reviewers": ["user3", {"name": "user4"}]}
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_validate_owners_content_allowed_users_valid(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content accepts allowed-users list of strings."""
        valid_content = {"approvers": ["user1"], "allowed-users": ["bob", "@alice"]}
        assert owners_file_handler._validate_owners_content(valid_content, "test/path") is True

    def test_validate_owners_content_allowed_users_not_list(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content rejects non-list allowed-users."""
        invalid_content = {"allowed-users": "bob"}
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_validate_owners_content_allowed_users_not_strings(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _validate_owners_content rejects non-string allowed-users entries."""
        invalid_content = {"allowed-users": ["bob", 123]}
        assert owners_file_handler._validate_owners_content(invalid_content, "test/path") is False

    def test_allowed_users_strips_at_prefix(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test allowed_users normalizes @username entries to GitHub logins."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"allowed-users": ["@bob", "alice"]},
        }
        assert owners_file_handler.allowed_users == ["bob", "alice"]

    @pytest.mark.asyncio
    async def test_get_file_content_from_local(self, owners_file_handler: OwnersFileHandler, tmp_path: Path) -> None:
        """Test _get_file_content_from_local method."""
        # Create a temporary OWNERS file
        owners_file = tmp_path / "test" / "OWNERS"
        owners_file.parent.mkdir(parents=True, exist_ok=True)
        owners_file.write_text("test content")

        # Set clone_repo_dir to tmp_path
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)

        result = await owners_file_handler._get_file_content_from_local(owners_file)

        assert result == ("test content", "test/OWNERS")

    @pytest.mark.asyncio
    async def test_get_file_content_from_local_file_not_found(
        self, owners_file_handler: OwnersFileHandler, tmp_path: Path
    ) -> None:
        """Test _get_file_content_from_local method with missing file."""
        # Create path to non-existent file
        missing_file = tmp_path / "test" / "OWNERS"

        # Set clone_repo_dir to tmp_path
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)
        owners_file_handler.logger.warning = Mock()

        result = await owners_file_handler._get_file_content_from_local(missing_file)

        assert result is None
        owners_file_handler.logger.warning.assert_called_once()
        # Verify the warning message contains expected information
        warning_call = owners_file_handler.logger.warning.call_args[0][0]
        assert "Failed to read OWNERS file" in warning_call
        assert "test/OWNERS" in warning_call
        assert "Skipping this file" in warning_call

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers(
        self,
        owners_file_handler: OwnersFileHandler,
        owners_files_test_data: dict[str, str],
        tmp_path: Path,
    ) -> None:
        """Test reading OWNERS files from local cloned repository."""
        # Create a temporary directory structure with OWNERS files
        for file_path, content in owners_files_test_data.items():
            full_path = tmp_path / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content)

        # Set clone_repo_dir to tmp_path (simulating already cloned repo)
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)

        # No worktree needed - read directly from clone
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers()

        expected = {
            ".": {"approvers": ["root_approver1", "root_approver2"], "reviewers": ["root_reviewer1", "root_reviewer2"]},
            "folder1": {
                "approvers": ["folder1_approver1", "folder1_approver2"],
                "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
            },
            "folder2": {},
            "folder/folder4": {
                "approvers": ["folder4_approver1", "folder4_approver2"],
                "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
            },
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1", "folder5_approver2"],
                "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
            },
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_too_many_files(
        self, owners_file_handler: OwnersFileHandler, tmp_path: Path
    ) -> None:
        """Test that too many OWNERS files are handled correctly."""
        # Create 1001 OWNERS files
        for i in range(1001):
            owners_file = tmp_path / f"file{i}" / "OWNERS"
            owners_file.parent.mkdir(parents=True, exist_ok=True)
            owners_file.write_text(yaml.dump({"approvers": [], "reviewers": []}))

        # Set clone_repo_dir to tmp_path
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)
        owners_file_handler.logger.error = Mock()

        # No worktree needed - read directly from clone
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers()

        assert len(result) == 1000
        owners_file_handler.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_invalid_yaml(
        self, owners_file_handler: OwnersFileHandler, tmp_path: Path
    ) -> None:
        """Test handling of invalid YAML in OWNERS files."""
        # Create OWNERS file with invalid YAML
        owners_file = tmp_path / "OWNERS"
        owners_file.write_text("invalid: yaml: content: [")

        # Set clone_repo_dir to tmp_path
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)
        owners_file_handler.logger.exception = Mock()

        # No worktree needed - read directly from clone
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers()

        assert result == {}
        owners_file_handler.logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_invalid_content(
        self, owners_file_handler: OwnersFileHandler, tmp_path: Path
    ) -> None:
        """Test handling of invalid content structure in OWNERS files."""
        # Create OWNERS file with invalid structure
        owners_file = tmp_path / "OWNERS"
        owners_file.write_text(yaml.dump({"approvers": "not_a_list"}))

        # Set clone_repo_dir to tmp_path
        owners_file_handler.github_webhook.clone_repo_dir = str(tmp_path)
        owners_file_handler.logger.error = Mock()

        # No worktree needed - read directly from clone
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers()

        assert result == {}
        owners_file_handler.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_approvers method."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["user1", "user2"], "reviewers": ["user3"]},
            "folder1": {"approvers": ["user4"], "reviewers": ["user5"]},
            "folder2": {"reviewers": ["user6"]},  # No approvers
        }

        result = await owners_file_handler.get_all_repository_approvers()

        assert result == ["user1", "user2", "user4"]

    @pytest.mark.asyncio
    async def test_get_all_repository_reviewers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_reviewers method."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["user1"], "reviewers": ["user2", "user3"]},
            "folder1": {"approvers": ["user4"], "reviewers": ["user5"]},
            "folder2": {"approvers": ["user6"]},  # No reviewers
        }

        result = await owners_file_handler.get_all_repository_reviewers()

        assert result == ["user2", "user3", "user5"]

    @pytest.mark.asyncio
    async def test_get_all_pull_request_approvers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_pull_request_approvers method."""
        owners_file_handler.changed_files = ["file1.py", "folder1/file2.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["user1", "user2"], "reviewers": ["user3"]},
            "folder1": {"approvers": ["user4"], "reviewers": ["user5"]},
        }

        result = await owners_file_handler.get_all_pull_request_approvers()

        assert result == ["user1", "user2", "user4"]

    @pytest.mark.asyncio
    async def test_get_all_pull_request_reviewers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_pull_request_reviewers method."""
        owners_file_handler.changed_files = ["file1.py", "folder1/file2.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["user1"], "reviewers": ["user2", "user3"]},
            "folder1": {"approvers": ["user4"], "reviewers": ["user5"]},
        }

        result = await owners_file_handler.get_all_pull_request_reviewers()

        assert result == ["user2", "user3", "user5"]

    @pytest.mark.asyncio
    async def test_owners_data_for_changed_files(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test owners_data_for_changed_files method."""
        owners_file_handler.changed_files = [
            "folder1/file1.py",
            "folder2/file2.py",
            "folder/folder4/file3.py",
            "folder5/file4.py",
            "root_file.py",
        ]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
            "folder2": {},
            "folder/folder4": {"approvers": ["folder4_approver1"], "reviewers": ["folder4_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
        }

        result = await owners_file_handler.owners_data_for_changed_files

        expected = {
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
            "folder2": {},
            "folder/folder4": {"approvers": ["folder4_approver1"], "reviewers": ["folder4_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_owners_data_for_changed_files_no_root_approvers(
        self, owners_file_handler: OwnersFileHandler
    ) -> None:
        """Test owners_data_for_changed_files when root-approvers is False."""
        owners_file_handler.changed_files = ["folder5/file1.py", "folder_with_no_owners/file2.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
        }

        result = await owners_file_handler.owners_data_for_changed_files

        expected = {
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_root_approvers_required_for_pr_false(self, owners_file_handler: OwnersFileHandler) -> None:
        """root-approvers: false for every matched dir, all folders covered -> not required."""
        owners_file_handler.changed_files = ["folder5/file1.py", "folder5/file2.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
        }

        assert await owners_file_handler.root_approvers_required_for_pr is False

    @pytest.mark.asyncio
    async def test_root_approvers_required_for_pr_default(self, owners_file_handler: OwnersFileHandler) -> None:
        """No explicit root-approvers: false -> root approvers required (OWNERS default)."""
        owners_file_handler.changed_files = ["folder1/file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
        }

        assert await owners_file_handler.root_approvers_required_for_pr is True

    @pytest.mark.asyncio
    async def test_local_approvers_by_dir(self, owners_file_handler: OwnersFileHandler) -> None:
        """Approvers per matched sub-OWNERS dir; the root entry is never included."""
        owners_file_handler.changed_files = ["folder1/file1.py", "folder5/file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
        }

        assert await owners_file_handler.local_approvers_by_dir == {
            "folder1": ["folder1_approver1"],
            "folder5": ["folder5_approver1"],
        }

    @pytest.mark.asyncio
    async def test_local_approvers_by_dir_ancestor_dir_matched(self, owners_file_handler: OwnersFileHandler) -> None:
        """An OWNERS dir matches when it covers any changed folder, incl. via ancestry.

        A PR touching `xx/yy` and `xx` matches both OWNERS dirs, so the by-dir map keeps
        them separate — the union is not enough for the /merge gate.
        """
        owners_file_handler.changed_files = ["xx/yy/file1.py", "xx/file2.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "xx": {"approvers": ["xx_approver1"], "reviewers": ["xx_reviewer1"]},
            "xx/yy": {"approvers": ["yy_approver1"], "reviewers": ["yy_reviewer1"]},
        }

        assert await owners_file_handler.local_approvers_by_dir == {
            "xx": ["xx_approver1"],
            "xx/yy": ["yy_approver1"],
        }

    @pytest.mark.asyncio
    async def test_root_approvers_required_for_pr_root_level_files(
        self, owners_file_handler: OwnersFileHandler
    ) -> None:
        """Root-level changed files can never be covered by a sub-OWNERS dir."""
        owners_file_handler.changed_files = ["README.md"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {
                "root-approvers": False,
                "approvers": ["root_approver1"],
                "reviewers": ["root_reviewer1"],
            },
            "folder1": {
                "root-approvers": False,
                "approvers": ["folder1_approver1"],
                "reviewers": ["folder1_reviewer1"],
            },
        }

        assert await owners_file_handler.root_approvers_required_for_pr is True

    @pytest.mark.asyncio
    async def test_root_approvers_required_for_pr_mixed_dirs(self, owners_file_handler: OwnersFileHandler) -> None:
        """One matched dir without the opt-out -> root approvers still required."""
        owners_file_handler.changed_files = ["folder1/file1.py", "folder5/file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
        }

        assert await owners_file_handler.root_approvers_required_for_pr is True

    @pytest.mark.asyncio
    async def test_root_approvers_required_for_pr_uncovered_folder(
        self, owners_file_handler: OwnersFileHandler
    ) -> None:
        """A changed folder with no matching sub-OWNERS dir -> root approvers still required."""
        owners_file_handler.changed_files = ["folder5/file1.py", "folder_with_no_owners/file2.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder5": {
                "root-approvers": False,
                "approvers": ["folder5_approver1"],
                "reviewers": ["folder5_reviewer1"],
            },
        }

        assert await owners_file_handler.root_approvers_required_for_pr is True

    @pytest.mark.asyncio
    async def test_owners_data_for_changed_files_caching(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test that owners_data_for_changed_files caches results using @functools.cached_property."""
        # Set up test data
        owners_file_handler.changed_files = [
            "folder1/file1.py",
            "folder2/file2.py",
        ]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
            "folder2": {},
        }

        # First call - computes and caches
        result1 = await owners_file_handler.owners_data_for_changed_files

        # Verify result is correct
        expected = {
            "folder1": {"approvers": ["folder1_approver1"], "reviewers": ["folder1_reviewer1"]},
            "folder2": {},
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
        }
        assert result1 == expected

        # Second call - should return cached result (same object reference)
        result2 = await owners_file_handler.owners_data_for_changed_files

        # Verify cache returns same result and same object reference
        assert result2 == result1
        assert result2 is result1  # Same object reference (cached by @functools.cached_property)

    @pytest.mark.asyncio
    async def test_owners_data_for_changed_files_cache_independence(self, mock_github_webhook: Mock) -> None:
        """Test that different OwnersFileHandler instances have independent caches using @functools.cached_property."""
        # Create two separate instances
        handler1 = OwnersFileHandler(mock_github_webhook)
        handler2 = OwnersFileHandler(mock_github_webhook)

        # Set up different data for each handler
        handler1.changed_files = ["folder1/file1.py"]
        handler1.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"], "reviewers": ["reviewer1"]},
            "folder1": {"approvers": ["folder1_approver"], "reviewers": ["folder1_reviewer"]},
        }

        handler2.changed_files = ["folder2/file2.py"]
        handler2.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver2"], "reviewers": ["reviewer2"]},
            "folder2": {"approvers": ["folder2_approver"], "reviewers": ["folder2_reviewer"]},
        }

        # Get results from both handlers
        result1 = await handler1.owners_data_for_changed_files
        result2 = await handler2.owners_data_for_changed_files

        # Verify they have independent results
        assert result1 != result2
        assert "folder1" in result1
        assert "folder2" in result2
        assert "folder2" not in result1
        assert "folder1" not in result2

        # Verify results are not the same object (independent caches)
        assert result1 is not result2

    @pytest.mark.asyncio
    async def test_assign_reviewers(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2", "test-user"]
        mock_pull_request.user.login = "test-user"

        with patch.object(mock_pull_request, "create_review_request") as mock_create_request:
            await owners_file_handler.assign_reviewers(mock_pull_request)
            # Should only add reviewers that are not the PR author
            expected_calls = [call(["reviewer1"]), call(["reviewer2"])]
            actual_calls = mock_create_request.call_args_list
            assert sorted(actual_calls, key=str) == sorted(expected_calls, key=str)

    @pytest.mark.asyncio
    async def test_assign_reviewers_no_reviewers(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test assigning reviewers when no reviewers to assign."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_pull_request_reviewers = []

        await owners_file_handler.assign_reviewers(mock_pull_request)

    @pytest.mark.asyncio
    async def test_assign_reviewers_github_exception(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test assign_reviewers when GitHub API raises an exception."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        mock_pull_request.user.login = "test-user"

        with patch.object(mock_pull_request, "create_review_request", side_effect=GithubException(404, "Not found")):
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await owners_file_handler.assign_reviewers(mock_pull_request)

                mock_comment.assert_called_once()
                assert "reviewer1 can not be added as reviewer" in mock_comment.call_args[0][0]

    @pytest.mark.parametrize(
        ("body", "grants"),
        [
            pytest.param("/add-allowed-user @newuser", True, id="own-line"),
            pytest.param("/ADD-Allowed-User @NewUser", True, id="different-casing"),
            pytest.param("  /add-allowed-user @newuser  ", True, id="surrounding-whitespace"),
            pytest.param("/add-allowed-user @newuser\nthanks", True, id="own-line-with-other-lines"),
            pytest.param("/add-allowed-user  @newuser", True, id="double-space"),
            pytest.param("/add-allowed-user\t@newuser", True, id="tab-separated"),
            pytest.param("/add-allowed-user @newuser   ", True, id="trailing-whitespace"),
            pytest.param("/add-allowed-user @newuser please", False, id="extra-tokens"),
            pytest.param("/add-allowed-user @newuser and @other-user", False, id="extra-mention"),
            pytest.param("please run /add-allowed-user @newuser later", False, id="inline-mention"),
            pytest.param("> /add-allowed-user @newuser", False, id="blockquote"),
            pytest.param("```\n/add-allowed-user @newuser\n```", False, id="backtick-fence"),
            pytest.param("~~~\n/add-allowed-user @newuser\n~~~", False, id="tilde-fence"),
            pytest.param("```\ncode\n```\n/add-allowed-user @newuser", True, id="after-closed-fence"),
        ],
    )
    def test_has_vouch_command(self, body: str, grants: bool) -> None:
        """Only a whole line outside fenced code blocks grants, whatever its whitespace runs:
        a mention, quote, or a line with extra tokens must not."""
        assert OwnersFileHandler._has_vouch_command(body, "/add-allowed-user @newuser") is grants

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_valid_user(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {".": {}}
        owners_file_handler.all_repository_approvers = ["approver1", "user1"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        with patch.object(owners_file_handler, "get_all_repository_maintainers") as mock_maintainers:
            with patch.object(owners_file_handler, "get_all_repository_collaborators") as mock_collaborators:
                with patch.object(owners_file_handler, "get_all_repository_contributors") as mock_contributors:
                    mock_maintainers.return_value = []
                    mock_collaborators.return_value = []
                    mock_contributors.return_value = []
                    with patch.object(mock_pull_request, "get_issue_comments", return_value=[]):
                        result = await owners_file_handler.is_user_valid_to_run_commands(mock_pull_request, "user1")
                        assert result is True

    @pytest.mark.parametrize(
        ("admin", "maintain"),
        [
            pytest.param(True, False, id="admin"),
            pytest.param(False, True, id="maintain"),
        ],
    )
    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_collaborator_listing_vouch(
        self,
        owners_file_handler: OwnersFileHandler,
        mock_pull_request: Mock,
        admin: bool,
        maintain: bool,
    ) -> None:
        """A commenter listed with admin or maintain can vouch for the target user."""
        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=[_vouch_comment("Voucher")],
            collaborators=[_named_user("voucher", admin=admin, maintain=maintain)],
        )

        assert result is True
        mock_create_comment.assert_not_called()

    @pytest.mark.parametrize(
        "collaborators",
        [
            pytest.param([], id="not-a-collaborator"),
            pytest.param([_named_user("voucher")], id="write-only-collaborator"),
        ],
    )
    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_non_maintainer_vouch_rejected(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock, collaborators: list[Mock]
    ) -> None:
        """An author who is not admin/maintain in the listing, or not listed at all, cannot vouch."""
        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=[_vouch_comment("voucher")],
            collaborators=collaborators,
        )

        assert result is False
        mock_create_comment.assert_called_once()
        assert "newuser is not allowed to run commands" in mock_create_comment.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_root_approver_vouch(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """A root OWNERS approver's `/add-allowed-user` comment is honoured, case-insensitively."""
        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=[_vouch_comment("rootapprover")],
            root_approvers=["RootApprover"],
        )

        assert result is True
        mock_create_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_own_line_vouch(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """The command on its own line, whatever its casing and whitespace runs, validates the user."""
        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=[_vouch_comment("rootapprover", "  /ADD-ALLOWED-USER\t @NewUser  ")],
            root_approvers=["RootApprover"],
        )

        assert result is True
        mock_create_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_inline_mention_not_a_vouch(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Even an authorized author only mentioning the command inline does not vouch."""
        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=[
                _vouch_comment("rootapprover", "I will run /add-allowed-user @newuser later"),
                _vouch_comment("org-admin", "/add-allowed-user @newuser please"),
            ],
            collaborators=[_named_user("org-admin", admin=True)],
            root_approvers=["rootapprover"],
        )

        assert result is False
        mock_create_comment.assert_called_once()
        assert "newuser is not allowed to run commands" in mock_create_comment.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_sub_directory_approver_vouch_rejected(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """A sub-directory approver is not a root approver, so their vouch is not honoured."""
        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=[_vouch_comment("subapprover")],
            owners={".": {}, "pkg": {"approvers": ["subapprover"]}},
        )

        assert result is False
        mock_create_comment.assert_called_once()
        message = mock_create_comment.call_args[0][0]
        assert "newuser is not allowed to run commands" in message
        assert "subapprover" not in message

    @pytest.mark.asyncio
    async def test_effective_maintainer_logins(self, owners_file_handler: OwnersFileHandler) -> None:
        """Only admin/maintain entries of the full (`affiliation="all"`) listing count, normalized."""
        owners_file_handler.repository.get_collaborators = Mock(
            return_value=[
                _named_user("Org-Admin", admin=True),
                _named_user("Maintainer", maintain=True),
                _named_user("Writer"),
            ]
        )
        api_call = AsyncMock(side_effect=lambda fn, *a, **k: fn(*a))

        with patch("webhook_server.libs.handlers.owners_files_handler.github_api_call", new=api_call):
            logins = await owners_file_handler.effective_maintainer_logins
            assert logins == {"org-admin", "maintainer"}
            # affiliation="all": org owners and team-inherited maintainers count too (#1268)
            owners_file_handler.repository.get_collaborators.assert_called_once_with()

            # Cached: a second access reuses the listing instead of issuing more API calls.
            assert await owners_file_handler.effective_maintainer_logins == logins
            owners_file_handler.repository.get_collaborators.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_vouch_uses_single_collaborator_listing(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Many vouching commenters cost one collaborator listing, not one permission call each."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {".": {}}
        owners_file_handler.all_repository_approvers = []
        owners_file_handler.all_pull_request_reviewers = []
        owners_file_handler.repository.get_collaborators = Mock(return_value=[])
        comments = [_vouch_comment(f"commenter{index}") for index in range(30)]

        with (
            patch.object(owners_file_handler, "get_all_repository_maintainers", return_value=[]),
            patch.object(owners_file_handler, "get_all_repository_collaborators", return_value=[]),
            patch.object(owners_file_handler, "get_all_repository_contributors", return_value=[]),
            patch.object(mock_pull_request, "get_issue_comments", return_value=comments),
            patch.object(mock_pull_request, "create_issue_comment") as mock_create_comment,
        ):
            result = await owners_file_handler.is_user_valid_to_run_commands(mock_pull_request, "newuser")

        assert result is False
        owners_file_handler.repository.get_collaborators.assert_called_once()
        mock_create_comment.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_authorized_vouch_survives_many_later_vouches(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """REGRESSION: an authorized vouch posted before many unauthorized ones still grants.

        The previous implementation walked comments newest-first and spent its lookup cap on the
        unauthorized commenters, so the older authorized vouch was never resolved and the target
        stayed refused.
        """
        comments = [_vouch_comment("org-admin")]
        comments.extend(_vouch_comment(f"commenter{index}") for index in range(30))

        result, mock_create_comment = await _run_vouch(
            owners_file_handler,
            mock_pull_request,
            comments=comments,
            collaborators=[_named_user("org-admin", admin=True)],
        )

        assert result is True
        mock_create_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_owners_approver_not_maintainer_allowed(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Guard: an OWNERS approver who is not a repository maintainer may still run commands
        (the `/merge` gate for SIG owners), without any comment or permission lookup."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {".": {}}
        owners_file_handler.all_repository_approvers = ["approver1"]
        owners_file_handler.all_pull_request_reviewers = []

        with patch.object(owners_file_handler, "get_all_repository_maintainers", return_value=[]):
            with patch.object(owners_file_handler, "get_all_repository_collaborators", return_value=[]):
                with patch.object(owners_file_handler, "get_all_repository_contributors", return_value=[]):
                    with patch.object(
                        owners_file_handler, "is_user_repository_maintainer", new=AsyncMock(return_value=False)
                    ) as mock_is_maintainer:
                        with patch.object(mock_pull_request, "get_issue_comments") as mock_get_comments:
                            result = await owners_file_handler.is_user_valid_to_run_commands(
                                mock_pull_request, "approver1"
                            )

                            assert result is True
                            mock_get_comments.assert_not_called()
                            mock_is_maintainer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_invalid_user_no_approval(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {".": {"approvers": ["rootapprover"]}}
        owners_file_handler.all_repository_approvers = ["approver1"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]

        with patch.object(owners_file_handler, "get_all_repository_maintainers") as mock_maintainers:
            with patch.object(owners_file_handler, "get_all_repository_collaborators") as mock_collaborators:
                with patch.object(owners_file_handler, "get_all_repository_contributors") as mock_contributors:
                    mock_maintainers.return_value = ["maintainer1"]
                    mock_collaborators.return_value = []
                    mock_contributors.return_value = []

                    mock_comment = Mock()
                    mock_comment.user.login = "maintainer1"
                    mock_comment.body = "Some other comment"

                    with patch.object(owners_file_handler.repository, "get_collaborators", return_value=[]):
                        with patch.object(mock_pull_request, "get_issue_comments") as mock_get_comments:
                            with patch.object(mock_pull_request, "create_issue_comment") as mock_create_comment:
                                mock_get_comments.return_value = [mock_comment]

                                result = await owners_file_handler.is_user_valid_to_run_commands(
                                    mock_pull_request, "invalid_user"
                                )

                                assert result is False
                                mock_create_comment.assert_called_once()
                                message = mock_create_comment.call_args[0][0]
                                assert "invalid_user is not allowed to run commands" in message
                                assert "maintainer1" in message
                                assert "rootapprover" in message

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_owners_allowed_users(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Users in OWNERS allowed-users can run commands without being collaborators."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"allowed-users": ["owners-allowed-user"]},
        }
        owners_file_handler.all_repository_approvers = ["approver1"]
        owners_file_handler.all_pull_request_reviewers = []

        with patch.object(owners_file_handler, "get_all_repository_maintainers", return_value=[]):
            with patch.object(owners_file_handler, "get_all_repository_collaborators", return_value=[]):
                with patch.object(owners_file_handler, "get_all_repository_contributors", return_value=[]):
                    result = await owners_file_handler.is_user_valid_to_run_commands(
                        mock_pull_request, "owners-allowed-user"
                    )
                    assert result is True

    @pytest.mark.asyncio
    async def test_valid_users_to_run_commands(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test valid_users_to_run_commands property includes OWNERS allowed-users."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"allowed-users": ["allowed-user1", "allowed-user2"]},
        }
        owners_file_handler.all_repository_approvers = ["approver1", "approver2"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]

        with patch.object(owners_file_handler, "get_all_repository_collaborators") as mock_collaborators:
            with patch.object(owners_file_handler, "get_all_repository_contributors") as mock_contributors:
                mock_collaborators.return_value = ["collaborator1", "collaborator2"]
                mock_contributors.return_value = ["contributor1", "contributor2"]

                result = await owners_file_handler.valid_users_to_run_commands

                expected = {
                    "approver1",
                    "approver2",
                    "reviewer1",
                    "reviewer2",
                    "collaborator1",
                    "collaborator2",
                    "contributor1",
                    "contributor2",
                    "allowed-user1",
                    "allowed-user2",
                }
                assert result == expected

    @pytest.mark.asyncio
    async def test_get_all_repository_contributors(self, owners_file_handler: OwnersFileHandler) -> None:
        mock_contributor1 = Mock()
        mock_contributor1.login = "contributor1"
        mock_contributor2 = Mock()
        mock_contributor2.login = "contributor2"

        with patch.object(
            owners_file_handler.repository, "get_contributors", return_value=[mock_contributor1, mock_contributor2]
        ):
            result = await owners_file_handler.get_all_repository_contributors()

            assert result == ["contributor1", "contributor2"]

    @pytest.mark.asyncio
    async def test_get_all_repository_collaborators(self, owners_file_handler: OwnersFileHandler) -> None:
        mock_collaborator1 = Mock()
        mock_collaborator1.login = "collaborator1"
        mock_collaborator2 = Mock()
        mock_collaborator2.login = "collaborator2"

        with patch.object(
            owners_file_handler.repository, "get_collaborators", return_value=[mock_collaborator1, mock_collaborator2]
        ) as mock_get_collaborators:
            result = await owners_file_handler.get_all_repository_collaborators()

            assert result == ["collaborator1", "collaborator2"]
            mock_get_collaborators.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_get_all_repository_maintainers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_maintainers method."""
        mock_admin = Mock()
        mock_admin.login = "admin_user"
        mock_admin.permissions.admin = True
        mock_admin.permissions.maintain = False

        mock_maintainer = Mock()
        mock_maintainer.login = "maintainer_user"
        mock_maintainer.permissions.admin = False
        mock_maintainer.permissions.maintain = True

        mock_regular = Mock()
        mock_regular.login = "regular_user"
        mock_regular.permissions.admin = False
        mock_regular.permissions.maintain = False

        mock_collaborators = [mock_admin, mock_maintainer, mock_regular]
        with patch.object(
            owners_file_handler.repository,
            "get_collaborators",
            return_value=mock_collaborators,
        ) as mock_get_collaborators:
            result = await owners_file_handler.get_all_repository_maintainers()

            assert result == ["admin_user", "maintainer_user"]
            mock_get_collaborators.assert_called_once_with(affiliation="direct")

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("role_name", "expected"),
        [
            ("admin", True),
            ("maintain", True),
            ("write", False),
            ("triage", False),
            ("none", False),
        ],
    )
    async def test_is_user_repository_maintainer(
        self, owners_file_handler: OwnersFileHandler, role_name: str, expected: bool
    ) -> None:
        """Effective admin/maintain role counts, anything lower does not.

        The legacy `permission` field reports `maintain` as "write", so `role_name` must be used.
        """
        owners_file_handler.repository.get_collaborator_role_name = Mock(return_value=role_name)
        api_call = AsyncMock(side_effect=lambda fn, *a, **k: fn(*a))

        with patch("webhook_server.libs.handlers.owners_files_handler.github_api_call", new=api_call):
            result = await owners_file_handler.is_user_repository_maintainer("some-user")

        assert result is expected
        # The blocking PyGithub call must be delegated to github_api_call (thread pool), not awaited directly
        api_call.assert_awaited_once()
        assert api_call.await_args.args[0] == owners_file_handler.repository.get_collaborator_role_name
        owners_file_handler.repository.get_collaborator_role_name.assert_called_once_with("some-user")

    @pytest.mark.asyncio
    async def test_is_user_repository_maintainer_unknown_user(self, owners_file_handler: OwnersFileHandler) -> None:
        """A user GitHub does not know for this repo is not a maintainer."""
        owners_file_handler.repository.get_collaborator_role_name = Mock(
            side_effect=UnknownObjectException(status=404, data={}, headers={})
        )

        with patch("asyncio.to_thread", new=AsyncMock(side_effect=lambda f, *a, **k: f(*a, **k))):
            result = await owners_file_handler.is_user_repository_maintainer("ghost-user")

        assert result is False

    @pytest.mark.parametrize(
        ("owners_entry", "sender"),
        [
            ("MyAkove", "MyAkove"),
            ("MyAkove", "myakove"),
            ("@MyAkove", "myakove"),
            ("  myakove  ", "myakove"),
        ],
    )
    @pytest.mark.asyncio
    async def test_is_root_approver(
        self, owners_file_handler: OwnersFileHandler, owners_entry: str, sender: str
    ) -> None:
        """Hand-written OWNERS logins match the GitHub sender case-insensitively and without `@`."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": [owners_entry], "reviewers": []}
        }

        assert await owners_file_handler.is_root_approver(sender) is True

    @pytest.mark.asyncio
    async def test_is_root_approver_non_approver(self, owners_file_handler: OwnersFileHandler) -> None:
        """A user that is not a root OWNERS approver is not a root approver."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {".": {"approvers": ["MyAkove"], "reviewers": []}}

        assert await owners_file_handler.is_root_approver("someone-else") is False

    @pytest.mark.asyncio
    async def test_repository_direct_collaborators(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test repository_direct_collaborators property fetches direct collaborators only."""
        mock_collaborators = ["direct_collaborator1", "direct_collaborator2"]
        owners_file_handler.repository.get_collaborators.return_value = mock_collaborators

        result = await owners_file_handler.repository_direct_collaborators

        assert result == mock_collaborators
        owners_file_handler.repository.get_collaborators.assert_called_once_with(affiliation="direct")

    @pytest.mark.asyncio
    async def test_repository_collaborators(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test repository_collaborators property."""
        mock_collaborators = ["collaborator1", "collaborator2"]
        owners_file_handler.repository.get_collaborators.return_value = mock_collaborators

        result = await owners_file_handler.repository_collaborators

        assert result == mock_collaborators
        owners_file_handler.repository.get_collaborators.assert_called_once()

    @pytest.mark.asyncio
    async def test_repository_contributors(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test repository_contributors property."""
        mock_contributors = ["contributor1", "contributor2"]
        owners_file_handler.repository.get_contributors.return_value = mock_contributors

        result = await owners_file_handler.repository_contributors

        assert result == mock_contributors
        owners_file_handler.repository.get_contributors.assert_called_once()

    @pytest.mark.asyncio
    async def test_root_reviewers_property(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_reviewers property."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"], "reviewers": ["reviewer1", "reviewer2"]}
        }

        result = owners_file_handler.root_reviewers

        assert result == ["reviewer1", "reviewer2"]

    @pytest.mark.asyncio
    async def test_root_approvers_property(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_approvers property."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1", "approver2"], "reviewers": ["reviewer1"]}
        }

        result = owners_file_handler.root_approvers

        assert result == ["approver1", "approver2"]

    @pytest.mark.asyncio
    async def test_root_reviewers_property_missing(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_reviewers property when root reviewers are missing."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"]}  # No reviewers
        }

        result = owners_file_handler.root_reviewers

        assert result == []

    @pytest.mark.asyncio
    async def test_root_approvers_property_missing(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_approvers property when root approvers are missing."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"reviewers": ["reviewer1"]}  # No approvers
        }

        result = owners_file_handler.root_approvers

        assert result == []
