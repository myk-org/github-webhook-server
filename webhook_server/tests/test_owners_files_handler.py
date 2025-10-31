from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml
from github.GithubException import GithubException

from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler, OwnersFileNotInitializedError


class TestOwnersFileHandler:
    """Test suite for OwnersFileHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
        mock_webhook.repository.full_name = "test-owner/test-repo"
        mock_webhook.add_pr_comment = AsyncMock()
        mock_webhook.request_pr_reviews = AsyncMock()
        # unified_api needs to be a Mock with async methods, not an AsyncMock itself
        mock_webhook.unified_api = Mock()
        mock_webhook.unified_api.request_reviews = AsyncMock()
        mock_webhook.unified_api.request_pr_reviews = AsyncMock()
        mock_webhook.unified_api.add_pr_comment = AsyncMock()
        mock_webhook.unified_api.create_issue_comment = AsyncMock()
        mock_webhook.unified_api.get_user_id = AsyncMock()
        mock_webhook.unified_api.add_assignees_by_login = AsyncMock()
        # Mock config
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(return_value=1000)
        return mock_webhook

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest instance."""
        mock_pr = Mock()
        mock_pr.id = "PR_kgDOTestId"
        mock_pr.number = 123
        mock_pr.base.ref = "main"
        mock_pr.user.login = "test-user"
        return mock_pr

    @pytest.fixture
    def owners_file_handler(self, mock_github_webhook: Mock) -> OwnersFileHandler:
        """Create an OwnersFileHandler instance."""
        return OwnersFileHandler(mock_github_webhook)

    @pytest.fixture
    def mock_tree(self) -> dict:
        """
        Create a mock git tree with OWNERS files (realistic after recursive tree fix).

        This represents the OUTPUT of get_git_tree() after recursive traversal,
        with full paths like "folder1/OWNERS", "folder2/OWNERS", etc.
        The new implementation builds these full paths during tree traversal.
        """
        return {
            "tree": [
                {"type": "blob", "path": "OWNERS"},
                {"type": "blob", "path": "folder1/OWNERS"},
                {"type": "blob", "path": "folder2/OWNERS"},
                {"type": "blob", "path": "folder/folder4/OWNERS"},
                {"type": "blob", "path": "folder5/OWNERS"},
                {"type": "blob", "path": "README.md"},  # Non-OWNERS file
            ]
        }

    @pytest.fixture
    def mock_content_files(self) -> dict[str, str]:
        """Create mock content files for different OWNERS files."""
        return {
            "OWNERS": yaml.dump({
                "approvers": ["root_approver1", "root_approver2"],
                "reviewers": ["root_reviewer1", "root_reviewer2"],
            }),
            "folder1/OWNERS": yaml.dump({
                "approvers": ["folder1_approver1", "folder1_approver2"],
                "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
            }),
            "folder2/OWNERS": yaml.dump({}),
            "folder/folder4/OWNERS": yaml.dump({
                "approvers": ["folder4_approver1", "folder4_approver2"],
                "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
            }),
            "folder5/OWNERS": yaml.dump({
                "root-approvers": False,
                "approvers": ["folder5_approver1", "folder5_approver2"],
                "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
            }),
        }

    @pytest.mark.asyncio
    async def test_initialize(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
        """Test the initialize method."""
        # Mock repository_data with GraphQL-formatted collaborators and contributors (Task 70)
        owners_file_handler.github_webhook.repository_data = {
            "collaborators": {
                "edges": [
                    {"permission": "WRITE", "node": {"login": "collab1"}},
                    {"permission": "ADMIN", "node": {"login": "collab2"}},
                ]
            },
            "mentionableUsers": {"nodes": [{"login": "contrib1"}]},
        }

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

                                result = await owners_file_handler.initialize(mock_pull_request)

                                assert result == owners_file_handler
                                assert owners_file_handler.changed_files == ["file1.py", "file2.py"]
                                assert owners_file_handler.all_repository_approvers_and_reviewers == {
                                    ".": {"approvers": ["user1"], "reviewers": ["user2"]}
                                }
                                assert owners_file_handler.all_repository_approvers == ["user1"]
                                assert owners_file_handler.all_repository_reviewers == ["user2"]
                                assert owners_file_handler.all_pull_request_approvers == ["user1"]
                                assert owners_file_handler.all_pull_request_reviewers == ["user2"]
                                # Verify cached collaborators and contributors (now SimpleNamespace objects)
                                assert len(owners_file_handler._repository_collaborators) == 2
                                assert owners_file_handler._repository_collaborators[0].login == "collab1"
                                assert owners_file_handler._repository_collaborators[0].permissions.admin is False
                                assert owners_file_handler._repository_collaborators[1].login == "collab2"
                                assert owners_file_handler._repository_collaborators[1].permissions.admin is True
                                assert len(owners_file_handler._repository_contributors) == 1
                                assert owners_file_handler._repository_contributors[0].login == "contrib1"
                                assert "collab1" in owners_file_handler._valid_users_to_run_commands
                                assert "collab2" in owners_file_handler._valid_users_to_run_commands
                                assert "contrib1" in owners_file_handler._valid_users_to_run_commands
                                assert "user1" in owners_file_handler._valid_users_to_run_commands
                                assert "user2" in owners_file_handler._valid_users_to_run_commands

    @pytest.mark.asyncio
    async def test_ensure_initialized_not_initialized(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _ensure_initialized raises error when not initialized."""
        with pytest.raises(OwnersFileNotInitializedError, match="initialize\\(\\) must be called first"):
            owners_file_handler._ensure_initialized()

    @pytest.mark.asyncio
    async def test_ensure_initialized_initialized(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test _ensure_initialized doesn't raise error when initialized."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.initialized = True  # Mark as initialized
        owners_file_handler._ensure_initialized()  # Should not raise

    @pytest.mark.asyncio
    async def test_list_changed_files(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
        """Test list_changed_files method."""
        mock_file1 = Mock()
        mock_file1.filename = "file1.py"
        mock_file2 = Mock()
        mock_file2.filename = "file2.py"
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_pull_request_files = AsyncMock(
            return_value=[mock_file1, mock_file2]
        )

        result = await owners_file_handler.list_changed_files(mock_pull_request)

        assert result == ["file1.py", "file2.py"]
        # Validate that the correct arguments were passed
        owners_file_handler.github_webhook.unified_api.get_pull_request_files.assert_called_once_with(
            "test", "repo", 123
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

    @pytest.mark.asyncio
    async def test_get_file_content(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
        """Test _get_file_content method."""
        mock_content = "test content"
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(return_value=mock_content)

        result = await owners_file_handler._get_file_content("test/path", mock_pull_request)

        assert result == (mock_content, "test/path")
        owners_file_handler.github_webhook.unified_api.get_file_contents.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_file_content_list_result(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test _get_file_content returns string from GraphQL API."""
        mock_content = "test content"
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(return_value=mock_content)

        result = await owners_file_handler._get_file_content("test/path", mock_pull_request)

        assert result == (mock_content, "test/path")

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers(
        self,
        owners_file_handler: OwnersFileHandler,
        mock_pull_request: Mock,
        mock_tree: dict,
        mock_content_files: dict[str, str],
    ) -> None:
        owners_file_handler.repository.full_name = "test/repo"

        owners_file_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(
            side_effect=lambda _o, _n, path, _ref: mock_content_files.get(path, "")
        )
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
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
    async def test_get_all_repository_approvers_and_reviewers_nested_paths(
        self,
        owners_file_handler: OwnersFileHandler,
        mock_pull_request: Mock,
    ) -> None:
        """Test that nested OWNERS files are discovered with full paths."""
        # Mock tree with deeply nested structure (realistic output from recursive get_git_tree)
        nested_tree = {
            "tree": [
                {"type": "blob", "path": "OWNERS"},
                {"type": "blob", "path": "src/OWNERS"},
                {"type": "blob", "path": "src/backend/OWNERS"},
                {"type": "blob", "path": "src/backend/handlers/OWNERS"},
                {"type": "blob", "path": "tests/unit/test_file.py"},
            ]
        }

        mock_content_files = {
            "OWNERS": yaml.dump({"approvers": ["root1"], "reviewers": ["root2"]}),
            "src/OWNERS": yaml.dump({"approvers": ["src1"], "reviewers": ["src2"]}),
            "src/backend/OWNERS": yaml.dump({"approvers": ["backend1"], "reviewers": ["backend2"]}),
            "src/backend/handlers/OWNERS": yaml.dump({"approvers": ["handler1"], "reviewers": ["handler2"]}),
        }

        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=nested_tree)
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(
            side_effect=lambda _o, _n, path, _ref: mock_content_files.get(path, "")
        )

        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)

        # Verify all nested OWNERS were discovered
        assert "." in result
        assert "src" in result
        assert "src/backend" in result
        assert "src/backend/handlers" in result

        # Verify content is correct
        assert result["."]["approvers"] == ["root1"]
        assert result["src"]["approvers"] == ["src1"]
        assert result["src/backend"]["approvers"] == ["backend1"]
        assert result["src/backend/handlers"]["approvers"] == ["handler1"]

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_too_many_files(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        mock_tree = {"tree": [{"type": "blob", "path": f"file{i}/OWNERS"} for i in range(1001)]}
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(
            return_value=yaml.dump({"approvers": [], "reviewers": []})
        )
        owners_file_handler.logger.error = Mock()
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
        assert len(result) == 1000
        owners_file_handler.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_custom_max_limit(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Test that custom max-owners-files config is respected."""
        # Set custom limit to 5
        mock_github_webhook.config.get_value = Mock(return_value=5)
        custom_handler = OwnersFileHandler(mock_github_webhook)

        mock_tree = {"tree": [{"type": "blob", "path": f"file{i}/OWNERS"} for i in range(10)]}
        custom_handler.repository.full_name = "test/repo"
        custom_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)
        custom_handler.github_webhook.unified_api.get_file_contents = AsyncMock(
            return_value=yaml.dump({"approvers": [], "reviewers": []})
        )
        custom_handler.logger.error = Mock()

        result = await custom_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)

        # Should only process 5 files because custom limit is 5
        assert len(result) == 5
        custom_handler.logger.error.assert_called_once()
        # Access the logged message directly instead of str(call_args)
        error_call = custom_handler.logger.error.call_args
        logged_message = error_call[0][0] if error_call[0] else ""
        assert ">5" in logged_message

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_invalid_yaml(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        mock_tree = {"tree": [{"type": "blob", "path": "OWNERS"}]}
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)
        mock_content = "invalid: yaml: content: ["
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(return_value=mock_content)
        owners_file_handler.logger.exception = Mock()
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
        assert result == {}
        owners_file_handler.logger.exception.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_invalid_content(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        mock_tree = {"tree": [{"type": "blob", "path": "OWNERS"}]}
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)
        mock_content = yaml.dump({"approvers": "not_a_list"})
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(return_value=mock_content)
        owners_file_handler.logger.warning = Mock()
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
        assert result == {}
        owners_file_handler.logger.warning.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_fetch_exception(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test that exceptions during OWNERS file fetch are logged with logger.exception."""
        mock_tree = {"tree": [{"type": "blob", "path": "test/OWNERS"}]}
        owners_file_handler.repository.full_name = "test/repo"
        owners_file_handler.github_webhook.unified_api.get_git_tree = AsyncMock(return_value=mock_tree)

        # Make get_file_contents raise a specific exception type
        test_exception = FileNotFoundError("OWNERS file not found")
        owners_file_handler.github_webhook.unified_api.get_file_contents = AsyncMock(side_effect=test_exception)
        owners_file_handler.logger.error = Mock()

        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)

        # Should return empty dict since file fetch failed
        assert result == {}

        # Verify error was logged with exc_info (exception object is passed directly)
        owners_file_handler.logger.error.assert_called_once()
        call_args = owners_file_handler.logger.error.call_args
        assert "Failed to fetch OWNERS file" in call_args[0][0]
        # exc_info can be either the exception object itself or a tuple
        # The handler passes the exception directly: exc_info=result
        exc_info = call_args[1]["exc_info"]
        assert exc_info is test_exception or (isinstance(exc_info, tuple) and exc_info[1] is test_exception)

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_approvers method."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["user1", "user2"], "reviewers": ["user3"]},
            "folder1": {"approvers": ["user4"], "reviewers": ["user5"]},
            "folder2": {"reviewers": ["user6"]},  # No approvers
        }
        owners_file_handler.initialized = True  # Mark as initialized

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
        owners_file_handler.initialized = True  # Mark as initialized

        result = await owners_file_handler.get_all_repository_reviewers()

        assert result == ["user2", "user3", "user5"]

    @pytest.mark.asyncio
    async def test_get_all_pull_request_approvers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_pull_request_approvers method."""
        owners_file_handler.changed_files = ["file1.py"]

        with patch.object(owners_file_handler, "owners_data_for_changed_files", new=AsyncMock()) as mock_owners_data:
            mock_owners_data.return_value = {
                ".": {"approvers": ["user1", "user2"], "reviewers": ["user3"]},
                "folder1": {"approvers": ["user4"], "reviewers": ["user5"]},
            }

            result = await owners_file_handler.get_all_pull_request_approvers()

            assert result == ["user1", "user2", "user4"]

    @pytest.mark.asyncio
    async def test_get_all_pull_request_reviewers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_pull_request_reviewers method."""
        owners_file_handler.changed_files = ["file1.py"]

        with patch.object(owners_file_handler, "owners_data_for_changed_files", new=AsyncMock()) as mock_owners_data:
            mock_owners_data.return_value = {
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

        owners_file_handler.initialized = True  # Mark as initialized
        result = await owners_file_handler.owners_data_for_changed_files()

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
        owners_file_handler.initialized = True  # Mark as initialized

        result = await owners_file_handler.owners_data_for_changed_files()

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
    async def test_owners_data_for_changed_files_mixed_root_approvers(
        self, owners_file_handler: OwnersFileHandler
    ) -> None:
        """Test owners_data_for_changed_files with mixed root-approvers settings.

        This test catches a logic bug where one folder disables root approvers
        while another requires them, ensuring proper handling of the root-approvers key.
        """
        owners_file_handler.changed_files = [
            "folder_no_root/file1.py",  # root-approvers: False
            "folder_with_root/file2.py",  # root-approvers not set (defaults to True)
            "another_folder/file3.py",  # no OWNERS file, should use root
        ]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
            "folder_no_root": {
                "root-approvers": False,
                "approvers": ["folder_no_root_approver1"],
                "reviewers": ["folder_no_root_reviewer1"],
            },
            "folder_with_root": {
                "approvers": ["folder_with_root_approver1"],
                "reviewers": ["folder_with_root_reviewer1"],
            },
        }
        owners_file_handler.initialized = True  # Mark as initialized

        result = await owners_file_handler.owners_data_for_changed_files()

        expected = {
            "folder_no_root": {
                "root-approvers": False,
                "approvers": ["folder_no_root_approver1"],
                "reviewers": ["folder_no_root_reviewer1"],
            },
            "folder_with_root": {
                "approvers": ["folder_with_root_approver1"],
                "reviewers": ["folder_with_root_reviewer1"],
            },
            ".": {"approvers": ["root_approver1"], "reviewers": ["root_reviewer1"]},
        }
        # When root-approvers is False, root approvers should not be included
        assert result == expected

        # Verify root approvers are not mixed into folder_no_root
        folder_no_root_data = result.get("folder_no_root", {})
        assert "root_approver1" not in folder_no_root_data.get("approvers", [])
        assert folder_no_root_data.get("root-approvers") is False

    @pytest.mark.asyncio
    async def test_assign_reviewers(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2", "test-user"]
        owners_file_handler.initialized = True  # Mark as initialized
        mock_pull_request.user.login = "test-user"

        with patch.object(
            owners_file_handler.github_webhook.unified_api, "request_pr_reviews", new_callable=AsyncMock
        ) as mock_request:
            await owners_file_handler.assign_reviewers(mock_pull_request)
            # Should be called once with all reviewers (batch assignment), excluding PR author
            assert mock_request.call_count == 1
            # Verify the call has correct arguments: pull_request, reviewers_list
            call_args = mock_request.call_args
            # Arguments are: pull_request, reviewers_list
            assert call_args[0][0] == mock_pull_request  # PR object
            reviewers_added = call_args[0][1]  # reviewers list
            assert set(reviewers_added) == {"reviewer1", "reviewer2"}

    @pytest.mark.asyncio
    async def test_assign_reviewers_github_exception(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test assign_reviewers when GitHub API raises an exception."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        owners_file_handler.initialized = True  # Mark as initialized
        mock_pull_request.user.login = "test-user"
        # Setup mock as REST PullRequest (use base.repo path)
        mock_pull_request.base.repo.owner.login = "test-owner"
        mock_pull_request.base.repo.name = "test-repo"

        with patch.object(
            owners_file_handler.github_webhook.unified_api,
            "request_pr_reviews",
            new_callable=AsyncMock,
            side_effect=GithubException(404, "Not found"),
        ):
            await owners_file_handler.assign_reviewers(mock_pull_request)
            # Verify add_pr_comment was called for the error
            # Method doesn't fail but posts error comment via unified_api
            mock_add_comment = owners_file_handler.github_webhook.unified_api.add_pr_comment
            assert mock_add_comment.call_count == 1
            # Check the error message was included - call_args is (args, kwargs)
            call_args = mock_add_comment.call_args
            # Arguments are: pull_request, body
            assert call_args[0][0] == mock_pull_request  # pull_request
            # Format: "Failed to assign reviewers reviewer1: [GithubException]"
            assert "Failed to assign reviewers reviewer1" in call_args[0][1]
            assert "GithubException" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_valid_user(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers = ["approver1", "user1"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1", "user1"], "reviewers": ["reviewer1"], "allowed-users": []},
        }
        # Cache valid users
        owners_file_handler._valid_users_to_run_commands = {"approver1", "user1", "reviewer1"}

        with patch.object(owners_file_handler, "get_all_repository_maintainers", new=AsyncMock(return_value=[])):
            # This test path uses unified_api.get_issue_comments, not PR.get_issue_comments
            # since the user is already valid, so we don't need this patch
            owners_file_handler.initialized = True  # Mark as initialized
            result = await owners_file_handler.is_user_valid_to_run_commands(mock_pull_request, "user1")
            assert result is True

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_invalid_user_with_approval(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers = ["approver1"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"], "reviewers": ["reviewer1"], "allowed-users": []},
        }
        # Cache valid users (invalid_user not in cache)
        owners_file_handler._valid_users_to_run_commands = {"approver1", "reviewer1"}

        with patch.object(
            owners_file_handler, "get_all_repository_maintainers", new=AsyncMock(return_value=["maintainer1"])
        ):
            mock_comment = Mock()
            mock_comment.user.login = "maintainer1"
            mock_comment.body = "/add-allowed-user @invalid_user"

            owners_file_handler.repository.full_name = "test/repo"
            owners_file_handler.github_webhook.unified_api.get_issue_comments = AsyncMock(return_value=[mock_comment])

            owners_file_handler.initialized = True  # Mark as initialized
            result = await owners_file_handler.is_user_valid_to_run_commands(mock_pull_request, "invalid_user")

            assert result is True

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_invalid_user_no_approval(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers = ["approver1"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"], "reviewers": ["reviewer1"], "allowed-users": []},
        }
        # Cache valid users (invalid_user not in cache)
        owners_file_handler._valid_users_to_run_commands = {"approver1", "reviewer1"}

        with patch.object(
            owners_file_handler, "get_all_repository_maintainers", new=AsyncMock(return_value=["maintainer1"])
        ):
            mock_comment = Mock()
            mock_comment.user.login = "maintainer1"
            mock_comment.body = "Some other comment"

            # Mock unified_api.get_issue_comments
            owners_file_handler.repository.full_name = "test/repo"
            owners_file_handler.github_webhook.unified_api.get_issue_comments = AsyncMock(return_value=[mock_comment])

            with patch.object(
                owners_file_handler.github_webhook.unified_api, "add_pr_comment", new_callable=AsyncMock
            ) as mock_add_comment:
                owners_file_handler.initialized = True  # Mark as initialized
                result = await owners_file_handler.is_user_valid_to_run_commands(mock_pull_request, "invalid_user")

                assert result is False
                # The method calls unified_api.add_pr_comment(pr, comment)
                assert mock_add_comment.call_count == 1
                # Arguments are: pull_request, comment_message
                call_args = mock_add_comment.call_args
                assert "invalid_user is not allowed to run retest commands" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_valid_users_to_run_commands(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test valid_users_to_run_commands property."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers = ["approver1", "approver2"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]
        # Cache valid users
        owners_file_handler._valid_users_to_run_commands = {
            "approver1",
            "approver2",
            "reviewer1",
            "reviewer2",
            "collaborator1",
            "collaborator2",
            "contributor1",
            "contributor2",
        }

        owners_file_handler.initialized = True  # Mark as initialized
        result = owners_file_handler.valid_users_to_run_commands

        expected = {
            "approver1",
            "approver2",
            "reviewer1",
            "reviewer2",
            "collaborator1",
            "collaborator2",
            "contributor1",
            "contributor2",
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_get_all_repository_contributors(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_contributors method."""
        mock_contributor1 = Mock(login="contributor1")
        mock_contributor2 = Mock(login="contributor2")

        # Initialize the handler with cached contributors
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler._repository_contributors = [mock_contributor1, mock_contributor2]

        owners_file_handler.initialized = True  # Mark as initialized
        result = await owners_file_handler.get_all_repository_contributors()
        assert result == ["contributor1", "contributor2"]

    @pytest.mark.asyncio
    async def test_get_all_repository_collaborators(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_collaborators method."""
        mock_collaborator1 = Mock(login="collaborator1")
        mock_collaborator2 = Mock(login="collaborator2")

        # Initialize the handler with cached collaborators
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler._repository_collaborators = [mock_collaborator1, mock_collaborator2]

        owners_file_handler.initialized = True  # Mark as initialized
        result = await owners_file_handler.get_all_repository_collaborators()
        assert result == ["collaborator1", "collaborator2"]

    @pytest.mark.asyncio
    async def test_get_all_repository_maintainers(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test get_all_repository_maintainers method."""
        mock_admin = Mock(login="admin_user", permissions=Mock(admin=True, maintain=False))
        mock_maintainer = Mock(login="maintainer_user", permissions=Mock(admin=False, maintain=True))
        mock_regular = Mock(login="regular_user", permissions=Mock(admin=False, maintain=False))

        # Initialize the handler with cached collaborators
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler._repository_collaborators = [mock_admin, mock_maintainer, mock_regular]

        owners_file_handler.initialized = True  # Mark as initialized
        result = await owners_file_handler.get_all_repository_maintainers()
        assert result == ["admin_user", "maintainer_user"]

    @pytest.mark.asyncio
    async def test_root_reviewers_property(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_reviewers property."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"], "reviewers": ["reviewer1", "reviewer2"]}
        }

        owners_file_handler.initialized = True  # Mark as initialized
        result = owners_file_handler.root_reviewers

        assert result == ["reviewer1", "reviewer2"]

    @pytest.mark.asyncio
    async def test_root_approvers_property(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_approvers property."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1", "approver2"], "reviewers": ["reviewer1"]}
        }

        owners_file_handler.initialized = True  # Mark as initialized
        result = owners_file_handler.root_approvers

        assert result == ["approver1", "approver2"]

    @pytest.mark.asyncio
    async def test_root_reviewers_property_missing(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_reviewers property when root reviewers are missing."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"approvers": ["approver1"]}  # No reviewers
        }

        owners_file_handler.initialized = True  # Mark as initialized
        result = owners_file_handler.root_reviewers

        assert result == []

    @pytest.mark.asyncio
    async def test_root_approvers_property_missing(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test root_approvers property when root approvers are missing."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_repository_approvers_and_reviewers = {
            ".": {"reviewers": ["reviewer1"]}  # No approvers
        }

        owners_file_handler.initialized = True  # Mark as initialized
        result = owners_file_handler.root_approvers

        assert result == []
