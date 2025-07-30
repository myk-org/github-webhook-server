import os
import tempfile
from typing import Any

import pytest
import yaml

from webhook_server.libs.config import Config


class TestConfigSchema:
    """Test suite for webhook server configuration schema validation."""

    @pytest.fixture
    def valid_minimal_config(self) -> dict[str, Any]:
        """Minimal valid configuration for testing."""
        return {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
            "repositories": {"test-repo": {"name": "org/test-repo"}},
        }

    @pytest.fixture
    def valid_full_config(self) -> dict[str, Any]:
        """Complete valid configuration with all options."""
        return {
            "log-level": "DEBUG",
            "log-file": "webhook.log",
            "github-app-id": 123456,
            "github-tokens": ["token1", "token2"],
            "webhook-ip": "http://localhost:5000",
            "ip-bind": "0.0.0.0",
            "port": 8080,
            "max-workers": 20,
            "webhook-secret": "secret123",  # pragma: allowlist secret
            "verify-github-ips": True,
            "verify-cloudflare-ips": False,
            "disable-ssl-warnings": True,
            "docker": {"username": "dockeruser", "password": "dockerpass"},  # pragma: allowlist secret
            "default-status-checks": ["WIP", "build"],
            "auto-verified-and-merged-users": ["bot[bot]"],
            "branch-protection": {
                "strict": True,
                "require_code_owner_reviews": True,
                "dismiss_stale_reviews": False,
                "required_approving_review_count": 2,
                "required_linear_history": True,
                "required_conversation_resolution": True,
            },
            "repositories": {
                "test-repo": {
                    "name": "org/test-repo",
                    "log-level": "INFO",
                    "log-file": "test-repo.log",
                    "slack-webhook-url": "https://hooks.slack.com/test",
                    "verified-job": True,
                    "pypi": {"token": "pypi-token"},
                    "events": ["push", "pull_request"],
                    "tox": {"main": "all", "dev": ["test1", "test2"]},
                    "tox-python-version": "3.11",
                    "pre-commit": True,
                    "protected-branches": {"main": {"include-runs": ["test1"], "exclude-runs": ["test2"]}, "dev": []},
                    "container": {
                        "username": "reguser",
                        "password": "regpass",  # pragma: allowlist secret
                        "repository": "registry.com/repo",
                        "tag": "latest",
                        "release": True,
                        "build-args": ["ARG1=val1"],
                        "args": ["--no-cache"],
                    },
                    "auto-verified-and-merged-users": ["user1"],
                    "github-tokens": ["repo-token"],
                    "branch-protection": {"strict": False, "required_approving_review_count": 1},
                    "set-auto-merge-prs": ["main"],
                    "can-be-merged-required-labels": ["ready"],
                    "conventional-title": "feat,fix,docs",
                    "minimum-lgtm": 2,
                }
            },
        }

    def create_temp_config_dir_and_data(self, config_data: dict[str, Any]) -> str:
        """Create a temporary directory with config.yaml file for testing."""
        temp_dir = tempfile.mkdtemp()
        config_file = os.path.join(temp_dir, "config.yaml")

        with open(config_file, "w") as config_file_handle:
            yaml.dump(config_data, config_file_handle)

        return temp_dir

    def test_valid_minimal_config_loads(
        self, valid_minimal_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that minimal valid configuration loads successfully."""
        temp_dir = self.create_temp_config_dir_and_data(valid_minimal_config)

        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)

            config = Config()
            assert config.root_data["github-app-id"] == 123456
            assert config.root_data["webhook-ip"] == "http://localhost:5000"
            assert "test-repo" in config.root_data["repositories"]
        finally:
            # Clean up
            import shutil

            shutil.rmtree(temp_dir)

    def test_valid_full_config_loads(self, valid_full_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that complete valid configuration loads successfully."""
        temp_dir = self.create_temp_config_dir_and_data(valid_full_config)

        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)

            config = Config()
            root_data = config.root_data

            # Test root-level properties
            assert root_data["log-level"] == "DEBUG"
            assert root_data["github-app-id"] == 123456
            assert root_data["port"] == 8080
            assert root_data["disable-ssl-warnings"] is True

            # Test repository-level properties
            repo_data = root_data["repositories"]["test-repo"]
            assert repo_data["name"] == "org/test-repo"
            assert repo_data["minimum-lgtm"] == 2
            assert repo_data["conventional-title"] == "feat,fix,docs"
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_log_level_enum_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that log-level accepts only valid enum values."""
        # Valid values should work
        for level in ["INFO", "DEBUG"]:
            config = valid_minimal_config.copy()
            config["log-level"] = level
            temp_dir = self.create_temp_config_dir_and_data(config)

            try:
                config_file = os.path.join(temp_dir, "config.yaml")
                with open(config_file, "r") as file_handle:
                    data = yaml.safe_load(file_handle)
                    assert data["log-level"] == level
            finally:
                import shutil

                shutil.rmtree(temp_dir)

    def test_required_fields_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test that missing required fields are handled appropriately."""
        # Test missing repositories
        config_without_repos = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:5000",
        }

        temp_dir = self.create_temp_config_dir_and_data(config_without_repos)

        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)

            with pytest.raises(ValueError, match="does not have `repositories`"):
                Config()
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_array_fields_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that array fields accept lists."""
        config = valid_minimal_config.copy()
        config["github-tokens"] = ["token1", "token2", "token3"]
        config["default-status-checks"] = ["WIP", "build", "test"]
        config["auto-verified-and-merged-users"] = ["bot1[bot]", "bot2[bot]"]

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert len(data["github-tokens"]) == 3
                assert len(data["default-status-checks"]) == 3
                assert len(data["auto-verified-and-merged-users"]) == 2
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_docker_object_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that docker configuration accepts proper object structure."""
        config = valid_minimal_config.copy()
        config["docker"] = {"username": "testuser", "password": "testpass"}  # pragma: allowlist secret

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["docker"]["username"] == "testuser"
                assert data["docker"]["password"] == "testpass"  # pragma: allowlist secret
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_branch_protection_object_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that branch-protection accepts proper boolean and integer values."""
        config = valid_minimal_config.copy()
        config["branch-protection"] = {
            "strict": True,
            "require_code_owner_reviews": False,
            "dismiss_stale_reviews": True,
            "required_approving_review_count": 2,
            "required_linear_history": False,
            "required_conversation_resolution": True,
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                branch_protection = data["branch-protection"]
                assert branch_protection["strict"] is True
                assert branch_protection["require_code_owner_reviews"] is False
                assert branch_protection["required_approving_review_count"] == 2
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_repository_structure_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that repository configuration accepts various structures."""
        config = valid_minimal_config.copy()
        config["repositories"] = {
            "repo1": {"name": "org/repo1"},
            "repo2": {"name": "org/repo2", "verified-job": False, "minimum-lgtm": 1},
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert "repo1" in data["repositories"]
                assert "repo2" in data["repositories"]
                assert data["repositories"]["repo2"]["minimum-lgtm"] == 1
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_tox_configuration_flexibility(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that tox configuration accepts both string and array values."""
        config = valid_minimal_config.copy()
        config["repositories"]["test-repo"]["tox"] = {
            "main": "all",  # string value
            "dev": ["test1", "test2"],  # array value
            "feature": "specific-test",  # another string
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                tox_config = data["repositories"]["test-repo"]["tox"]
                assert tox_config["main"] == "all"
                assert tox_config["dev"] == ["test1", "test2"]
                assert tox_config["feature"] == "specific-test"
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_protected_branches_flexibility(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that protected-branches accepts both arrays and objects."""
        config = valid_minimal_config.copy()
        config["repositories"]["test-repo"]["protected-branches"] = {
            "main": {"include-runs": ["test1", "test2"], "exclude-runs": ["skip-test"]},
            "dev": [],  # empty array
            "feature": ["simple-array"],
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                protected_branches = data["repositories"]["test-repo"]["protected-branches"]
                assert "include-runs" in protected_branches["main"]
                assert protected_branches["dev"] == []
                assert protected_branches["feature"] == ["simple-array"]
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_container_configuration_complete(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that container configuration accepts all properties."""
        config = valid_minimal_config.copy()
        config["repositories"]["test-repo"]["container"] = {
            "username": "reguser",
            "password": "regpass",  # pragma: allowlist secret
            "repository": "registry.com/repo",
            "tag": "v1.0.0",
            "release": True,
            "build-args": ["ARG1=value1", "ARG2=value2"],
            "args": ["--no-cache", "--pull"],
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                container = data["repositories"]["test-repo"]["container"]
                assert container["username"] == "reguser"
                assert container["release"] is True
                assert len(container["build-args"]) == 2
                assert len(container["args"]) == 2
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_boolean_fields_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that boolean fields accept proper boolean values."""
        config = valid_minimal_config.copy()
        config.update({"verify-github-ips": True, "verify-cloudflare-ips": False, "disable-ssl-warnings": True})
        config["repositories"]["test-repo"].update({"verified-job": False, "pre-commit": True})

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["verify-github-ips"] is True
                assert data["verify-cloudflare-ips"] is False
                assert data["disable-ssl-warnings"] is True
                assert data["repositories"]["test-repo"]["verified-job"] is False
                assert data["repositories"]["test-repo"]["pre-commit"] is True
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_integer_fields_validation(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that integer fields accept proper integer values."""
        config = valid_minimal_config.copy()
        config.update({"port": 8080, "max-workers": 20})
        config["repositories"]["test-repo"]["minimum-lgtm"] = 3

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["port"] == 8080
                assert data["max-workers"] == 20
                assert data["repositories"]["test-repo"]["minimum-lgtm"] == 3
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_disable_ssl_warnings_configuration(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test the disable-ssl-warnings configuration option."""
        config = valid_minimal_config.copy()
        config["disable-ssl-warnings"] = True

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["disable-ssl-warnings"] is True
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_empty_configuration_handling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test handling of empty configuration file."""
        temp_dir = self.create_temp_config_dir_and_data({})

        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)

            # Empty config should fail validation
            with pytest.raises(ValueError):
                Config()
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_malformed_yaml_handling(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test handling of malformed YAML configuration."""
        temp_dir = tempfile.mkdtemp()
        config_file = os.path.join(temp_dir, "config.yaml")

        # Write malformed YAML
        with open(config_file, "w") as file_handle:
            file_handle.write("invalid: yaml: content: [unclosed bracket")

        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)

            # Malformed YAML should result in empty config and fail repositories validation
            with pytest.raises(ValueError, match="does not have `repositories`"):
                Config()
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_default_values_behavior(
        self, valid_minimal_config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test that default values are properly applied when not specified."""
        # Test that optional fields can be omitted
        config = valid_minimal_config.copy()

        # Don't include optional fields
        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)
            config_obj = Config()
            # These fields should not be present since they weren't specified
            assert "disable-ssl-warnings" not in config_obj.root_data
            assert "verify-github-ips" not in config_obj.root_data
            assert "minimum-lgtm" not in config_obj.root_data["repositories"]["test-repo"]
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_create_issue_for_new_pr_configuration(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Test create-issue-for-new-pr configuration at global and repository levels."""
        # Test global configuration
        global_config = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:8080",
            "create-issue-for-new-pr": False,  # Global setting
            "repositories": {
                "test-repo": {
                    "name": "test-org/test-repo",
                    # No repository-specific setting - should use global
                }
            },
        }

        temp_dir = self.create_temp_config_dir_and_data(global_config)
        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)
            config = Config()
            assert config.root_data["create-issue-for-new-pr"] is False
        finally:
            import shutil

            shutil.rmtree(temp_dir)

        # Test repository-specific override
        repo_override_config = {
            "github-app-id": 123456,
            "github-tokens": ["token1"],
            "webhook-ip": "http://localhost:8080",
            "create-issue-for-new-pr": False,  # Global setting
            "repositories": {
                "test-repo": {
                    "name": "test-org/test-repo",
                    "create-issue-for-new-pr": True,  # Repository override
                }
            },
        }

        temp_dir = self.create_temp_config_dir_and_data(repo_override_config)
        try:
            monkeypatch.setenv("WEBHOOK_SERVER_DATA_DIR", temp_dir)
            config = Config()
            assert config.root_data["create-issue-for-new-pr"] is False
            assert config.root_data["repositories"]["test-repo"]["create-issue-for-new-pr"] is True
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_pr_size_thresholds_valid_configuration(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that pr-size-thresholds accepts valid configuration with threshold and color."""
        config = valid_minimal_config.copy()
        config["pr-size-thresholds"] = {
            "Small": {"threshold": 100, "color": "green"},
            "Large": {"threshold": 500, "color": "red"},
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                pr_thresholds = data["pr-size-thresholds"]
                assert pr_thresholds["Small"]["threshold"] == 100
                assert pr_thresholds["Small"]["color"] == "green"
                assert pr_thresholds["Large"]["threshold"] == 500
                assert pr_thresholds["Large"]["color"] == "red"
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_pr_size_thresholds_repository_level(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that pr-size-thresholds works at repository level."""
        config = valid_minimal_config.copy()
        config["repositories"]["test-repo"]["pr-size-thresholds"] = {
            "Express": {"threshold": 25, "color": "lightgray"},
            "Standard": {"threshold": 100, "color": "green"},
            "Extended": {"threshold": 300, "color": "orange"},
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                repo_thresholds = data["repositories"]["test-repo"]["pr-size-thresholds"]
                assert repo_thresholds["Express"]["threshold"] == 25
                assert repo_thresholds["Express"]["color"] == "lightgray"
                assert repo_thresholds["Extended"]["threshold"] == 300
                assert repo_thresholds["Extended"]["color"] == "orange"
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_pr_size_thresholds_various_color_names(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that pr-size-thresholds accepts various CSS3 color names."""
        config = valid_minimal_config.copy()
        config["pr-size-thresholds"] = {
            "Tiny": {"threshold": 10, "color": "lightgray"},
            "Small": {"threshold": 50, "color": "green"},
            "Medium": {"threshold": 150, "color": "orange"},
            "Large": {"threshold": 300, "color": "darkorange"},
            "Huge": {"threshold": 1000, "color": "red"},
            "Massive": {"threshold": 2000, "color": "darkred"},
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                pr_thresholds = data["pr-size-thresholds"]
                assert len(pr_thresholds) == 6
                assert pr_thresholds["Tiny"]["color"] == "lightgray"
                assert pr_thresholds["Massive"]["threshold"] == 2000
        finally:
            import shutil

            shutil.rmtree(temp_dir)

    def test_pr_size_thresholds_missing_fields(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test handling of pr-size-thresholds with missing threshold or color fields."""
        # Test missing threshold
        config = valid_minimal_config.copy()
        config["pr-size-thresholds"] = {
            "Small": {"color": "green"},  # missing threshold
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                # Should still load, validation will happen at runtime
                assert "pr-size-thresholds" in data
        finally:
            import shutil

            shutil.rmtree(temp_dir)

        # Test missing color (should be acceptable with fallback)
        config2 = valid_minimal_config.copy()
        config2["pr-size-thresholds"] = {
            "Small": {"threshold": 100},  # missing color
        }

        temp_dir2 = self.create_temp_config_dir_and_data(config2)

        try:
            config_file = os.path.join(temp_dir2, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["pr-size-thresholds"]["Small"]["threshold"] == 100
        finally:
            import shutil

            shutil.rmtree(temp_dir2)

    def test_pr_size_thresholds_invalid_threshold_values(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test pr-size-thresholds with invalid threshold values."""
        # Test negative threshold
        config = valid_minimal_config.copy()
        config["pr-size-thresholds"] = {
            "Small": {"threshold": -10, "color": "green"},
        }

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                # Config loads, but validation should catch this at runtime
                assert data["pr-size-thresholds"]["Small"]["threshold"] == -10
        finally:
            import shutil

            shutil.rmtree(temp_dir)

        # Test zero threshold
        config2 = valid_minimal_config.copy()
        config2["pr-size-thresholds"] = {
            "Small": {"threshold": 0, "color": "green"},
        }

        temp_dir2 = self.create_temp_config_dir_and_data(config2)

        try:
            config_file = os.path.join(temp_dir2, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["pr-size-thresholds"]["Small"]["threshold"] == 0
        finally:
            import shutil

            shutil.rmtree(temp_dir2)

        # Test non-integer threshold
        config3 = valid_minimal_config.copy()
        config3["pr-size-thresholds"] = {
            "Small": {"threshold": "not-a-number", "color": "green"},
        }

        temp_dir3 = self.create_temp_config_dir_and_data(config3)

        try:
            config_file = os.path.join(temp_dir3, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["pr-size-thresholds"]["Small"]["threshold"] == "not-a-number"
        finally:
            import shutil

            shutil.rmtree(temp_dir3)

    def test_pr_size_thresholds_empty_configuration(self, valid_minimal_config: dict[str, Any]) -> None:
        """Test that empty pr-size-thresholds configuration is handled properly."""
        config = valid_minimal_config.copy()
        config["pr-size-thresholds"] = {}

        temp_dir = self.create_temp_config_dir_and_data(config)

        try:
            config_file = os.path.join(temp_dir, "config.yaml")
            with open(config_file, "r") as file_handle:
                data = yaml.safe_load(file_handle)
                assert data["pr-size-thresholds"] == {}
        finally:
            import shutil

            shutil.rmtree(temp_dir)
