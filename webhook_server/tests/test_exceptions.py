"""Tests for custom exceptions."""

import pytest

from webhook_server.libs.exceptions import (
    NoApiTokenError,
    ProcessGithubWebhookError,
    RepositoryNotFoundInConfigError,
)


def test_repository_not_found_error():
    """Test RepositoryNotFoundInConfigError can be raised."""
    with pytest.raises(RepositoryNotFoundInConfigError):
        raise RepositoryNotFoundInConfigError("test-repo not found")


def test_process_github_webhook_error():
    """Test ProcessGithubWebhookError initialization."""
    err_dict = {"error": "test error", "details": "something went wrong"}
    error = ProcessGithubWebhookError(err_dict)

    assert error.err == err_dict
    assert str(err_dict) in str(error)


def test_no_api_token_error():
    """Test NoApiTokenError can be raised."""
    with pytest.raises(NoApiTokenError):
        raise NoApiTokenError("No API token provided")
