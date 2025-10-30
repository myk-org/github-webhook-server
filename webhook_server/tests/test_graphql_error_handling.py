"""Tests for GraphQL error handling in unified_api.py."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI


class TestGraphQLErrorHandling:
    """Test error handling in GraphQL operations."""

    @pytest.fixture
    def mock_config(self):
        """Create mock config."""
        config = Mock()
        config.get_value = Mock(return_value=9)  # For tree-max-depth
        return config

    @pytest.fixture
    def unified_api(self, mock_config) -> UnifiedGitHubAPI:
        """Create UnifiedGitHubAPI instance for testing."""
        logger = Mock()
        api = UnifiedGitHubAPI(token="test_token", logger=logger, config=mock_config)  # pragma: allowlist secret
        api.graphql_client = AsyncMock()
        return api

    @pytest.mark.asyncio
    async def test_get_branch_returns_false_for_not_found(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that get_branch returns False for NOT_FOUND errors."""
        # Simulate GraphQL NOT_FOUND error
        error_mock = AsyncMock(
            side_effect=GraphQLError("Could not resolve to a Ref with the name 'refs/heads/nonexistent'")
        )

        with patch.object(unified_api.graphql_client, "execute", error_mock):
            result = await unified_api.get_branch("owner", "repo", "nonexistent")

        assert result is False, "Should return False for NOT_FOUND errors"

    @pytest.mark.asyncio
    async def test_get_branch_propagates_auth_errors(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that get_branch raises GraphQLError for auth failures."""
        # Simulate GraphQL auth error
        error_mock = AsyncMock(side_effect=GraphQLError("Bad credentials"))

        with patch.object(unified_api.graphql_client, "execute", error_mock):
            with pytest.raises(GraphQLError, match="Bad credentials"):
                await unified_api.get_branch("owner", "repo", "main")

    @pytest.mark.asyncio
    async def test_get_branch_propagates_rate_limit_errors(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that get_branch raises GraphQLError for rate limit errors."""
        # Simulate GraphQL rate limit error
        error_mock = AsyncMock(side_effect=GraphQLError("API rate limit exceeded"))

        with patch.object(unified_api.graphql_client, "execute", error_mock):
            with pytest.raises(GraphQLError, match="rate limit"):
                await unified_api.get_branch("owner", "repo", "main")

    @pytest.mark.asyncio
    async def test_get_branch_returns_true_when_branch_exists(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that get_branch returns True when branch exists."""
        # Simulate successful GraphQL response
        mock_response = {"repository": {"ref": {"id": "test-ref-id"}}}

        with patch.object(unified_api.graphql_client, "execute", return_value=mock_response):
            result = await unified_api.get_branch("owner", "repo", "main")

        assert result is True, "Should return True when branch exists"

    @pytest.mark.asyncio
    async def test_get_branch_returns_false_when_branch_does_not_exist(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that get_branch returns False when ref is None."""
        # Simulate GraphQL response with null ref (branch doesn't exist)
        mock_response = {"repository": {"ref": None}}

        with patch.object(unified_api.graphql_client, "execute", return_value=mock_response):
            result = await unified_api.get_branch("owner", "repo", "nonexistent")

        assert result is False, "Should return False when ref is None"

    @pytest.mark.asyncio
    async def test_get_branch_case_insensitive_not_found_check(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that NOT_FOUND check is case-insensitive."""
        # Test with different case variations
        for error_msg in ["NOT FOUND", "Not Found", "not found", "Could Not Resolve"]:
            error_mock = AsyncMock(side_effect=GraphQLError(error_msg))
            with patch.object(unified_api.graphql_client, "execute", error_mock):
                result = await unified_api.get_branch("owner", "repo", "test")
            assert result is False, f"Should return False for error: {error_msg}"

    @pytest.mark.asyncio
    async def test_get_branch_propagates_network_errors(self, unified_api: UnifiedGitHubAPI) -> None:
        """Test that get_branch raises GraphQLError for network errors."""
        # Simulate GraphQL network error
        error_mock = AsyncMock(side_effect=GraphQLError("Connection timeout"))

        with patch.object(unified_api.graphql_client, "execute", error_mock):
            with pytest.raises(GraphQLError, match="timeout"):
                await unified_api.get_branch("owner", "repo", "main")
