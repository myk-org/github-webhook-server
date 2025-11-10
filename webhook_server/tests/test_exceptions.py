"""Tests for custom exceptions."""

import pytest

from webhook_server.libs.exceptions import (
    NoApiTokenError,
    RepositoryNotFoundInConfigError,
)


def test_repository_not_found_error():
    """Test RepositoryNotFoundInConfigError can be raised."""
    with pytest.raises(RepositoryNotFoundInConfigError):
        raise RepositoryNotFoundInConfigError()


def test_no_api_token_error():
    """Test NoApiTokenError can be raised."""
    with pytest.raises(NoApiTokenError):
        raise NoApiTokenError()
