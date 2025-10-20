"""Comprehensive tests for unified API GraphQL mutations."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from webhook_server.libs.graphql.unified_api import UnifiedGitHubAPI


@pytest.fixture
def mock_graphql_client():
    """Create a mock GraphQL client."""
    client = AsyncMock()
    client.execute = AsyncMock()
    return client


@pytest.fixture
def mock_rest_client():
    """Create a mock REST client."""
    return MagicMock()


@pytest.fixture
async def initialized_api(mock_graphql_client, mock_rest_client):
    """Create initialized UnifiedGitHubAPI."""
    api = UnifiedGitHubAPI(token="test_token", logger=MagicMock())
    api.graphql_client = mock_graphql_client
    api.rest_client = mock_rest_client
    api._initialized = True
    return api


@pytest.mark.asyncio
async def test_add_comment_mutation(initialized_api, mock_graphql_client):
    """Test add_comment calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"addComment": {"commentEdge": {"node": {"id": "C_123"}}}}

    result = await initialized_api.add_comment("PR_123", "Test comment")

    assert result["id"] == "C_123"
    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    assert "mutation" in call_args[0][0]
    assert "addComment" in call_args[0][0]


@pytest.mark.asyncio
async def test_add_labels_mutation(initialized_api, mock_graphql_client):
    """Test add_labels calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"addLabelsToLabelable": {"labelable": {"id": "PR_123"}}}

    await initialized_api.add_labels("PR_123", ["bug", "enhancement"])

    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    assert "mutation" in call_args[0][0]
    assert "addLabelsToLabelable" in call_args[0][0]


@pytest.mark.asyncio
async def test_remove_labels_mutation(initialized_api, mock_graphql_client):
    """Test remove_labels calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"removeLabelsFromLabelable": {"labelable": {"id": "PR_123"}}}

    await initialized_api.remove_labels("PR_123", ["wip"])

    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_user_id_query(initialized_api, mock_graphql_client):
    """Test get_user_id fetches user node ID."""
    mock_graphql_client.execute.return_value = {"user": {"id": "U_kgDOABCDEF"}}

    result = await initialized_api.get_user_id("testuser")

    assert result == "U_kgDOABCDEF"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_label_id_query(initialized_api, mock_graphql_client):
    """Test get_label_id fetches label node ID."""
    mock_graphql_client.execute.return_value = {"repository": {"label": {"id": "LA_kgDOABCDEF"}}}

    result = await initialized_api.get_label_id("owner", "repo", "bug")

    assert result == "LA_kgDOABCDEF"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_label_id_not_found(initialized_api, mock_graphql_client):
    """Test get_label_id returns None when label doesn't exist."""
    mock_graphql_client.execute.return_value = {"repository": {"label": None}}

    result = await initialized_api.get_label_id("owner", "repo", "nonexistent")

    assert result is None


@pytest.mark.asyncio
async def test_create_label_mutation(initialized_api, mock_graphql_client):
    """Test create_label calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"createLabel": {"label": {"id": "LA_123", "name": "newlabel"}}}

    result = await initialized_api.create_label("R_123", "newlabel", "ff0000")

    assert result["id"] == "LA_123"
    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    assert "mutation" in call_args[0][0]
    assert "createLabel" in call_args[0][0]


@pytest.mark.asyncio
async def test_update_label_mutation(initialized_api, mock_graphql_client):
    """Test update_label calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"updateLabel": {"label": {"id": "LA_123", "color": "00ff00"}}}

    result = await initialized_api.update_label("LA_123", "00ff00")

    assert result["color"] == "00ff00"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_request_reviews_mutation(initialized_api, mock_graphql_client):
    """Test request_reviews calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"requestReviews": {"pullRequest": {"id": "PR_123"}}}

    # Mock get_user_id
    with patch.object(initialized_api, "get_user_id", return_value="U_123"):
        await initialized_api.request_reviews("PR_123", ["reviewer1"])

    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_pull_request_title(initialized_api, mock_graphql_client):
    """Test update_pull_request with title only."""
    mock_graphql_client.execute.return_value = {
        "updatePullRequest": {"pullRequest": {"id": "PR_123", "title": "New title"}}
    }

    result = await initialized_api.update_pull_request("PR_123", title="New title")

    assert result["title"] == "New title"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_pull_request_body(initialized_api, mock_graphql_client):
    """Test update_pull_request with body only."""
    mock_graphql_client.execute.return_value = {
        "updatePullRequest": {"pullRequest": {"id": "PR_123", "body": "New body"}}
    }

    result = await initialized_api.update_pull_request("PR_123", body="New body")

    assert result["body"] == "New body"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_pull_request_both(initialized_api, mock_graphql_client):
    """Test update_pull_request with both title and body."""
    mock_graphql_client.execute.return_value = {
        "updatePullRequest": {"pullRequest": {"id": "PR_123", "title": "New title", "body": "New body"}}
    }

    result = await initialized_api.update_pull_request("PR_123", title="New title", body="New body")

    assert result["title"] == "New title"
    assert result["body"] == "New body"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_enable_pull_request_automerge(initialized_api, mock_graphql_client):
    """Test enable_pull_request_automerge calls GraphQL mutation."""
    mock_graphql_client.execute.return_value = {"enablePullRequestAutoMerge": {"pullRequest": {"id": "PR_123"}}}

    await initialized_api.enable_pull_request_automerge("PR_123", "SQUASH")

    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    assert "SQUASH" in str(call_args)


@pytest.mark.asyncio
async def test_get_repository_query(initialized_api, mock_graphql_client):
    """Test get_repository fetches repo data."""
    mock_graphql_client.execute.return_value = {
        "repository": {"id": "R_123", "name": "test-repo", "owner": {"login": "owner"}}
    }

    result = await initialized_api.get_repository("owner", "repo")

    assert result["id"] == "R_123"
    assert result["name"] == "test-repo"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_pull_request_basic(initialized_api, mock_graphql_client):
    """Test get_pull_request fetches basic PR data."""
    mock_graphql_client.execute.return_value = {
        "repository": {
            "pullRequest": {
                "id": "PR_123",
                "number": 1,
                "title": "Test PR",
                "state": "OPEN",
            }
        }
    }

    result = await initialized_api.get_pull_request("owner", "repo", 1)

    assert result["id"] == "PR_123"
    assert result["number"] == 1
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_pull_request_with_commits(initialized_api, mock_graphql_client):
    """Test get_pull_request includes commits when requested."""
    mock_graphql_client.execute.return_value = {
        "repository": {
            "pullRequest": {
                "id": "PR_123",
                "number": 1,
                "commits": {"nodes": [{"commit": {"oid": "abc123"}}]},
            }
        }
    }

    result = await initialized_api.get_pull_request("owner", "repo", 1, include_commits=True)

    assert "commits" in result
    assert len(result["commits"]["nodes"]) == 1
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_pull_request_with_labels(initialized_api, mock_graphql_client):
    """Test get_pull_request includes labels when requested."""
    mock_graphql_client.execute.return_value = {
        "repository": {
            "pullRequest": {
                "id": "PR_123",
                "number": 1,
                "labels": {"nodes": [{"name": "bug"}]},
            }
        }
    }

    result = await initialized_api.get_pull_request("owner", "repo", 1, include_labels=True)

    assert "labels" in result
    assert len(result["labels"]["nodes"]) == 1
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_pull_request_with_reviews(initialized_api, mock_graphql_client):
    """Test get_pull_request includes reviews when requested."""
    mock_graphql_client.execute.return_value = {
        "repository": {
            "pullRequest": {
                "id": "PR_123",
                "number": 1,
                "reviews": {"nodes": [{"state": "APPROVED"}]},
            }
        }
    }

    result = await initialized_api.get_pull_request("owner", "repo", 1, include_reviews=True)

    assert "reviews" in result
    assert len(result["reviews"]["nodes"]) == 1
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_lazy_initialization_in_add_comment(mock_graphql_client):
    """Test that methods auto-initialize if not initialized."""
    api = UnifiedGitHubAPI(token="test_token", logger=MagicMock())

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient", return_value=mock_graphql_client),
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql_client.execute.return_value = {"addComment": {"commentEdge": {"node": {"id": "C_123"}}}}

        result = await api.add_comment("PR_123", "Test")

        assert api._initialized
        assert result["id"] == "C_123"


@pytest.mark.asyncio
async def test_lazy_initialization_in_add_labels(mock_graphql_client):
    """Test lazy initialization in add_labels."""
    api = UnifiedGitHubAPI(token="test_token", logger=MagicMock())

    with (
        patch("webhook_server.libs.graphql.unified_api.GraphQLClient", return_value=mock_graphql_client),
        patch("webhook_server.libs.graphql.unified_api.Github"),
    ):
        mock_graphql_client.execute.return_value = {"addLabelsToLabelable": {"labelable": {"id": "PR_123"}}}

        await api.add_labels("PR_123", ["bug"])

        assert api._initialized
        mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_get_label_id_with_owner_repo(initialized_api, mock_graphql_client):
    """Test get_label_id with different owner/repo."""
    mock_graphql_client.execute.return_value = {"repository": {"label": {"id": "LA_xyz"}}}

    result = await initialized_api.get_label_id("different-owner", "different-repo", "feature")

    assert result == "LA_xyz"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_label_different_color(initialized_api, mock_graphql_client):
    """Test create_label with different color."""
    mock_graphql_client.execute.return_value = {
        "createLabel": {"label": {"id": "LA_new", "name": "enhancement", "color": "0000ff"}}
    }

    result = await initialized_api.create_label("R_456", "enhancement", "0000ff")

    assert result["id"] == "LA_new"
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_update_pull_request_none_values(initialized_api, mock_graphql_client):
    """Test update_pull_request with None values."""
    mock_graphql_client.execute.return_value = {"updatePullRequest": {"pullRequest": {"id": "PR_123"}}}

    result = await initialized_api.update_pull_request("PR_123")

    assert result is not None
    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_request_reviews_multiple_reviewers(initialized_api, mock_graphql_client):
    """Test request_reviews with multiple reviewers."""
    mock_graphql_client.execute.return_value = {"requestReviews": {"pullRequest": {"id": "PR_123"}}}

    with patch.object(initialized_api, "get_user_id", side_effect=["U_1", "U_2", "U_3"]):
        await initialized_api.request_reviews("PR_123", ["user1", "user2", "user3"])

    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_enable_automerge_merge_method(initialized_api, mock_graphql_client):
    """Test enable_automerge with MERGE method."""
    mock_graphql_client.execute.return_value = {"enablePullRequestAutoMerge": {"pullRequest": {"id": "PR_123"}}}

    await initialized_api.enable_pull_request_automerge("PR_123", "MERGE")

    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    assert "MERGE" in str(call_args)


@pytest.mark.asyncio
async def test_enable_automerge_rebase_method(initialized_api, mock_graphql_client):
    """Test enable_automerge with REBASE method."""
    mock_graphql_client.execute.return_value = {"enablePullRequestAutoMerge": {"pullRequest": {"id": "PR_123"}}}

    await initialized_api.enable_pull_request_automerge("PR_123", "REBASE")

    mock_graphql_client.execute.assert_called_once()
    call_args = mock_graphql_client.execute.call_args
    assert "REBASE" in str(call_args)


@pytest.mark.asyncio
async def test_remove_labels_multiple(initialized_api, mock_graphql_client):
    """Test remove_labels with multiple label IDs."""
    mock_graphql_client.execute.return_value = {"removeLabelsFromLabelable": {"labelable": {"id": "PR_123"}}}

    await initialized_api.remove_labels("PR_123", ["LA_1", "LA_2", "LA_3"])

    mock_graphql_client.execute.assert_called_once()


@pytest.mark.asyncio
async def test_add_labels_multiple(initialized_api, mock_graphql_client):
    """Test add_labels with multiple label IDs."""
    mock_graphql_client.execute.return_value = {"addLabelsToLabelable": {"labelable": {"id": "PR_123"}}}

    await initialized_api.add_labels("PR_123", ["LA_1", "LA_2", "LA_3", "LA_4"])

    mock_graphql_client.execute.assert_called_once()
