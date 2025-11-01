"""Integration tests for UnifiedGitHubAPI multi-step workflows."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI

# Test token constant
TEST_GITHUB_TOKEN = "test_token_12345"  # pragma: allowlist secret  # noqa: S105


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    return Mock()


@pytest.fixture
def mock_config():
    """Create a mock config."""
    config = Mock()
    config.get_value = Mock(return_value=9)  # For tree-max-depth
    return config


@pytest.mark.asyncio
async def test_complete_pr_workflow_uses_graphql(mock_logger, mock_config):
    """
    Test complete PR workflow uses GraphQL for all operations, not REST.

    Workflow: Fetch PR → Add comment → Add labels → Request review
    Verify: All operations use GraphQL client, no REST fallback
    """
    # Create UnifiedGitHubAPI instance
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    # Mock GraphQL client to track calls
    mock_graphql_client = AsyncMock()

    # Mock GraphQL responses for each operation
    # 1. get_pull_request response
    mock_graphql_client.execute.side_effect = [
        # First call: get_pull_request
        {
            "repository": {
                "pullRequest": {
                    "id": "PR_kwDOABCD123",
                    "number": 123,
                    "title": "Test PR",
                    "body": "Test body",
                    "state": "OPEN",
                    "isDraft": False,
                    "author": {"login": "testuser"},
                    "baseRef": {"name": "main", "target": {"oid": "abc123"}},
                    "headRef": {"name": "feature", "target": {"oid": "def456"}},
                    "labels": {"nodes": []},
                    "commits": {"nodes": []},
                }
            }
        },
        # Second call: add_comment
        {
            "addComment": {
                "commentEdge": {
                    "node": {
                        "id": "IC_kwDOABCD456",
                        "body": "Test comment",
                        "createdAt": "2023-01-01T00:00:00Z",
                    }
                }
            }
        },
        # Third call: add_labels
        {"addLabelsToLabelable": {"labelable": {"id": "PR_kwDOABCD123"}}},
        # Fourth call: request_reviews
        {"requestReviews": {"pullRequest": {"id": "PR_kwDOABCD123"}}},
    ]

    # Mock REST client to verify it's NOT called
    mock_rest_client = Mock()

    # Initialize and inject mocks
    await api.initialize()
    api.graphql_client = mock_graphql_client
    api.rest_client = mock_rest_client

    # Execute complete workflow
    # Step 1: Fetch PR
    pr_data = await api.get_pull_request_data(
        owner="test-owner",
        name="test-repo",
        number=123,
        include_commits=True,
        include_labels=True,
    )

    # Step 2: Add comment
    comment = await api.add_comment(pr_data["id"], "Test comment")

    # Step 3: Add labels
    await api.add_labels(pr_data["id"], ["L_kwDOABCD789"])

    # Step 4: Request reviews
    await api.request_reviews(pr_data["id"], ["U_kwDOABCD999"])

    # Verify all 4 operations used GraphQL
    assert mock_graphql_client.execute.call_count == 4, "All 4 operations should use GraphQL"

    # Verify REST client was NEVER used (no get_repo, get_pull, etc.)
    assert mock_rest_client.get_repo.call_count == 0, "REST client should not be used for these operations"
    mock_rest_client.get_repo.assert_not_called()

    # Verify results
    assert pr_data["number"] == 123
    assert pr_data["title"] == "Test PR"
    assert comment["body"] == "Test comment"

    # Cleanup
    await api.close()


@pytest.mark.asyncio
async def test_pr_workflow_with_error_recovery(mock_logger, mock_config):
    """
    Test PR workflow with GraphQL error and recovery.

    Verifies that errors in multi-step workflows are properly propagated
    and don't leave the API in an inconsistent state.
    """
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    # Mock GraphQL client that fails on second operation
    mock_graphql_client = AsyncMock()
    mock_graphql_client.execute.side_effect = [
        # First call: get_pull_request succeeds
        {
            "repository": {
                "pullRequest": {
                    "id": "PR_kwDOABCD123",
                    "number": 123,
                    "title": "Test PR",
                    "body": "Test body",
                    "state": "OPEN",
                    "author": {"login": "testuser"},
                    "baseRef": {"name": "main", "target": {"oid": "abc123"}},
                    "headRef": {"name": "feature", "target": {"oid": "def456"}},
                }
            }
        },
        # Second call: add_comment fails
        Exception("GraphQL mutation failed"),
    ]

    # Initialize and inject mocks
    await api.initialize()
    api.graphql_client = mock_graphql_client

    # Execute workflow - first operation succeeds
    pr_data = await api.get_pull_request_data(owner="test-owner", name="test-repo", number=123)
    assert pr_data["number"] == 123

    # Second operation should fail and propagate exception
    with pytest.raises(Exception, match="GraphQL mutation failed"):
        await api.add_comment(pr_data["id"], "This will fail")

    # Verify GraphQL client was called twice (success + failure)
    assert mock_graphql_client.execute.call_count == 2

    # Verify API can still be used after error (not in broken state)
    mock_graphql_client.execute.side_effect = [
        {
            "repository": {
                "pullRequest": {
                    "id": "PR_kwDOABCD124",
                    "number": 124,
                    "title": "Recovery PR",
                    "state": "OPEN",
                }
            }
        }
    ]

    # Can still make calls after error
    new_pr = await api.get_pull_request_data(owner="test-owner", name="test-repo", number=124)
    assert new_pr["number"] == 124

    # Cleanup
    await api.close()


@pytest.mark.asyncio
async def test_batch_operations_use_graphql(mock_logger, mock_config):
    """
    Test that batch operations efficiently use GraphQL, not multiple REST calls.

    Verifies that batch fetching uses GraphQL's ability to fetch multiple
    resources in a single query rather than N REST API calls.
    """
    api = UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config)

    # Mock GraphQL client
    mock_graphql_client = AsyncMock()
    mock_graphql_client.execute_batch = AsyncMock(
        return_value=[
            {
                "repository": {
                    "pullRequest": {
                        "id": f"PR_{i}",
                        "number": i,
                        "title": f"PR {i}",
                        "state": "OPEN",
                    }
                }
            }
            for i in range(1, 6)
        ]
    )

    # Mock REST client to verify it's not used
    mock_rest_client = Mock()

    # Initialize and inject mocks
    await api.initialize()
    api.graphql_client = mock_graphql_client
    api.rest_client = mock_rest_client

    # Batch fetch 5 PRs
    queries = [
        (
            """
            query($owner: String!, $name: String!, $number: Int!) {
                repository(owner: $owner, name: $name) {
                    pullRequest(number: $number) {
                        id number title state
                    }
                }
            }
            """,
            {"owner": "test-owner", "name": "test-repo", "number": i},
        )
        for i in range(1, 6)
    ]

    results = await api.execute_batch(queries)

    # Verify single batch call was made instead of 5 individual calls
    assert mock_graphql_client.execute_batch.call_count == 1
    assert len(results) == 5

    # Verify REST client was NEVER used
    mock_rest_client.get_repo.assert_not_called()

    # Cleanup
    await api.close()


@pytest.mark.asyncio
async def test_context_manager_workflow(mock_logger, mock_config):
    """
    Test complete workflow using async context manager.

    Verifies that async context manager properly initializes and cleans up
    resources during multi-step workflows.
    """
    # Mock GraphQL responses
    mock_graphql_responses = [
        # get_pull_request
        {
            "repository": {
                "pullRequest": {
                    "id": "PR_kwDOABCD123",
                    "number": 456,
                    "title": "Context Manager PR",
                    "state": "OPEN",
                    "author": {"login": "testuser"},
                }
            }
        },
        # add_comment
        {
            "addComment": {
                "commentEdge": {
                    "node": {
                        "id": "IC_kwDOABCD789",
                        "body": "Auto-generated comment",
                    }
                }
            }
        },
    ]

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient") as MockGraphQLClient,
        patch("webhook_server.libs.graphql.unified_api.Github") as MockGithub,
    ):
        # Setup mocks
        mock_gql_instance = AsyncMock()
        mock_gql_instance.execute = AsyncMock(side_effect=mock_graphql_responses)
        mock_gql_instance.close = AsyncMock()
        MockGraphQLClient.return_value = mock_gql_instance

        mock_rest_instance = Mock()
        mock_rest_instance.close = Mock()
        MockGithub.return_value = mock_rest_instance

        # Use context manager for workflow
        async with UnifiedGitHubAPI(token=TEST_GITHUB_TOKEN, logger=mock_logger, config=mock_config) as api:
            # Fetch PR
            pr = await api.get_pull_request_data("test-owner", "test-repo", 456)
            assert pr["number"] == 456

            # Add comment
            comment = await api.add_comment(pr["id"], "Auto-generated comment")
            assert comment["body"] == "Auto-generated comment"

        # Verify cleanup was called
        mock_gql_instance.close.assert_called_once()
        mock_rest_instance.close.assert_called_once()
