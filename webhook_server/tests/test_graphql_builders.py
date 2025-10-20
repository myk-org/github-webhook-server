"""Tests for GraphQL builders."""

from webhook_server.libs.graphql.graphql_builders import MutationBuilder, QueryBuilder


def test_query_builder_get_rate_limit():
    """Test rate limit query builder."""
    query = QueryBuilder.get_rate_limit()
    assert "rateLimit" in query
    assert "remaining" in query
    assert "resetAt" in query


def test_query_builder_get_viewer():
    """Test viewer query builder."""
    query = QueryBuilder.get_viewer()
    assert "viewer" in query
    assert "login" in query
    assert "email" in query


def test_query_builder_get_repository():
    """Test repository query builder."""
    query = QueryBuilder.get_repository("owner", "repo")
    assert "repository" in query
    assert "owner" in query
    assert "repo" in query
    assert "nameWithOwner" in query


def test_query_builder_get_pull_request_basic():
    """Test basic PR query builder."""
    query = QueryBuilder.get_pull_request("owner", "repo", 123)
    assert "repository" in query
    assert "pullRequest" in query
    assert "number: 123" in query
    assert "PullRequestFields" in query


def test_query_builder_get_pull_request_with_commits():
    """Test PR query with commits."""
    query = QueryBuilder.get_pull_request("owner", "repo", 123, include_commits=True)
    assert "commits" in query
    assert "CommitFields" in query


def test_query_builder_get_pull_request_with_labels():
    """Test PR query with labels."""
    query = QueryBuilder.get_pull_request("owner", "repo", 123, include_labels=True)
    assert "labels" in query
    assert "LabelFields" in query


def test_query_builder_get_pull_request_with_reviews():
    """Test PR query with reviews."""
    query = QueryBuilder.get_pull_request("owner", "repo", 123, include_reviews=True)
    assert "reviews" in query
    assert "ReviewFields" in query


def test_query_builder_get_pull_requests():
    """Test list PRs query builder."""
    query = QueryBuilder.get_pull_requests("owner", "repo", states=["OPEN"], first=50)
    assert "pullRequests" in query
    assert "states: [OPEN]" in query
    assert "first: 50" in query
    assert "pageInfo" in query
    assert "hasNextPage" in query


def test_query_builder_get_pull_requests_with_cursor():
    """Test PRs query with pagination cursor."""
    query = QueryBuilder.get_pull_requests("owner", "repo", after="cursor123")
    assert "after:" in query
    assert "cursor123" in query


def test_query_builder_get_commit():
    """Test commit query builder."""
    query = QueryBuilder.get_commit("owner", "repo", "abc123")
    assert "repository" in query
    assert "object" in query
    assert 'oid: "abc123"' in query
    assert "CommitFields" in query


def test_query_builder_get_file_contents():
    """Test file contents query builder."""
    query = QueryBuilder.get_file_contents("owner", "repo", "main:OWNERS")
    assert "repository" in query
    assert "object" in query
    assert 'expression: "main:OWNERS"' in query
    assert "Blob" in query


def test_query_builder_get_issues():
    """Test issues query builder."""
    query = QueryBuilder.get_issues("owner", "repo", states=["OPEN", "CLOSED"], first=20)
    assert "issues" in query
    assert "states: [OPEN, CLOSED]" in query
    assert "first: 20" in query
    assert "pageInfo" in query


def test_mutation_builder_add_comment():
    """Test add comment mutation builder."""
    mutation, variables = MutationBuilder.add_comment("subject123", "Test comment")
    assert "addComment" in mutation
    assert "subjectId" in mutation
    assert "body" in mutation
    assert variables["subjectId"] == "subject123"
    assert variables["body"] == "Test comment"


def test_mutation_builder_add_labels():
    """Test add labels mutation builder."""
    mutation, variables = MutationBuilder.add_labels("labelable123", ["label1", "label2"])
    assert "addLabelsToLabelable" in mutation
    assert "labelableId" in mutation
    assert "labelIds" in mutation
    assert variables["labelableId"] == "labelable123"
    assert variables["labelIds"] == ["label1", "label2"]


def test_mutation_builder_remove_labels():
    """Test remove labels mutation builder."""
    mutation, variables = MutationBuilder.remove_labels("labelable123", ["label1"])
    assert "removeLabelsFromLabelable" in mutation
    assert variables["labelableId"] == "labelable123"
    assert variables["labelIds"] == ["label1"]


def test_mutation_builder_add_assignees():
    """Test add assignees mutation builder."""
    mutation, variables = MutationBuilder.add_assignees("assignable123", ["user1", "user2"])
    assert "addAssigneesToAssignable" in mutation
    assert variables["assignableId"] == "assignable123"
    assert variables["assigneeIds"] == ["user1", "user2"]


def test_mutation_builder_create_issue():
    """Test create issue mutation builder."""
    mutation, variables = MutationBuilder.create_issue(
        "repo123",
        "Test Issue",
        body="Test body",
        assignee_ids=["user1"],
        label_ids=["label1"],
    )
    assert "createIssue" in mutation
    assert variables["repositoryId"] == "repo123"
    assert variables["title"] == "Test Issue"
    assert variables["body"] == "Test body"
    assert variables["assigneeIds"] == ["user1"]
    assert variables["labelIds"] == ["label1"]
