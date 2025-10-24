"""Edge cases and REST operation tests for unified GitHub API."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
async def initialized_api(mock_graphql_client, mock_rest_client, mock_logger):
    """Create initialized UnifiedGitHubAPI."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)
    api.graphql_client = mock_graphql_client
    api.rest_client = mock_rest_client
    api._initialized = True
    return api


# ===== Lazy Initialization Tests =====


@pytest.mark.asyncio
async def test_lazy_init_already_initialized(mock_logger):
    """Test that initialize() returns early if already initialized."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_rate_limit(mock_logger):
    """Test lazy initialization in get_rate_limit."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_viewer(mock_logger):
    """Test lazy initialization in get_viewer."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_repository(mock_logger):
    """Test lazy initialization in get_repository."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_pull_request(mock_logger):
    """Test lazy initialization in get_pull_request."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as mock_gql_class,
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_gql = AsyncMock()
        mock_gql.execute = AsyncMock(return_value={"repository": {"pullRequest": {"id": "PR_123"}}})
        mock_gql_class.return_value = mock_gql

        result = await api.get_pull_request("owner", "repo", 1)

        assert api._initialized
        assert result["id"] == "PR_123"


@pytest.mark.asyncio
async def test_lazy_init_get_pull_requests(mock_logger):
    """Test lazy initialization in get_pull_requests."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_commit(mock_logger):
    """Test lazy initialization in get_commit."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_remove_labels(mock_logger):
    """Test lazy initialization in remove_labels."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_add_assignees(mock_logger):
    """Test lazy initialization in add_assignees."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_create_issue(mock_logger):
    """Test lazy initialization in create_issue."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_request_reviews(mock_logger):
    """Test lazy initialization in request_reviews."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_update_pull_request(mock_logger):
    """Test lazy initialization in update_pull_request."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_enable_automerge(mock_logger):
    """Test lazy initialization in enable_pull_request_automerge."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_user_id(mock_logger):
    """Test lazy initialization in get_user_id."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_label_id(mock_logger):
    """Test lazy initialization in get_label_id."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_create_label(mock_logger):
    """Test lazy initialization in create_label."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_update_label(mock_logger):
    """Test lazy initialization in update_label."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_lazy_init_get_file_contents(mock_logger):
    """Test lazy initialization in get_file_contents."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
    """Test get_file_contents falls back to REST for binary files."""
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": True, "text": None}}}

    mock_contents = MagicMock()
    mock_contents.decoded_content = b"binary content"

    with patch.object(initialized_api, "get_contents", new=AsyncMock(return_value=mock_contents)):
        result = await initialized_api.get_file_contents("owner", "repo", "image.png")

    assert result == "binary content"


@pytest.mark.asyncio
async def test_get_file_contents_null_text_fallback(initialized_api, mock_graphql_client):
    """Test get_file_contents falls back to REST when text is None."""
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": False, "text": None}}}

    mock_contents = MagicMock()
    mock_contents.decoded_content = b"fallback content"

    with patch.object(initialized_api, "get_contents", new=AsyncMock(return_value=mock_contents)):
        result = await initialized_api.get_file_contents("owner", "repo", "file.txt")

    assert result == "fallback content"


@pytest.mark.asyncio
async def test_get_file_contents_non_utf8_binary(initialized_api, mock_graphql_client):
    """Test get_file_contents handles non-UTF-8 binary content gracefully."""
    mock_graphql_client.execute.return_value = {"repository": {"object": {"isBinary": True, "text": None}}}

    # Create binary content with invalid UTF-8 sequences
    # 0xFF and 0xFE are invalid UTF-8 start bytes
    mock_contents = MagicMock()
    mock_contents.decoded_content = b"\xff\xfe\x00\x48\x00\x65\x00\x6c\x00\x6c\x00\x6f"  # UTF-16 LE encoded "Hello"

    with patch.object(initialized_api, "get_contents", new=AsyncMock(return_value=mock_contents)):
        # Should not raise UnicodeDecodeError, should use errors="replace"
        result = await initialized_api.get_file_contents("owner", "repo", "binary.dat")

    # Verify result contains replacement characters (�) for invalid UTF-8
    assert result is not None
    assert isinstance(result, str)
    # UTF-16 bytes decoded as UTF-8 with errors="replace" will have replacement chars
    # \ufffd is the Unicode replacement character used for invalid sequences
    assert "\ufffd" in result, f"Expected replacement character in result, got: {result!r}"


# ===== Error Handling Tests =====


@pytest.mark.asyncio
async def test_add_comment_error_handling(initialized_api, mock_graphql_client, mock_logger):
    """Test add_comment error handling and logging."""
    from webhook_server.libs.graphql.graphql_client import GraphQLError

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


@pytest.mark.asyncio
async def test_get_repository_for_rest_operations(mock_logger):
    """Test get_repository_for_rest_operations lazy initialization."""
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger)

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
async def test_get_pr_for_check_runs(initialized_api, mock_rest_client):
    """Test get_pr_for_check_runs."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_pull:
            return mock_pr
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_pr_for_check_runs("owner", "repo", 1)

    assert result == mock_pr


@pytest.mark.asyncio
async def test_get_pull_request_files(initialized_api, mock_rest_client):
    """Test get_pull_request_files."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_files = [MagicMock(), MagicMock()]
    mock_pr.get_files.return_value = iter(mock_files)

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_pull:
            return mock_pr
        elif callable(func):
            # Handle lambda functions like: lambda: list(pr.get_files())
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_pull_request_files("owner", "repo", 1)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_add_comment_graphql(initialized_api, mock_graphql_client):
    """Test add_comment GraphQL mutation."""
    # Mock get_pull_request to return PR data with ID
    mock_graphql_client.execute.side_effect = [
        # First call: get_pull_request
        {"repository": {"pullRequest": {"id": "PR_123", "number": 1}}},
        # Second call: add_comment
        {"addComment": {"commentEdge": {"node": {"id": "comment123", "body": "Test comment"}}}},
    ]

    # Test the actual GraphQL approach used in production
    pr_data = await initialized_api.get_pull_request("owner", "repo", 1)
    result = await initialized_api.add_comment(pr_data["id"], "Test comment")

    assert result["id"] == "comment123"
    assert mock_graphql_client.execute.call_count == 2


@pytest.mark.asyncio
async def test_get_issue_comments(initialized_api, mock_rest_client):
    """Test get_issue_comments."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_comments = [MagicMock(), MagicMock()]
    mock_pr.get_issue_comments.return_value = mock_comments

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_pull:
            return mock_pr
        elif callable(func):
            # Handle lambda functions like: lambda: pr.get_issue_comments()
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_issue_comments("owner", "repo", 1)

    assert result == mock_comments


@pytest.mark.asyncio
async def test_add_assignees_by_login(initialized_api, mock_rest_client):
    """Test add_assignees_by_login."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()

    async def mock_to_thread(func, *args, **kwargs):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_pull:
            return mock_pr
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        await initialized_api.add_assignees_by_login("owner", "repo", 1, ["user1"])


@pytest.mark.asyncio
async def test_get_issue_comment(initialized_api, mock_rest_client):
    """Test get_issue_comment."""
    mock_repo = MagicMock()
    mock_pr = MagicMock()
    mock_comment = MagicMock()

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_pull:
            return mock_pr
        elif func == mock_pr.get_issue_comment:
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
async def test_get_contributors(initialized_api, mock_rest_client):
    """Test get_contributors."""
    mock_repo = MagicMock()
    mock_contributors = [MagicMock(), MagicMock()]
    mock_repo.get_contributors.return_value = iter(mock_contributors)

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif callable(func):
            # Handle lambda functions like: lambda: list(repo.get_contributors())
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_contributors("owner", "repo")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_collaborators(initialized_api, mock_rest_client):
    """Test get_collaborators."""
    mock_repo = MagicMock()
    mock_collaborators = [MagicMock(), MagicMock()]
    mock_repo.get_collaborators.return_value = iter(mock_collaborators)

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif callable(func):
            # Handle lambda functions like: lambda: list(repo.get_collaborators())
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_collaborators("owner", "repo")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_branch(initialized_api, mock_rest_client):
    """Test get_branch."""
    mock_repo = MagicMock()
    mock_branch = MagicMock()

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_branch:
            return mock_branch
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_branch("owner", "repo", "main")

    assert result == mock_branch


@pytest.mark.asyncio
async def test_get_branch_protection(initialized_api, mock_rest_client):
    """Test get_branch_protection."""
    mock_repo = MagicMock()
    mock_branch = MagicMock()
    mock_protection = MagicMock()

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_branch:
            return mock_branch
        elif func == mock_branch.get_protection:
            return mock_protection
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_branch_protection("owner", "repo", "main")

    assert result == mock_protection


@pytest.mark.asyncio
async def test_get_issues(initialized_api, mock_rest_client):
    """Test get_issues."""
    mock_repo = MagicMock()
    mock_issues = [MagicMock(), MagicMock()]
    mock_repo.get_issues.return_value = iter(mock_issues)

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif callable(func):
            # Handle lambda functions like: lambda: list(repo.get_issues())
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_issues("owner", "repo")

    assert len(result) == 2


@pytest.mark.asyncio
async def test_edit_issue(initialized_api):
    """Test edit_issue."""
    mock_issue = MagicMock()

    with patch("asyncio.to_thread", new=AsyncMock()):
        await initialized_api.edit_issue(mock_issue, "closed")


@pytest.mark.asyncio
async def test_get_contents(initialized_api, mock_rest_client):
    """Test get_contents."""
    mock_repo = MagicMock()
    mock_contents = MagicMock()

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_contents:
            return mock_contents
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_contents("owner", "repo", "path", "main")

    assert result == mock_contents


@pytest.mark.asyncio
async def test_get_git_tree(initialized_api, mock_rest_client):
    """Test get_git_tree."""
    mock_repo = MagicMock()
    mock_tree = MagicMock()

    async def mock_to_thread(func, *args, **kwargs):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_git_tree:
            return mock_tree
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_git_tree("owner", "repo", "main")

    assert result == mock_tree


@pytest.mark.asyncio
async def test_get_commit_check_runs_with_rest_commit(initialized_api):
    """Test get_commit_check_runs with REST commit object."""
    mock_commit = MagicMock()
    mock_check_runs = [MagicMock(), MagicMock()]
    mock_commit.get_check_runs.return_value = iter(mock_check_runs)

    async def mock_to_thread(func):
        # Handle lambda functions like: lambda: list(commit.get_check_runs())
        if callable(func):
            return func()
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

    async def mock_to_thread(func, *args):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_commit:
            return mock_rest_commit
        elif callable(func):
            # Handle lambda functions like: lambda: list(rest_commit.get_check_runs())
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_commit_check_runs(mock_commit_wrapper, "owner", "repo")

    assert len(result) == 1


@pytest.mark.asyncio
async def test_get_commit_check_runs_fallback(initialized_api):
    """Test get_commit_check_runs fallback for unsupported commit."""

    # Create minimal object without get_check_runs or sha attributes
    class MockCommitFallback:
        pass

    mock_commit = MockCommitFallback()

    result = await initialized_api.get_commit_check_runs(mock_commit)

    assert result == []


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

    async def mock_to_thread(func, *args, **kwargs):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif func == mock_repo.get_pull:
            return mock_pr
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        await initialized_api.merge_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
async def test_check_pr_merged_status(initialized_api, mock_graphql_client):
    """Test checking PR merge status via GraphQL."""
    # Mock get_pull_request to return PR data with merged status
    mock_graphql_client.execute.return_value = {
        "repository": {"pullRequest": {"id": "PR_123", "number": 1, "merged": True, "state": "MERGED"}}
    }

    pr_data = await initialized_api.get_pull_request("owner", "repo", 1)
    is_merged = pr_data["merged"]

    assert isinstance(is_merged, bool)
    assert is_merged is True


@pytest.mark.asyncio
async def test_get_pr_with_commits(initialized_api, mock_graphql_client):
    """Test getting PR commits via GraphQL."""
    # Mock get_pull_request with include_commits=True
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

    pr_data = await initialized_api.get_pull_request("owner", "repo", 1, include_commits=True)
    commits = pr_data["commits"]["nodes"]

    assert isinstance(commits, list)
    assert len(commits) == 2
    assert commits[0]["commit"]["oid"] == "abc123"
    assert commits[1]["commit"]["oid"] == "def456"


@pytest.mark.asyncio
async def test_get_pulls_from_commit(initialized_api):
    """Test get_pulls_from_commit."""
    mock_commit = MagicMock()
    mock_pulls = [MagicMock(), MagicMock()]
    mock_commit.get_pulls.return_value = iter(mock_pulls)

    async def mock_to_thread(func):
        # Handle lambda functions like: lambda: list(commit.get_pulls())
        if callable(func):
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_pulls_from_commit(mock_commit)

    assert len(result) == 2


@pytest.mark.asyncio
async def test_get_open_pull_requests(initialized_api, mock_rest_client):
    """Test get_open_pull_requests."""
    mock_repo = MagicMock()
    mock_prs = [MagicMock(), MagicMock()]
    mock_repo.get_pulls.return_value = iter(mock_prs)

    async def mock_to_thread(func, *args, **kwargs):
        if func == mock_rest_client.get_repo:
            return mock_repo
        elif callable(func):
            # Handle lambda functions like: lambda: list(repo.get_pulls())
            return func()
        return None

    with patch("asyncio.to_thread", side_effect=mock_to_thread):
        result = await initialized_api.get_open_pull_requests("owner", "repo")

    assert len(result) == 2


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
