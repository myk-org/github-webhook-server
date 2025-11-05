"""Edge cases and REST operation tests for unified GitHub API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from github.Commit import Commit

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI

# Test token constant to avoid S106 security warnings
TEST_GITHUB_TOKEN = (
    "ghp_test1234567890abcdefghijklmnopqrstuvwxyz"  # pragma: allowlist secret  # noqa: S105  # gitleaks:allow
)


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return MagicMock()


@pytest.fixture
def mock_config():
    """Create a mock config."""
    config = MagicMock()
    config.get_value = MagicMock(return_value=9)  # For tree-max-depth
    return config


@pytest.fixture
def mock_graphql_client():
    """Create a mock GraphQL client."""
    client = AsyncMock()
    client.execute = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.fixture
def mock_rest_client():
    """Create a mock REST client."""
    client = MagicMock()
    client.close = MagicMock()
    return client


@pytest.fixture
async def initialized_api(mock_graphql_client, mock_rest_client, mock_logger, mock_config):
    """Create initialized UnifiedGitHubAPI."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)
    api.graphql_client = mock_graphql_client
    api.rest_client = mock_rest_client
    api._initialized = True
    return api


# ===== Helper Functions =====


def create_mock_to_thread_simple(rest_client, repo_mock=None, result_mock=None):
    """
    Create a simple mock_to_thread helper for basic repo operations.

    Args:
        rest_client: Mock REST client
        repo_mock: Mock repository object (optional)
        result_mock: Mock result object (optional)

    Returns:
        Async function that mocks asyncio.to_thread behavior
    """

    async def mock_to_thread(_func, *_args):
        # Route: REST client -> get repository
        if _func == rest_client.get_repo:
            return repo_mock
        # Route: Repository -> get pull request by number
        elif repo_mock and hasattr(repo_mock, "get_pull") and _func == repo_mock.get_pull:
            return result_mock
        # Route: Repository -> get branch by name
        elif repo_mock and hasattr(repo_mock, "get_branch") and _func == repo_mock.get_branch:
            return result_mock
        # Route: Repository -> get file contents
        elif repo_mock and hasattr(repo_mock, "get_contents") and _func == repo_mock.get_contents:
            return result_mock
        # Route: Branch -> get protection settings
        elif result_mock and hasattr(result_mock, "get_protection") and _func == result_mock.get_protection:
            return result_mock.get_protection.return_value
        # Route: PR -> get specific issue comment
        elif result_mock and hasattr(result_mock, "get_issue_comment") and _func == result_mock.get_issue_comment:
            return result_mock.get_issue_comment.return_value
        # Route: Lambda function execution (e.g., list comprehensions)
        elif callable(_func):
            # Handle lambda functions
            return _func()
        return None

    return mock_to_thread


# ===== Lazy Initialization Tests =====


@pytest.mark.asyncio
async def test_lazy_init_already_initialized(mock_logger, mock_config):
    """Test that initialize() returns early if already initialized."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_rest_class,
    ):
        # First initialization
        await api.initialize()

        # Reset call counts
        mock_gql_class.reset_mock()
        mock_rest_class.reset_mock()

        # Second initialization should return early
        await api.initialize()

        # Should not create new clients
        mock_gql_class.assert_not_called()
        mock_rest_class.assert_not_called()


@pytest.mark.asyncio
async def test_lazy_init_get_rate_limit(mock_logger, mock_config):
    """Test lazy initialization in get_rate_limit."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"rateLimit": {"limit": 5000}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_rate_limit()

        assert api._initialized
        assert result["limit"] == 5000


@pytest.mark.asyncio
async def test_lazy_init_get_viewer(mock_logger, mock_config):
    """Test lazy initialization in get_viewer."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"viewer": {"login": "test"}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_viewer()

        assert api._initialized
        assert result["login"] == "test"


@pytest.mark.asyncio
async def test_lazy_init_get_repository(mock_logger, mock_config):
    """Test lazy initialization in get_repository."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"id": "R_123"}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_repository("owner", "repo")

        assert api._initialized
        assert result["id"] == "R_123"


@pytest.mark.asyncio
async def test_lazy_init_get_pull_request(mock_logger, mock_config):
    """Test lazy initialization in get_pull_request_data."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequest": {"id": "PR_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_pull_request_data("owner", "repo", 1)

        assert api._initialized
        assert result["id"] == "PR_123"


@pytest.mark.asyncio
async def test_lazy_init_get_pull_requests(mock_logger, mock_config):
    """Test lazy initialization in get_pull_requests."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequests": {"nodes": []}}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_pull_requests("owner", "repo")

        assert api._initialized
        assert "nodes" in result


@pytest.mark.asyncio
async def test_lazy_init_get_commit(mock_logger, mock_config):
    """Test lazy initialization in get_commit."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"object": {"oid": "abc123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_commit("owner", "repo", "abc123")

        assert api._initialized
        assert result["oid"] == "abc123"


@pytest.mark.asyncio
async def test_lazy_init_remove_labels(mock_logger, mock_config):
    """Test lazy initialization in remove_labels."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={})
        mock_gql_class.return_value = mock_gql

        await api.remove_labels("PR_123", ["label1"])

        assert api._initialized


@pytest.mark.asyncio
async def test_lazy_init_add_assignees(mock_logger, mock_config):
    """Test lazy initialization in add_assignees."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={})
        mock_gql_class.return_value = mock_gql

        await api.add_assignees("PR_123", ["U_123"])

        assert api._initialized


@pytest.mark.asyncio
async def test_lazy_init_create_issue(mock_logger, mock_config):
    """Test lazy initialization in create_issue."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"createIssue": {"issue": {"id": "I_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.create_issue("R_123", "Test Issue")

        assert api._initialized
        assert result["id"] == "I_123"


@pytest.mark.asyncio
async def test_lazy_init_request_reviews(mock_logger, mock_config):
    """Test lazy initialization in request_reviews."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={})
        mock_gql_class.return_value = mock_gql

        await api.request_reviews("PR_123", ["U_123"])

        assert api._initialized


@pytest.mark.asyncio
async def test_lazy_init_update_pull_request(mock_logger, mock_config):
    """Test lazy initialization in update_pull_request."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"updatePullRequest": {"pullRequest": {"id": "PR_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.update_pull_request("PR_123", title="New")

        assert api._initialized
        assert result["id"] == "PR_123"


@pytest.mark.asyncio
async def test_lazy_init_enable_automerge(mock_logger, mock_config):
    """Test lazy initialization in enable_pull_request_automerge."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={})
        mock_gql_class.return_value = mock_gql

        await api.enable_pull_request_automerge("PR_123")

        assert api._initialized


@pytest.mark.asyncio
async def test_lazy_init_get_user_id(mock_logger, mock_config):
    """Test lazy initialization in get_user_id."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"user": {"id": "U_123"}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_user_id("testuser")

        assert api._initialized
        assert result == "U_123"


@pytest.mark.asyncio
async def test_lazy_init_get_label_id(mock_logger, mock_config):
    """Test lazy initialization in get_label_id."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"label": {"id": "LA_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_label_id("owner", "repo", "bug")

        assert api._initialized
        assert result == "LA_123"


@pytest.mark.asyncio
async def test_lazy_init_create_label(mock_logger, mock_config):
    """Test lazy initialization in create_label."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"createLabel": {"label": {"id": "LA_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.create_label("R_123", "bug", "ff0000")

        assert api._initialized
        assert result["id"] == "LA_123"


@pytest.mark.asyncio
async def test_lazy_init_update_label(mock_logger, mock_config):
    """Test lazy initialization in update_label."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"updateLabel": {"label": {"id": "LA_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.update_label("LA_123", "00ff00")

        assert api._initialized
        assert result["id"] == "LA_123"


# ===== File Operations Tests =====


@pytest.mark.asyncio
async def test_lazy_init_get_file_contents(mock_logger, mock_config):
    """Test lazy initialization in get_file_contents."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"object": {"isBinary": False, "text": "content"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_file_contents("owner", "repo", "README.md")

        assert api._initialized
        assert result == "content"


@pytest.mark.asyncio
async def test_get_file_contents_text(initialized_api, mock_graphql_client):
    """Test get_file_contents for text files."""
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": False, "text": "file content"}}}

    result = await initialized_api.get_file_contents("owner", "repo", "README.md")

    assert result == "file content"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_file_contents_binary_fallback(initialized_api, mock_graphql_client):
    """Test get_file_contents raises ValueError for binary files."""
    # Mock GraphQL returning binary file
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": True, "text": None}}}

    # Should raise ValueError, not fall back to REST
    with pytest.raises(ValueError, match="Binary file not supported"):
        await initialized_api.get_file_contents("owner", "repo", "image.png")

    # Verify only GraphQL was called (no REST fallback)
    assert mock_graphql_client.execute.call_count == 1


@pytest.mark.asyncio
async def test_get_file_contents_null_text_fallback(initialized_api, mock_graphql_client):
    """Test get_file_contents raises ValueError when text is None."""
    # Mock GraphQL returning file with null text (binary or empty)
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": False, "text": None}}}

    # Should raise ValueError when text is None
    with pytest.raises(ValueError, match="Binary file not supported"):
        await initialized_api.get_file_contents("owner", "repo", "file.txt")

    # Verify only GraphQL was called (no REST fallback)
    assert mock_graphql_client.execute.call_count == 1


@pytest.mark.asyncio
async def test_get_file_contents_non_utf8_binary(initialized_api, mock_graphql_client):
    """Test get_file_contents raises ValueError for binary files."""
    # Mock GraphQL returning binary file (non-UTF-8 content)
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": True, "text": None}}}

    # Should raise ValueError for binary files
    with pytest.raises(ValueError, match="Binary file not supported"):
        await initialized_api.get_file_contents("owner", "repo", "binary.dat")

    # Verify only GraphQL was called (no REST fallback)
    assert mock_graphql_client.execute.call_count == 1


@pytest.mark.asyncio
async def test_get_file_contents_file_not_found(initialized_api, mock_graphql_client):
    """Test get_file_contents raises FileNotFoundError when blob is None."""
    # Mock GraphQL returning None for object (file doesn't exist)
    mock_graphql_client.execute.return_value = {"repository": {"object": None}}

    with pytest.raises(FileNotFoundError, match="File not found"):
        await initialized_api.get_file_contents("owner", "repo", "nonexistent.txt")


# ===== Error Handling Tests =====


@pytest.mark.asyncio
async def test_add_comment_error_handling(initialized_api, mock_graphql_client, mock_logger):
    """Test add_comment error handling and logging."""

    mock_graphql_client.execute.side_effect = GraphQLError("GraphQL error")

    with pytest.raises(GraphQLError, match="GraphQL error"):
        await initialized_api.add_comment("PR_123", "Test")

    # Verify error was logged
    mock_logger.exception.assert_called()


@pytest.mark.asyncio
async def test_add_comment_missing_node(initialized_api, mock_graphql_client, mock_logger):
    """Test add_comment with missing comment node in response."""
    mock_graphql_client.execute.return_value = {"addComment": {}}

    with pytest.raises(KeyError):
        await initialized_api.add_comment("PR_123", "Test")

    # Verify error was logged
    mock_logger.exception.assert_called()


# ===== REST Operations Tests =====
# NOTE: Remaining inline mock_to_thread implementations below are test-specific
# and don't benefit from extraction. They handle unique lambda patterns or
# single-use routing logic that would be harder to understand as generic helpers.


@pytest.mark.asyncio
async def test_get_repository_for_rest_operations(mock_logger, mock_config):
    """Test get_repository_for_rest_operations lazy initialization."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    mock_repo = MagicMock()

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient"),
        patch("webhook_server.libs.graphql.unified_api.Github") as mock_github_class,
        patch("asyncio.to_thread", new=AsyncMock(return_value=mock_repo)),
    ):
        mock_github_instance = MagicMock()
        mock_github_class.return_value = mock_github_instance

        result = await api.get_repository_for_rest_operations("owner", "repo")

        assert api._initialized
        assert result == mock_repo


@pytest.mark.asyncio
async def test_get_pull_request_files(initialized_api, mock_rest_client):
    """Test get_pull_request_files."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_files = [MagicMock(), MagicMock()]
    mock_pr.get_files.return_value = iter(mock_files)

    mock_to_thread = create_mock_to_thread_simple(mock_rest_client, mock_repo, mock_pr)

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_pull_request_files("owner", "repo", 1)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_add_comment_graphql(initialized_api, mock_graphql_client):
    """Test add_comment GraphQL mutation."""
    # Mock get_pull_request_data to return PR data with ID
    mock_graphql_client.execute.side_effect = [
        # First call: get_pull_request_data
        {"repository": {"pullRequest": {"id": "PR_123", "number": 1}}},
        # Second call: add_comment
        {"addComment": {"commentEdge": {"node": {"id": "comment123", "body": "Test comment"}}}},
    ]

    # Test the actual GraphQL approach used in production
    pr_data = await initialized_api.get_pull_request_data("owner", "repo", 1)
    result = await initialized_api.add_comment(pr_data["id"], "Test comment")

    assert result["id"] == "comment123"
    assert mock_graphql_client.execute.call_count == 2


@pytest.mark.asyncio
async def test_get_issue_comments(initialized_api, mock_rest_client):
    """Test get_issue_comments."""
    mock_repo = MagicMock()
    mock_issue = MagicMock()
    mock_comments = [MagicMock(), MagicMock()]
    mock_issue.get_comments.return_value = mock_comments

    async def mock_to_thread(_func, *_args):
        if _func == mock_rest_client.get_repo:
            return mock_repo
        elif _func == mock_repo.get_issue:
            return mock_issue
        elif callable(_func):
            # Handle lambda functions like: lambda: issue.get_comments()
            return _func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_issue_comments("owner", "repo", 1)

    assert result == mock_comments


@pytest.mark.asyncio
async def test_add_assignees_by_login(initialized_api, mock_graphql_client):
    """Test add_assignees_by_login uses GraphQL."""
    # Mock GraphQL responses
    mock_graphql_client.execute.side_effect = [
        # First call: get_pull_request_data
        {"repository": {"pullRequest": {"id": "PR_123", "number": 1}}},
        # Second call: get_user_id for "user1"
        {"user": {"id": "U_kgDOABcD1M"}},
        # Third call: add_assignees mutation
        {},
    ]

    await initialized_api.add_assignees_by_login("owner", "repo", 1, ["user1"])

    # Verify GraphQL was called 3 times (get PR + get user ID + add assignees)
    assert mock_graphql_client.execute.call_count == 3


@pytest.mark.asyncio
async def test_get_issue_comment(initialized_api, mock_rest_client):
    """Test get_issue_comment."""
    mock_repo = MagicMock()
    mock_issue = MagicMock()
    mock_comment = MagicMock()
    mock_issue.get_comment.return_value = mock_comment

    async def mock_to_thread(_func, *_args):
        if _func == mock_rest_client.get_repo:
            return mock_repo
        elif _func == mock_repo.get_issue:
            return mock_issue
        elif _func == mock_issue.get_comment:
            return mock_comment
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_issue_comment("owner", "repo", 1, 123)

    assert result == mock_comment


@pytest.mark.asyncio
async def test_create_reaction(initialized_api):
    """Test create_reaction."""
    mock_comment = MagicMock()

    with patch("asyncio.to_thread", new=AsyncMock()):
        await initialized_api.create_reaction(mock_comment, "+1")


@pytest.mark.asyncio
async def test_get_contributors(initialized_api, mock_graphql_client):
    """Test get_contributors with GraphQL."""
    # Mock GraphQL response for contributors (mentionableUsers)
    mock_graphql_client.execute.return_value = {
        "repository": {
            "mentionableUsers": {
                "nodes": [
                    {
                        "id": "U_1",
                        "login": "user1",
                        "name": "User One",
                        "email": "user1@example.com",
                        "avatarUrl": "https://example.com/avatar1",
                    },
                    {
                        "id": "U_2",
                        "login": "user2",
                        "name": "User Two",
                        "email": "user2@example.com",
                        "avatarUrl": "https://example.com/avatar2",
                    },
                ]
            }
        }
    }

    result = await initialized_api.get_contributors("owner", "repo")

    assert len(result) == 2
    assert result[0]["login"] == "user1"
    assert result[1]["login"] == "user2"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_collaborators(initialized_api, mock_graphql_client):
    """Test get_collaborators with GraphQL."""
    # Mock GraphQL response for collaborators with permissions
    mock_graphql_client.execute.return_value = {
        "repository": {
            "collaborators": {
                "edges": [
                    {
                        "permission": "ADMIN",
                        "node": {
                            "id": "U_1",
                            "login": "admin",
                            "name": "Admin User",
                            "email": "admin@example.com",
                            "avatarUrl": "https://example.com/avatar1",
                        },
                    },
                    {
                        "permission": "WRITE",
                        "node": {
                            "id": "U_2",
                            "login": "writer",
                            "name": "Writer User",
                            "email": "writer@example.com",
                            "avatarUrl": "https://example.com/avatar2",
                        },
                    },
                ]
            }
        }
    }

    result = await initialized_api.get_collaborators("owner", "repo")

    assert len(result) == 2
    assert result[0]["permission"] == "ADMIN"
    assert result[0]["node"]["login"] == "admin"
    assert result[1]["permission"] == "WRITE"
    assert result[1]["node"]["login"] == "writer"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_branch(initialized_api):
    """Test get_branch with GraphQL - returns True if branch exists."""
    # Mock GraphQL response for existing branch (without "data" wrapper - GraphQLClient.execute returns raw result)
    mock_response = {"repository": {"ref": {"id": "REF_123"}}}

    with patch.object(initialized_api.graphql_client, "execute", return_value=mock_response):
        result = await initialized_api.get_branch("owner", "repo", "main")

    assert result is True


@pytest.mark.asyncio
async def test_get_branch_not_found(initialized_api):
    """Test get_branch with GraphQL - returns False if branch doesn't exist."""
    # Mock GraphQL response for non-existent branch (without "data" wrapper - GraphQLClient.execute returns raw result)
    mock_response = {"repository": {"ref": None}}

    with patch.object(initialized_api.graphql_client, "execute", return_value=mock_response):
        result = await initialized_api.get_branch("owner", "repo", "nonexistent")

    assert result is False


@pytest.mark.asyncio
async def test_get_branch_protection(initialized_api, mock_rest_client):
    """Test get_branch_protection."""
    mock_repo = MagicMock()
    mock_branch = MagicMock()
    mock_protection = MagicMock()
    mock_branch.get_protection.return_value = mock_protection

    mock_to_thread = create_mock_to_thread_simple(mock_rest_client, mock_repo, mock_branch)

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_branch_protection("owner", "repo", "main")

    assert result == mock_protection


@pytest.mark.asyncio
async def test_get_issues(initialized_api, mock_graphql_client):
    """Test get_issues with GraphQL."""
    # Mock GraphQL response for issues (defaults to OPEN)
    mock_graphql_client.execute.return_value = {
        "repository": {
            "issues": {
                "nodes": [
                    {
                        "id": "I_1",
                        "number": 1,
                        "title": "Issue 1",
                        "body": "Description 1",
                        "state": "OPEN",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "updatedAt": "2024-01-02T00:00:00Z",
                        "author": {"login": "user1"},
                        "labels": {"nodes": [{"id": "LA_1", "name": "bug"}]},
                    },
                    {
                        "id": "I_2",
                        "number": 2,
                        "title": "Issue 2",
                        "body": "Description 2",
                        "state": "OPEN",
                        "createdAt": "2024-01-03T00:00:00Z",
                        "updatedAt": "2024-01-04T00:00:00Z",
                        "author": {"login": "user2"},
                        "labels": {"nodes": []},
                    },
                ]
            }
        }
    }

    result = await initialized_api.get_issues("owner", "repo")

    assert len(result) == 2
    assert result[0]["number"] == 1
    assert result[0]["title"] == "Issue 1"
    assert result[1]["number"] == 2
    assert result[1]["title"] == "Issue 2"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_edit_issue(initialized_api, mock_graphql_client):
    """Test edit_issue uses GraphQL closeIssue mutation."""
    mock_issue = MagicMock()
    mock_issue.node_id = "I_kgDOABcD1M"

    # Mock GraphQL closeIssue mutation response
    mock_graphql_client.execute.return_value = {"closeIssue": {"issue": {"id": "I_kgDOABcD1M", "state": "CLOSED"}}}

    await initialized_api.edit_issue(mock_issue, "closed")

    # Verify GraphQL mutation was called
    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    mutation = call_args[0][0]
    variables = call_args[0][1]

    assert "closeIssue" in mutation
    assert variables["issueId"] == "I_kgDOABcD1M"


@pytest.mark.asyncio
async def test_edit_issue_reopen(initialized_api, mock_graphql_client):
    """Test edit_issue uses GraphQL reopenIssue mutation for state='open'."""
    mock_issue = MagicMock()
    mock_issue.node_id = "I_kgDOABcD1M"

    # Mock GraphQL reopenIssue mutation response
    mock_graphql_client.execute.return_value = {"reopenIssue": {"issue": {"id": "I_kgDOABcD1M", "state": "OPEN"}}}

    await initialized_api.edit_issue(mock_issue, "open")

    # Verify GraphQL mutation was called
    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    mutation = call_args[0][0]
    variables = call_args[0][1]

    assert "reopenIssue" in mutation
    assert variables["issueId"] == "I_kgDOABcD1M"


@pytest.mark.asyncio
async def test_edit_issue_with_dict(initialized_api, mock_graphql_client):
    """Test edit_issue handles dict format (from GraphQL get_issues)."""
    # Dict format from GraphQL get_issues() response
    issue_dict = {"id": "I_kgDOABcD1M", "number": 42, "title": "Test Issue", "state": "OPEN"}

    # Mock GraphQL closeIssue mutation response
    mock_graphql_client.execute.return_value = {"closeIssue": {"issue": {"id": "I_kgDOABcD1M", "state": "CLOSED"}}}

    await initialized_api.edit_issue(issue_dict, "closed")

    # Verify GraphQL mutation was called with correct issue ID
    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    variables = call_args[0][1]

    assert variables["issueId"] == "I_kgDOABcD1M"


@pytest.mark.asyncio
async def test_get_contents(initialized_api, mock_rest_client):
    """Test get_contents."""
    mock_repo = MagicMock()
    mock_contents = MagicMock()

    mock_to_thread = create_mock_to_thread_simple(mock_rest_client, mock_repo, mock_contents)

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_contents("owner", "repo", "path", "main")

    assert result == mock_contents


@pytest.mark.asyncio
async def test_get_git_tree(initialized_api, mock_graphql_client):
    """Test get_git_tree with GraphQL recursive tree traversal."""
    # Mock GraphQL response for git tree with nested structure
    mock_graphql_client.execute.return_value = {
        "repository": {
            "object": {
                "oid": "tree123",
                "entries": [
                    {
                        "name": "file1.txt",
                        "type": "BLOB",
                        "mode": "100644",
                        "object": {"oid": "blob123", "byteSize": 1024},
                    },
                    {
                        "name": "subdir",
                        "type": "TREE",
                        "mode": "040000",
                        "object": {
                            "oid": "tree456",
                            "entries": [
                                {
                                    "name": "OWNERS",
                                    "type": "BLOB",
                                    "mode": "100644",
                                    "object": {"oid": "blob456", "byteSize": 512},
                                },
                                {
                                    "name": "nested",
                                    "type": "TREE",
                                    "mode": "040000",
                                    "object": {
                                        "oid": "tree789",
                                        "entries": [
                                            {
                                                "name": "deep_file.py",
                                                "type": "BLOB",
                                                "mode": "100644",
                                                "object": {"oid": "blob789", "byteSize": 2048},
                                            }
                                        ],
                                    },
                                },
                            ],
                        },
                    },
                ],
            }
        }
    }

    result = await initialized_api.get_git_tree("owner", "repo", "main")

    assert result["sha"] == "tree123"
    assert len(result["tree"]) == 5  # file1.txt, subdir, subdir/OWNERS, subdir/nested, subdir/nested/deep_file.py

    # Check top-level file
    assert result["tree"][0]["path"] == "file1.txt"
    assert result["tree"][0]["type"] == "blob"
    assert result["tree"][0]["size"] == 1024

    # Check top-level directory
    assert result["tree"][1]["path"] == "subdir"
    assert result["tree"][1]["type"] == "tree"

    # Check nested file with full path
    assert result["tree"][2]["path"] == "subdir/OWNERS"
    assert result["tree"][2]["type"] == "blob"
    assert result["tree"][2]["size"] == 512

    # Check nested directory with full path
    assert result["tree"][3]["path"] == "subdir/nested"
    assert result["tree"][3]["type"] == "tree"

    # Check deeply nested file with full path
    assert result["tree"][4]["path"] == "subdir/nested/deep_file.py"
    assert result["tree"][4]["type"] == "blob"
    assert result["tree"][4]["size"] == 2048

    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_commit_check_runs_with_rest_commit(initialized_api):
    """Test get_commit_check_runs with REST commit object."""
    mock_commit = MagicMock(spec=Commit)
    mock_check_runs = [MagicMock(), MagicMock()]
    mock_commit.get_check_runs.return_value = iter(mock_check_runs)

    async def mock_to_thread(_func):
        # Handle lambda functions like: lambda: list(commit.get_check_runs())
        if callable(_func):
            return _func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_commit_check_runs(mock_commit)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_commit_check_runs_with_commit_wrapper(initialized_api, mock_rest_client):
    """Test get_commit_check_runs with CommitWrapper."""

    # Create minimal object without get_check_runs method
    class MockCommitWrapper:
        sha = "abc123"

    mock_commit_wrapper = MockCommitWrapper()

    mock_repo = MagicMock()
    mock_rest_commit = MagicMock()
    mock_check_runs = [MagicMock()]
    mock_rest_commit.get_check_runs.return_value = iter(mock_check_runs)

    async def mock_to_thread(_func, *_args):
        if _func == mock_rest_client.get_repo:
            return mock_repo
        elif _func == mock_repo.get_commit:
            return mock_rest_commit
        elif callable(_func):
            # Handle lambda functions like: lambda: list(rest_commit.get_check_runs())
            return _func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_commit_check_runs(mock_commit_wrapper, "owner", "repo")

    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_commit_check_runs_fallback(initialized_api):
    """Test get_commit_check_runs raises ValueError for unsupported commit without sha."""

    # Create minimal object without get_check_runs or sha attributes (fail-fast behavior)
    class MockCommitFallback:
        pass

    mock_commit = MockCommitFallback()

    # Should raise ValueError when commit is not a Commit instance and has no sha attribute
    with pytest.raises(ValueError, match="owner and name required"):
        await initialized_api.get_commit_check_runs(mock_commit)


@pytest.mark.asyncio
async def test_create_check_run(initialized_api):
    """Test create_check_run."""
    mock_repo = MagicMock()

    with patch("asyncio.to_thread", new=AsyncMock()):
        await initialized_api.create_check_run(mock_repo, name="test", head_sha="abc")


@pytest.mark.asyncio
async def test_merge_pull_request(initialized_api, mock_rest_client):
    """Test merge_pull_request."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()

    async def mock_to_thread(_func, *_args, **_kwargs):
        if _func == mock_rest_client.get_repo:
            return mock_repo
        elif _func == mock_repo.get_pull:
            return mock_pr
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        await initialized_api.merge_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
async def test_check_pr_merged_status(initialized_api, mock_graphql_client):
    """Test checking PR merge status via GraphQL."""
    # Mock get_pull_request_data to return PR data with merged status
    mock_graphql_client.execute.return_value = {
        "repository": {"pullRequest": {"id": "PR_123", "number": 1, "merged": True, "state": "MERGED"}}
    }

    pr_data = await initialized_api.get_pull_request_data("owner", "repo", 1)
    is_merged = pr_data["merged"]

    assert isinstance(is_merged, bool)
    assert is_merged is True


@pytest.mark.asyncio
async def test_get_pr_with_commits(initialized_api, mock_graphql_client):
    """Test getting PR commits via GraphQL."""
    # Mock get_pull_request_data with include_commits=True
    mock_graphql_client.execute.return_value = {
        "repository": {
            "pullRequest": {
                "id": "PR_123",
                "number": 1,
                "commits": {
                    "nodes": [
                        {"commit": {"oid": "abc123", "message": "First commit"}},
                        {"commit": {"oid": "def456", "message": "Second commit"}},
                    ]
                },
            }
        }
    }

    pr_data = await initialized_api.get_pull_request_data("owner", "repo", 1, include_commits=True)
    commits = pr_data["commits"]["nodes"]

    assert isinstance(commits, list)
    assert len(commits) == 2
    assert commits[0]["commit"]["oid"] == "abc123"
    assert commits[1]["commit"]["oid"] == "def456"


@pytest.mark.asyncio
async def test_get_pulls_from_commit(initialized_api, mock_graphql_client):
    """Test get_pulls_from_commit_sha with GraphQL."""
    # Create mock commit with sha attribute
    mock_commit = MagicMock()
    mock_commit.sha = "abc123"

    # Mock GraphQL response for associatedPullRequests
    mock_graphql_client.execute.return_value = {
        "repository": {
            "object": {
                "associatedPullRequests": {
                    "nodes": [
                        {
                            "id": "PR_1",
                            "number": 1,
                            "title": "PR 1",
                            "state": "OPEN",
                            "baseRefName": "main",
                            "headRefName": "feature",
                            "author": {"login": "user1"},
                            "createdAt": "2024-01-01T00:00:00Z",
                            "updatedAt": "2024-01-02T00:00:00Z",
                            "mergedAt": None,
                            "closedAt": None,
                        },
                        {
                            "id": "PR_2",
                            "number": 2,
                            "title": "PR 2",
                            "state": "MERGED",
                            "baseRefName": "main",
                            "headRefName": "bugfix",
                            "author": {"login": "user2"},
                            "createdAt": "2024-01-03T00:00:00Z",
                            "updatedAt": "2024-01-04T00:00:00Z",
                            "mergedAt": "2024-01-04T00:00:00Z",
                            "closedAt": "2024-01-04T00:00:00Z",
                        },
                    ]
                }
            }
        }
    }

    result = await initialized_api.get_pulls_from_commit_sha("owner", "repo", mock_commit.sha)

    assert len(result) == 2
    assert result[0]["number"] == 1
    assert result[0]["title"] == "PR 1"
    assert result[1]["number"] == 2
    assert result[1]["state"] == "MERGED"
    mock_graphql_client.execute.assert_called_once()


# ===== Additional Tests for Coverage =====


@pytest.mark.asyncio
async def test_get_pull_requests_with_states(initialized_api, mock_graphql_client):
    """Test get_pull_requests with specific states."""
    mock_graphql_client.execute.return_value = {"repository": {"pullRequests": {"nodes": [], "pageInfo": {}}}}

    result = await initialized_api.get_pull_requests(
        "owner", "repo", states=["OPEN", "MERGED"], first=20, after="cursor123"
    )

    assert "nodes" in result
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_issue_with_all_params(initialized_api, mock_graphql_client):
    """Test create_issue with all optional parameters."""
    mock_graphql_client.execute.return_value = {"createIssue": {"issue": {"id": "I_123", "title": "Test"}}}

    result = await initialized_api.create_issue(
        "R_123",
        "Test Issue",
        body="Description",
        assignee_ids=["U_1", "U_2"],
        label_ids=["LA_1"],
    )

    assert result["id"] == "I_123"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_add_comment_empty_pr_id(initialized_api, mock_graphql_client):
    """Test add_comment with empty PR ID."""
    mock_graphql_client.execute.side_effect = GraphQLError("Invalid PR ID")

    with pytest.raises(GraphQLError):
        await initialized_api.add_comment("", "Test comment")


@pytest.mark.asyncio
async def test_get_repository_graphql_error(initialized_api, mock_graphql_client):
    """Test get_repository handles GraphQL errors."""
    mock_graphql_client.execute.side_effect = GraphQLError("Repository not found")

    with pytest.raises(GraphQLError):
        await initialized_api.get_repository("owner", "repo")
