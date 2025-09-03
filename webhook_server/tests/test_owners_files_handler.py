from unittest.mock import AsyncMock, Mock, call, patch

import pytest
import yaml

from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.tests.conftest import ContentFile


class TestOwnersFileHandler:
    """Test suite for OwnersFileHandler class."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook instance."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.repository = Mock()
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

    @pytest.fixture
    def mock_tree(self) -> Mock:
        """Create a mock git tree with OWNERS files."""
        tree = Mock()
        tree.tree = [
            Mock(type="blob", path="OWNERS"),
            Mock(type="blob", path="folder1/OWNERS"),
            Mock(type="blob", path="folder2/OWNERS"),
            Mock(type="blob", path="folder/folder4/OWNERS"),
            Mock(type="blob", path="folder5/OWNERS"),
            Mock(type="blob", path="README.md"),  # Non-OWNERS file
        ]
        return tree

    @pytest.fixture
    def mock_content_files(self) -> dict[str, ContentFile]:
        """Create mock content files for different OWNERS files."""
        return {
            "OWNERS": ContentFile(
                yaml.dump({
                    "approvers": ["root_approver1", "root_approver2"],
                    "reviewers": ["root_reviewer1", "root_reviewer2"],
                })
            ),
            "folder1/OWNERS": ContentFile(
                yaml.dump({
                    "approvers": ["folder1_approver1", "folder1_approver2"],
                    "reviewers": ["folder1_reviewer1", "folder1_reviewer2"],
                })
            ),
            "folder2/OWNERS": ContentFile(yaml.dump({})),
            "folder/folder4/OWNERS": ContentFile(
                yaml.dump({
                    "approvers": ["folder4_approver1", "folder4_approver2"],
                    "reviewers": ["folder4_reviewer1", "folder4_reviewer2"],
                })
            ),
            "folder5/OWNERS": ContentFile(
                yaml.dump({
                    "root-approvers": False,
                    "approvers": ["folder5_approver1", "folder5_approver2"],
                    "reviewers": ["folder5_reviewer1", "folder5_reviewer2"],
                })
            ),
        }

    @pytest.mark.asyncio
    async def test_initialize(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
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
    async def test_list_changed_files(self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock) -> None:
        """Test list_changed_files method."""
        mock_file1 = Mock()
        mock_file1.filename = "file1.py"
        mock_file2 = Mock()
        mock_file2.filename = "file2.py"
        mock_pull_request.get_files.return_value = [mock_file1, mock_file2]

        result = await owners_file_handler.list_changed_files(mock_pull_request)

        assert result == ["file1.py", "file2.py"]
        mock_pull_request.get_files.assert_called_once()

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
        mock_content = ContentFile("test content")
        owners_file_handler.repository.get_contents = Mock(return_value=mock_content)

        result = await owners_file_handler._get_file_content("test/path", mock_pull_request)

        assert result == (mock_content, "test/path")
        owners_file_handler.repository.get_contents.assert_called_once_with("test/path", "main")

    @pytest.mark.asyncio
    async def test_get_file_content_list_result(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test _get_file_content when repository returns a list."""
        mock_content = ContentFile("test content")
        owners_file_handler.repository.get_contents = Mock(return_value=[mock_content])

        result = await owners_file_handler._get_file_content("test/path", mock_pull_request)

        assert result == (mock_content, "test/path")

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers(
        self,
        owners_file_handler: OwnersFileHandler,
        mock_pull_request: Mock,
        mock_tree: Mock,
        mock_content_files: dict[str, ContentFile],
    ) -> None:
        owners_file_handler.repository.get_git_tree = Mock(return_value=mock_tree)

        def mock_get_contents(path: str, ref: str) -> ContentFile:
            return mock_content_files.get(path, ContentFile(""))

        owners_file_handler.repository.get_contents = Mock(side_effect=mock_get_contents)
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
    async def test_get_all_repository_approvers_and_reviewers_too_many_files(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        mock_tree = Mock()
        mock_tree.tree = [Mock(type="blob", path=f"file{i}/OWNERS") for i in range(1001)]
        owners_file_handler.repository.get_git_tree = Mock(return_value=mock_tree)
        owners_file_handler.logger.error = Mock()
        owners_file_handler.repository.get_contents = Mock(
            return_value=ContentFile(yaml.dump({"approvers": [], "reviewers": []}))
        )
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
        assert len(result) == 1000
        owners_file_handler.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_invalid_yaml(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        mock_tree = Mock()
        mock_tree.tree = [Mock(type="blob", path="OWNERS")]
        owners_file_handler.repository.get_git_tree = Mock(return_value=mock_tree)
        mock_content = ContentFile("invalid: yaml: content: [")
        owners_file_handler.repository.get_contents = Mock(return_value=mock_content)
        owners_file_handler.logger.error = Mock()
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
        assert result == {}
        owners_file_handler.logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_all_repository_approvers_and_reviewers_invalid_content(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        mock_tree = Mock()
        mock_tree.tree = [Mock(type="blob", path="OWNERS")]
        owners_file_handler.repository.get_git_tree = Mock(return_value=mock_tree)
        mock_content = ContentFile(yaml.dump({"approvers": "not_a_list"}))
        owners_file_handler.repository.get_contents = Mock(return_value=mock_content)
        owners_file_handler.logger.error = Mock()
        result = await owners_file_handler.get_all_repository_approvers_and_reviewers(mock_pull_request)
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
        owners_file_handler.changed_files = ["file1.py"]

        with patch.object(owners_file_handler, "owners_data_for_changed_files") as mock_owners_data:
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

        with patch.object(owners_file_handler, "owners_data_for_changed_files") as mock_owners_data:
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
    async def test_assign_reviewers_github_exception(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        """Test assign_reviewers when GitHub API raises an exception."""
        owners_file_handler.changed_files = ["file1.py"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1"]
        mock_pull_request.user.login = "test-user"

        from github.GithubException import GithubException

        with patch.object(mock_pull_request, "create_review_request", side_effect=GithubException(404, "Not found")):
            with patch.object(mock_pull_request, "create_issue_comment") as mock_comment:
                await owners_file_handler.assign_reviewers(mock_pull_request)

                mock_comment.assert_called_once()
                assert "reviewer1 can not be added as reviewer" in mock_comment.call_args[0][0]

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_valid_user(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
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

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_invalid_user_with_approval(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
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
                    mock_comment.body = "/add-allowed-user @invalid_user"

                    with patch.object(mock_pull_request, "get_issue_comments") as mock_get_comments:
                        mock_get_comments.return_value = [mock_comment]

                        result = await owners_file_handler.is_user_valid_to_run_commands(
                            mock_pull_request, "invalid_user"
                        )

                        assert result is True

    @pytest.mark.asyncio
    async def test_is_user_valid_to_run_commands_invalid_user_no_approval(
        self, owners_file_handler: OwnersFileHandler, mock_pull_request: Mock
    ) -> None:
        owners_file_handler.changed_files = ["file1.py"]
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

                    with patch.object(mock_pull_request, "get_issue_comments") as mock_get_comments:
                        with patch.object(mock_pull_request, "create_issue_comment") as mock_create_comment:
                            mock_get_comments.return_value = [mock_comment]

                            result = await owners_file_handler.is_user_valid_to_run_commands(
                                mock_pull_request, "invalid_user"
                            )

                            assert result is False
                            mock_create_comment.assert_called_once()
                            assert (
                                "invalid_user is not allowed to run retest commands"
                                in mock_create_comment.call_args[0][0]
                            )

    @pytest.mark.asyncio
    async def test_valid_users_to_run_commands(self, owners_file_handler: OwnersFileHandler) -> None:
        """Test valid_users_to_run_commands property."""
        owners_file_handler.changed_files = ["file1.py"]
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
        ):
            result = await owners_file_handler.get_all_repository_collaborators()

            assert result == ["collaborator1", "collaborator2"]

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

        with patch.object(
            owners_file_handler.repository,
            "get_collaborators",
            return_value=[mock_admin, mock_maintainer, mock_regular],
        ):
            result = await owners_file_handler.get_all_repository_maintainers()

            assert result == ["admin_user", "maintainer_user"]

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
