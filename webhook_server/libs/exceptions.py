class RepositoryNotFoundInConfigError(Exception):
    """Raised when a repository is not found in the configuration file."""

    pass


class NoApiTokenError(Exception):
    """Raised when no API token is available for GitHub API operations."""

    pass
