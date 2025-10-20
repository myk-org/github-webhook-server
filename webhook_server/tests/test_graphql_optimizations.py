"""Tests for GraphQL optimizations module."""

from webhook_server.libs.graphql.graphql_optimizations import (
    get_multiple_prs_batch_query,
    get_pr_can_be_merged_batch_query,
    get_pr_full_context_query,
)


def test_get_pr_can_be_merged_batch_query():
    """Test optimized can-be-merged batch query."""
    query = get_pr_can_be_merged_batch_query("owner", "repo", 123)

    # Should include all required fields for merge check
    assert "pullRequest" in query
    assert "number: 123" in query
    assert "mergeable" in query
    assert "labels" in query
    assert "reviews" in query
    assert "commits" in query
    assert "statusCheckRollup" in query
    assert "baseRef" in query
    assert "headRef" in query


def test_get_pr_full_context_query():
    """Test full PR context query."""
    query = get_pr_full_context_query("owner", "repo", 456)

    # Should include comprehensive PR data
    assert "pullRequest" in query
    assert "number: 456" in query
    assert "commits" in query
    assert "labels" in query
    assert "reviews" in query
    assert "comments" in query
    assert "assignees" in query
    assert "author" in query


def test_get_multiple_prs_batch_query():
    """Test batch query for multiple PRs."""
    pr_numbers = [100, 200, 300]
    query = get_multiple_prs_batch_query("owner", "repo", pr_numbers)

    # Should create aliased queries for each PR
    assert "pr_100" in query
    assert "pr_200" in query
    assert "pr_300" in query
    assert "number: 100" in query
    assert "number: 200" in query
    assert "number: 300" in query
    assert "repository" in query


def test_get_multiple_prs_empty_list():
    """Test batch query with empty PR list."""
    query = get_multiple_prs_batch_query("owner", "repo", [])

    # Should still have repository query structure
    assert "repository" in query
    assert "owner" in query
    assert "repo" in query
