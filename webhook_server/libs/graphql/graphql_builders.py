"""GraphQL query and mutation builders for GitHub API."""

from __future__ import annotations

from typing import Any


# Common GraphQL fragments for reuse
PULL_REQUEST_FRAGMENT = """
fragment PullRequestFields on PullRequest {
    id
    number
    title
    body
    state
    createdAt
    updatedAt
    closedAt
    mergedAt
    merged
    mergeable
    permalink
    additions
    deletions
    author {
        login
    }
    baseRef {
        name
        target {
            oid
        }
    }
    headRef {
        name
        target {
            oid
        }
    }
}
"""

COMMIT_FRAGMENT = """
fragment CommitFields on Commit {
    oid
    message
    committedDate
    author {
        name
        email
        user {
            login
        }
    }
}
"""

LABEL_FRAGMENT = """
fragment LabelFields on Label {
    id
    name
    color
    description
}
"""

REVIEW_FRAGMENT = """
fragment ReviewFields on PullRequestReview {
    id
    state
    createdAt
    author {
        login
    }
    body
}
"""


class QueryBuilder:
    """Builder for GraphQL queries."""

    @staticmethod
    def get_rate_limit() -> str:
        """Get current rate limit information."""
        return """
            query {
                rateLimit {
                    limit
                    remaining
                    resetAt
                    cost
                }
            }
        """

    @staticmethod
    def get_viewer() -> str:
        """Get authenticated user information."""
        return """
            query {
                viewer {
                    login
                    name
                    id
                    avatarUrl
                    email
                }
            }
        """

    @staticmethod
    def get_repository(owner: str, name: str) -> str:
        """
        Get repository information.

        Args:
            owner: Repository owner
            name: Repository name

        Returns:
            GraphQL query string
        """
        return f"""
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    id
                    name
                    nameWithOwner
                    description
                    url
                    isPrivate
                    isFork
                    defaultBranchRef {{
                        name
                    }}
                }}
            }}
        """

    @staticmethod
    def get_pull_request(
        owner: str,
        name: str,
        number: int,
        include_commits: bool = False,
        include_labels: bool = False,
        include_reviews: bool = False,
    ) -> str:
        """
        Get pull request information.

        Args:
            owner: Repository owner
            name: Repository name
            number: Pull request number
            include_commits: Include commit history
            include_labels: Include labels
            include_reviews: Include reviews

        Returns:
            GraphQL query string
        """
        commits_field = (
            """
            commits(first: 100) {
                totalCount
                nodes {
                    commit {
                        ...CommitFields
                    }
                }
            }
        """
            if include_commits
            else ""
        )

        labels_field = (
            """
            labels(first: 100) {
                nodes {
                    ...LabelFields
                }
            }
        """
            if include_labels
            else ""
        )

        reviews_field = (
            """
            reviews(first: 100) {
                nodes {
                    ...ReviewFields
                }
            }
        """
            if include_reviews
            else ""
        )

        fragments = []
        if include_commits:
            fragments.append(COMMIT_FRAGMENT)
        if include_labels:
            fragments.append(LABEL_FRAGMENT)
        if include_reviews:
            fragments.append(REVIEW_FRAGMENT)

        fragment_str = "\n".join(fragments)

        return f"""
            {fragment_str}
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    pullRequest(number: {number}) {{
                        ...PullRequestFields
                        {commits_field}
                        {labels_field}
                        {reviews_field}
                    }}
                }}
            }}
            {PULL_REQUEST_FRAGMENT}
        """

    @staticmethod
    def get_pull_requests(
        owner: str, name: str, states: list[str] | None = None, first: int = 10, after: str | None = None
    ) -> str:
        """
        Get pull requests with pagination.

        Args:
            owner: Repository owner
            name: Repository name
            states: PR states to filter (OPEN, CLOSED, MERGED)
            first: Number of results to return
            after: Cursor for pagination

        Returns:
            GraphQL query string
        """
        states_str = f"states: [{', '.join(states)}]" if states else ""
        after_str = f', after: "{after}"' if after else ""

        return f"""
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    pullRequests({states_str}, first: {first}{after_str}, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
                        totalCount
                        pageInfo {{
                            hasNextPage
                            endCursor
                        }}
                        nodes {{
                            ...PullRequestFields
                        }}
                    }}
                }}
            }}
            {PULL_REQUEST_FRAGMENT}
        """

    @staticmethod
    def get_commit(owner: str, name: str, oid: str) -> str:
        """
        Get commit information.

        Args:
            owner: Repository owner
            name: Repository name
            oid: Commit SHA

        Returns:
            GraphQL query string
        """
        return f"""
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    object(oid: "{oid}") {{
                        ... on Commit {{
                            ...CommitFields
                        }}
                    }}
                }}
            }}
            {COMMIT_FRAGMENT}
        """

    @staticmethod
    def get_file_contents(owner: str, name: str, expression: str) -> str:
        """
        Get file contents from repository.

        Args:
            owner: Repository owner
            name: Repository name
            expression: Git expression (e.g., "main:path/to/file")

        Returns:
            GraphQL query string
        """
        return f"""
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    object(expression: "{expression}") {{
                        ... on Blob {{
                            text
                            byteSize
                        }}
                    }}
                }}
            }}
        """

    @staticmethod
    def get_issues(
        owner: str, name: str, states: list[str] | None = None, first: int = 10, after: str | None = None
    ) -> str:
        """
        Get issues with pagination.

        Args:
            owner: Repository owner
            name: Repository name
            states: Issue states to filter (OPEN, CLOSED)
            first: Number of results
            after: Cursor for pagination

        Returns:
            GraphQL query string
        """
        states_str = f"states: [{', '.join(states)}]" if states else ""
        after_str = f', after: "{after}"' if after else ""

        return f"""
            query {{
                repository(owner: "{owner}", name: "{name}") {{
                    issues({states_str}, first: {first}{after_str}, orderBy: {{field: UPDATED_AT, direction: DESC}}) {{
                        totalCount
                        pageInfo {{
                            hasNextPage
                            endCursor
                        }}
                        nodes {{
                            id
                            number
                            title
                            body
                            state
                            createdAt
                            updatedAt
                            author {{
                                login
                            }}
                        }}
                    }}
                }}
            }}
        """


class MutationBuilder:
    """Builder for GraphQL mutations."""

    @staticmethod
    def add_comment(subject_id: str, body: str) -> tuple[str, dict[str, Any]]:
        """
        Add a comment to a PR or issue.

        Args:
            subject_id: The node ID of the PR or issue
            body: Comment body

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($subjectId: ID!, $body: String!) {
                addComment(input: {subjectId: $subjectId, body: $body}) {
                    commentEdge {
                        node {
                            id
                            body
                            createdAt
                        }
                    }
                }
            }
        """
        variables = {
            "subjectId": subject_id,
            "body": body,
        }
        return mutation, variables

    @staticmethod
    def add_labels(labelable_id: str, label_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """
        Add labels to a PR or issue.

        Args:
            labelable_id: The node ID of the PR or issue
            label_ids: List of label node IDs

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($labelableId: ID!, $labelIds: [ID!]!) {
                addLabelsToLabelable(input: {labelableId: $labelableId, labelIds: $labelIds}) {
                    clientMutationId
                }
            }
        """
        variables = {
            "labelableId": labelable_id,
            "labelIds": label_ids,
        }
        return mutation, variables

    @staticmethod
    def remove_labels(labelable_id: str, label_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """
        Remove labels from a PR or issue.

        Args:
            labelable_id: The node ID of the PR or issue
            label_ids: List of label node IDs to remove

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($labelableId: ID!, $labelIds: [ID!]!) {
                removeLabelsFromLabelable(input: {labelableId: $labelableId, labelIds: $labelIds}) {
                    clientMutationId
                }
            }
        """
        variables = {
            "labelableId": labelable_id,
            "labelIds": label_ids,
        }
        return mutation, variables

    @staticmethod
    def add_assignees(assignable_id: str, assignee_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """
        Add assignees to a PR or issue.

        Args:
            assignable_id: The node ID of the PR or issue
            assignee_ids: List of user node IDs

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($assignableId: ID!, $assigneeIds: [ID!]!) {
                addAssigneesToAssignable(input: {assignableId: $assignableId, assigneeIds: $assigneeIds}) {
                    clientMutationId
                }
            }
        """
        variables = {
            "assignableId": assignable_id,
            "assigneeIds": assignee_ids,
        }
        return mutation, variables

    @staticmethod
    def create_issue(
        repository_id: str,
        title: str,
        body: str | None = None,
        assignee_ids: list[str] | None = None,
        label_ids: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """
        Create a new issue.

        Args:
            repository_id: Repository node ID
            title: Issue title
            body: Issue body (optional)
            assignee_ids: List of assignee node IDs (optional)
            label_ids: List of label node IDs (optional)

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($repositoryId: ID!, $title: String!, $body: String, $assigneeIds: [ID!], $labelIds: [ID!]) {
                createIssue(input: {
                    repositoryId: $repositoryId,
                    title: $title,
                    body: $body,
                    assigneeIds: $assigneeIds,
                    labelIds: $labelIds
                }) {
                    issue {
                        id
                        number
                        title
                    }
                }
            }
        """
        variables = {
            "repositoryId": repository_id,
            "title": title,
            "body": body,
            "assigneeIds": assignee_ids,
            "labelIds": label_ids,
        }
        return mutation, variables

    @staticmethod
    def request_reviews(pull_request_id: str, user_ids: list[str]) -> tuple[str, dict[str, Any]]:
        """
        Request reviews on a pull request.

        Args:
            pull_request_id: PR node ID
            user_ids: List of user node IDs

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($pullRequestId: ID!, $userIds: [ID!]!) {
                requestReviews(input: {pullRequestId: $pullRequestId, userIds: $userIds}) {
                    clientMutationId
                }
            }
        """
        variables = {
            "pullRequestId": pull_request_id,
            "userIds": user_ids,
        }
        return mutation, variables

    @staticmethod
    def update_pull_request(
        pull_request_id: str, title: str | None = None, body: str | None = None
    ) -> tuple[str, dict[str, Any]]:
        """
        Update pull request title or body.

        Args:
            pull_request_id: PR node ID
            title: New title (optional)
            body: New body (optional)

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($pullRequestId: ID!, $title: String, $body: String) {
                updatePullRequest(input: {pullRequestId: $pullRequestId, title: $title, body: $body}) {
                    pullRequest {
                        id
                        number
                        title
                        body
                    }
                }
            }
        """
        variables = {
            "pullRequestId": pull_request_id,
            "title": title,
            "body": body,
        }
        return mutation, variables

    @staticmethod
    def enable_pull_request_automerge(pull_request_id: str, merge_method: str = "SQUASH") -> tuple[str, dict[str, Any]]:
        """
        Enable auto-merge on a pull request.

        Args:
            pull_request_id: PR node ID
            merge_method: MERGE, SQUASH, or REBASE

        Returns:
            Tuple of (mutation string, variables dict)
        """
        mutation = """
            mutation($pullRequestId: ID!, $mergeMethod: PullRequestMergeMethod!) {
                enablePullRequestAutoMerge(input: {pullRequestId: $pullRequestId, mergeMethod: $mergeMethod}) {
                    clientMutationId
                }
            }
        """
        variables = {
            "pullRequestId": pull_request_id,
            "mergeMethod": merge_method,
        }
        return mutation, variables


# Pagination Pattern Documentation:
# For async pagination with GraphQL, use this pattern:
#
# async def get_all_pull_requests(client, owner, name):
#     results = []
#     cursor = None
#     while True:
#         query = QueryBuilder.get_pull_requests(owner, name, after=cursor, first=100)
#         data = await client.execute(query)
#         results.extend(data['repository']['pullRequests']['nodes'])
#         if not data['repository']['pullRequests']['pageInfo']['hasNextPage']:
#             break
#         cursor = data['repository']['pullRequests']['pageInfo']['endCursor']
#     return results
