#!/usr/bin/env python3
"""
Schema validation utility for webhook server configuration files.

This module provides utilities to validate configuration files against
the expected schema structure without requiring external JSON schema libraries.
"""

import sys
from pathlib import Path
from typing import Any, Union

import yaml  # type: ignore
from simple_logger.logger import get_logger


class ConfigValidator:
    """Validates webhook server configuration against schema rules."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def validate_config(self, config_data: dict[str, Any]) -> bool:
        """
        Validate configuration data against schema rules.

        Args:
            config_data: Configuration dictionary to validate

        Returns:
            True if valid, False otherwise. Errors stored in self.errors
        """
        self.errors = []

        # Check required root fields
        self._validate_required_fields(config_data)

        # Validate field types
        self._validate_root_fields(config_data)

        # Validate repositories section
        if "repositories" in config_data:
            self._validate_repositories(config_data["repositories"])

        return len(self.errors) == 0

    def _validate_required_fields(self, config: dict[str, Any]) -> None:
        """Validate that required fields are present."""
        required_fields = ["repositories"]

        for field in required_fields:
            if field not in config:
                self.errors.append(f"Missing required field: {field}")

    def _validate_root_fields(self, config: dict[str, Any]) -> None:
        """Validate root-level configuration fields."""
        # String fields
        string_fields = ["log-level", "log-file", "webhook-ip", "ip-bind", "webhook-secret"]
        for field in string_fields:
            if field in config and not isinstance(config[field], str):
                self.errors.append(f"Field '{field}' must be a string")

        # Integer fields
        integer_fields = ["github-app-id", "port", "max-workers"]
        for field in integer_fields:
            if field in config and not isinstance(config[field], int):
                self.errors.append(f"Field '{field}' must be an integer")

        # Boolean fields
        boolean_fields = ["verify-github-ips", "verify-cloudflare-ips", "disable-ssl-warnings"]
        for field in boolean_fields:
            if field in config and not isinstance(config[field], bool):
                self.errors.append(f"Field '{field}' must be a boolean")

        # Array fields
        array_fields = ["github-tokens", "default-status-checks", "auto-verified-and-merged-users"]
        for field in array_fields:
            if field in config and not isinstance(config[field], list):
                self.errors.append(f"Field '{field}' must be an array")

        # Enum validations
        if "log-level" in config and config["log-level"] not in ["INFO", "DEBUG"]:
            self.errors.append("Field 'log-level' must be either 'INFO' or 'DEBUG'")

        # Object validations
        if "docker" in config:
            self._validate_docker_config(config["docker"])

        if "branch-protection" in config:
            self._validate_branch_protection(config["branch-protection"])

    def _validate_docker_config(self, docker_config: Any) -> None:
        """Validate docker configuration."""
        if not isinstance(docker_config, dict):
            self.errors.append("Field 'docker' must be an object")
            return

        required_docker_fields = ["username", "password"]
        for field in required_docker_fields:
            if field not in docker_config:
                self.errors.append(f"Docker config missing required field: {field}")
            elif not isinstance(docker_config[field], str):
                self.errors.append(f"Docker field '{field}' must be a string")

    def _validate_branch_protection(self, branch_protection_config: Any) -> None:
        """Validate branch protection configuration."""
        if not isinstance(branch_protection_config, dict):
            self.errors.append("Field 'branch-protection' must be an object")
            return

        boolean_branch_protection_fields = [
            "strict",
            "require_code_owner_reviews",
            "dismiss_stale_reviews",
            "required_linear_history",
            "required_conversation_resolution",
        ]
        for field in boolean_branch_protection_fields:
            if field in branch_protection_config and not isinstance(branch_protection_config[field], bool):
                self.errors.append(f"Field 'branch-protection.{field}' must be a boolean")

        if "required_approving_review_count" in branch_protection_config:
            if not isinstance(branch_protection_config["required_approving_review_count"], int):
                self.errors.append("Field 'branch-protection.required_approving_review_count' must be an integer")

    def _validate_repositories(self, repositories: Any) -> None:
        """Validate repositories configuration."""
        if not isinstance(repositories, dict):
            self.errors.append("Field 'repositories' must be an object")
            return

        if not repositories:
            self.errors.append("Field 'repositories' cannot be empty")
            return

        for repo_name, repo_config in repositories.items():
            self._validate_single_repository(repo_name, repo_config)

    def _validate_single_repository(self, repo_name: str, repo_config: Any) -> None:
        """Validate a single repository configuration."""
        if not isinstance(repo_config, dict):
            self.errors.append(f"Repository '{repo_name}' configuration must be an object")
            return

        # Required repository fields
        if "name" not in repo_config:
            self.errors.append(f"Repository '{repo_name}' missing required field 'name'")

        # String fields
        string_fields = [
            "name",
            "log-level",
            "log-file",
            "slack-webhook-url",
            "tox-python-version",
            "conventional-title",
        ]
        for field in string_fields:
            if field in repo_config and not isinstance(repo_config[field], str):
                self.errors.append(f"Repository '{repo_name}' field '{field}' must be a string")

        # Boolean fields
        boolean_fields = ["verified-job", "pre-commit"]
        for field in boolean_fields:
            if field in repo_config and not isinstance(repo_config[field], bool):
                self.errors.append(f"Repository '{repo_name}' field '{field}' must be a boolean")

        # Integer fields
        integer_fields = ["minimum-lgtm"]
        for field in integer_fields:
            if field in repo_config and not isinstance(repo_config[field], int):
                self.errors.append(f"Repository '{repo_name}' field '{field}' must be an integer")

        # Array fields
        array_fields = [
            "events",
            "auto-verified-and-merged-users",
            "github-tokens",
            "set-auto-merge-prs",
            "can-be-merged-required-labels",
        ]
        for field in array_fields:
            if field in repo_config and not isinstance(repo_config[field], list):
                self.errors.append(f"Repository '{repo_name}' field '{field}' must be an array")

        # Complex object validations
        if "pypi" in repo_config:
            self._validate_pypi_config(repo_name, repo_config["pypi"])

        if "container" in repo_config:
            self._validate_container_config(repo_name, repo_config["container"])

        if "tox" in repo_config:
            self._validate_tox_config(repo_name, repo_config["tox"])

    def _validate_pypi_config(self, repo_name: str, pypi_config: Any) -> None:
        """Validate PyPI configuration."""
        if not isinstance(pypi_config, dict):
            self.errors.append(f"Repository '{repo_name}' pypi config must be an object")
            return

        if "token" in pypi_config and not isinstance(pypi_config["token"], str):
            self.errors.append(f"Repository '{repo_name}' pypi token must be a string")

    def _validate_container_config(self, repo_name: str, container_config: Any) -> None:
        """Validate container configuration."""
        if not isinstance(container_config, dict):
            self.errors.append(f"Repository '{repo_name}' container config must be an object")
            return

        string_fields = ["username", "password", "repository", "tag"]
        for field in string_fields:
            if field in container_config and not isinstance(container_config[field], str):
                self.errors.append(f"Repository '{repo_name}' container field '{field}' must be a string")

        if "release" in container_config and not isinstance(container_config["release"], bool):
            self.errors.append(f"Repository '{repo_name}' container 'release' must be a boolean")

        array_fields = ["build-args", "args"]
        for field in array_fields:
            if field in container_config and not isinstance(container_config[field], list):
                self.errors.append(f"Repository '{repo_name}' container field '{field}' must be an array")

    def _validate_tox_config(self, repo_name: str, tox_config: Any) -> None:
        """Validate tox configuration."""
        if not isinstance(tox_config, dict):
            self.errors.append(f"Repository '{repo_name}' tox config must be an object")
            return

        # Tox values can be strings or arrays
        for branch, tox_value in tox_config.items():
            if not isinstance(tox_value, (str, list)):
                self.errors.append(f"Repository '{repo_name}' tox branch '{branch}' must be a string or array")


def validate_config_file(config_path: Union[str, Path]) -> bool:
    """
    Validate a configuration file.

    Args:
        config_path: Path to the configuration file

    Returns:
        True if valid, False otherwise
    """
    try:
        with open(config_path, "r") as file_handle:
            config_data = yaml.safe_load(file_handle)
    except Exception as exception:
        logger = get_logger(name="test_schema_validator")
        logger.error(f"Error loading config file: {exception}")
        return False

    validator = ConfigValidator()
    is_valid = validator.validate_config(config_data)

    logger = get_logger(name="test_schema_validator")
    if not is_valid:
        logger.error("Configuration validation failed:")
        for error in validator.errors:
            logger.error(f"  - {error}")
    else:
        logger.info("Configuration is valid!")

    return is_valid


def main() -> int:
    """Main entry point for command-line usage."""
    if len(sys.argv) != 2:
        logger = get_logger(name="test_schema_validator")
        logger.error("Usage: python test_schema_validator.py <config_file_path>")
        return 1

    config_path = sys.argv[1]

    if not Path(config_path).exists():
        logger = get_logger(name="test_schema_validator")
        logger.error(f"Error: Config file '{config_path}' does not exist")
        return 1

    is_valid = validate_config_file(config_path)
    return 0 if is_valid else 1


if __name__ == "__main__":
    sys.exit(main())
