import os
from logging import Logger
from typing import Any

import github
import yaml
from github.GithubException import UnknownObjectException
from simple_logger.logger import get_logger


class Config:
    def __init__(
        self,
        logger: Logger | None = None,
        repository: str | None = None,
    ) -> None:
        self.logger = logger or get_logger(name="config")
        self.data_dir: str = os.environ.get("WEBHOOK_SERVER_DATA_DIR", "/home/podman/data")
        self.config_path: str = os.path.join(self.data_dir, "config.yaml")
        self.repository = repository
        self.exists()
        self.repositories_exists()

    def exists(self) -> None:
        if not os.path.isfile(self.config_path):
            raise FileNotFoundError(f"Config file {self.config_path} not found")

    def repositories_exists(self) -> None:
        if not self.root_data.get("repositories"):
            raise ValueError(f"Config {self.config_path} does not have `repositories`")

    @property
    def root_data(self) -> dict[str, Any]:
        try:
            with open(self.config_path) as fd:
                return yaml.safe_load(fd) or {}
        except FileNotFoundError:
            # Since existence is validated in __init__, this indicates a race condition.
            # Re-raise to propagate the error rather than returning empty dict.
            self.logger.exception(f"Config file not found: {self.config_path}")
            raise
        except yaml.YAMLError:
            self.logger.exception(f"Config file has invalid YAML syntax: {self.config_path}")
            raise
        except PermissionError:
            self.logger.exception(f"Permission denied reading config file: {self.config_path}")
            raise
        except Exception:
            self.logger.exception(f"Failed to load config file {self.config_path}")
            raise

    @property
    def repository_data(self) -> dict[str, Any]:
        return self.root_data["repositories"].get(self.repository, {})

    def repository_local_data(self, github_api: github.Github, repository_full_name: str) -> dict[str, Any]:
        """
        Get repository-specific configuration from .github-webhook-server.yaml file.

        Reads configuration from the repository's .github-webhook-server.yaml file,
        which takes precedence over global config.yaml settings.

        Args:
            github_api: PyGithub API instance for repository access
            repository_full_name: Full repository name (owner/repo-name)

        Returns:
            Dictionary containing repository configuration, or empty dict if file not found

        Raises:
            yaml.YAMLError: If repository config file has invalid YAML syntax
        """
        if self.repository and repository_full_name:
            try:
                # Directly use github_api.get_repo instead of importing get_github_repo_api
                # to avoid circular dependency with helpers.py
                self.logger.debug(f"Get GitHub API for repository {repository_full_name}")
                repo = github_api.get_repo(repository_full_name)
                try:
                    _path = repo.get_contents(".github-webhook-server.yaml")
                except UnknownObjectException:
                    return {}

                config_file = _path[0] if isinstance(_path, list) else _path
                repo_config = yaml.safe_load(config_file.decoded_content)
                return repo_config

            except yaml.YAMLError:
                self.logger.exception(f"Repository {repository_full_name} config has invalid YAML syntax")
                raise

            except Exception:
                self.logger.exception(f"Repository {repository_full_name} config file not found or error")
                return {}

        self.logger.error("self.repository or self.repository_full_name is not defined")
        return {}

    def get_value(self, value: str, return_on_none: Any = None, extra_dict: dict[str, Any] | None = None) -> Any:
        """
        Get value from config

        Supports dot notation for nested values (e.g., "docker.username", "pypi.token")

        Order of getting value:
            1. Local repository file (.github-webhook-server.yaml)
            2. Repository level global config file (config.yaml)
            3. Root level global config file (config.yaml)
        """
        if extra_dict:
            result = self._get_nested_value(value, extra_dict)
            if result is not None:
                return result

        for scope in (self.repository_data, self.root_data):
            result = self._get_nested_value(value, scope)
            if result is not None:
                return result

        return return_on_none

    def _get_nested_value(self, key: str, data: dict[str, Any]) -> Any:
        """
        Get value from nested dict using dot notation.

        Args:
            key: Key with optional dot notation (e.g., "docker.username", "pypi.token")
            data: Dictionary to search

        Returns:
            Value if found, None otherwise
        """
        keys = key.split(".")
        current = data

        for k in keys:
            if isinstance(current, dict) and k in current:
                current = current[k]
            else:
                return None

        return current

    def get_ai_config(self, extra_dict: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Get AI features configuration with repository overrides.

        Order of precedence:
            1. extra_dict (e.g., from .github-webhook-server.yaml)
            2. Repository-level config
            3. Global config

        Returns:
            Dictionary with AI configuration, or empty dict if not configured
        """
        # Start with global AI config
        ai_config = self.root_data.get("ai-features", {})

        # Override with repository-level config if available
        repo_ai_config = self.repository_data.get("ai-features", {})
        if repo_ai_config:
            # Deep merge: repository config overrides global config
            ai_config = self._deep_merge(ai_config.copy(), repo_ai_config)

        # Override with extra_dict if provided (e.g., .github-webhook-server.yaml)
        if extra_dict and "ai-features" in extra_dict:
            ai_config = self._deep_merge(ai_config.copy(), extra_dict["ai-features"])

        return ai_config

    def _deep_merge(self, base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
        """
        Deep merge two dictionaries, with override taking precedence.

        Args:
            base: Base dictionary
            override: Override dictionary (takes precedence)

        Returns:
            Merged dictionary
        """
        result = base.copy()

        for key, value in override.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                # Recursively merge nested dicts
                result[key] = self._deep_merge(result[key], value)
            else:
                # Override value
                result[key] = value

        return result

    def is_ai_feature_enabled(self, feature_name: str, extra_dict: dict[str, Any] | None = None) -> bool:
        """
        Check if a specific AI feature is enabled.

        Args:
            feature_name: Name of the feature (e.g., "nlp-commands", "test-analysis")
            extra_dict: Extra config dict (e.g., from .github-webhook-server.yaml)

        Returns:
            True if feature is enabled, False otherwise
        """
        ai_config = self.get_ai_config(extra_dict)

        # Check if AI features are globally enabled
        if not ai_config.get("enabled", False):
            return False

        # Check if specific feature is enabled
        features = ai_config.get("features", {})
        feature_config = features.get(feature_name, {})

        return feature_config.get("enabled", False)

    def get_gemini_api_key(self, extra_dict: dict[str, Any] | None = None) -> str | None:
        """
        Get Gemini API key from environment variable.

        The environment variable name is configured in ai-features.gemini.api-key-env
        (defaults to GEMINI_API_KEY).

        Args:
            extra_dict: Extra config dict (e.g., from .github-webhook-server.yaml)

        Returns:
            API key if found, None otherwise
        """
        ai_config = self.get_ai_config(extra_dict)

        # Get API key environment variable name
        gemini_config = ai_config.get("gemini", {})
        api_key_env = gemini_config.get("api-key-env", "GEMINI_API_KEY")

        # Get API key from environment
        api_key = os.getenv(api_key_env)

        if not api_key:
            self.logger.warning(f"Gemini API key not found in environment variable: {api_key_env}")

        return api_key
