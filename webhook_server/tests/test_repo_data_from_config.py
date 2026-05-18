import logging as python_logging
from unittest.mock import patch


def test_repo_data_from_config_repository_found(process_github_webhook):
    process_github_webhook._repo_data_from_config(repository_config={})

    assert process_github_webhook.repository_full_name == "my-org/test-repo"
    assert process_github_webhook.github_app_id == 123456
    assert process_github_webhook.pypi == {"token": "PYPI TOKEN"}
    assert process_github_webhook.verified_job
    assert process_github_webhook.tox_python_version == "3.8"
    assert process_github_webhook.tox_args == "-x --no-header"
    assert "args" not in process_github_webhook.tox, "args key must be popped from tox dict"
    assert "python-version" not in process_github_webhook.tox, "python-version key must be popped from tox dict"
    assert process_github_webhook.slack_webhook_url == "Slack webhook url"
    assert process_github_webhook.container_repository_username == "registry username"
    assert process_github_webhook.container_repository_password == "registry_password"  # pragma: allowlist secret
    assert process_github_webhook.container_repository == "registry_repository_full_path"
    assert process_github_webhook.dockerfile == "Dockerfile"
    assert process_github_webhook.container_tag == "image_tag"
    assert process_github_webhook.container_build_args == ["my-build-arg1=1", "my-build-arg2=2"]
    assert process_github_webhook.container_command_args == ["--format docker"]
    assert process_github_webhook.container_release
    assert process_github_webhook.pre_commit
    assert process_github_webhook.auto_verified_and_merged_users == ["my[bot]"]
    assert process_github_webhook.can_be_merged_required_labels == ["my-label1", "my-label2"]
    assert process_github_webhook.minimum_lgtm == 0


def test_tox_python_version_nested_no_deprecation_warning(process_github_webhook, caplog):
    """When 'python-version' is set under 'tox', no deprecation warning should be logged."""
    with caplog.at_level(python_logging.WARNING):
        process_github_webhook._repo_data_from_config(repository_config={})

    assert process_github_webhook.tox_python_version == "3.8"
    assert "deprecated" not in caplog.text.lower()


def test_tox_python_version_legacy_deprecation_warning(process_github_webhook, caplog):
    """When standalone 'tox-python-version' is used instead of nested 'tox.python-version',
    a deprecation warning should be logged."""
    original_get_value = process_github_webhook.config.get_value

    def patched_get_value(value, *args, **kwargs):
        # Override tox to return a dict WITHOUT python-version
        if value == "tox":
            return {"args": "-x --no-header"}
        # Return legacy standalone key
        if value == "tox-python-version":
            return "3.11"
        return original_get_value(value, *args, **kwargs)

    with patch.object(process_github_webhook.config, "get_value", side_effect=patched_get_value):
        with caplog.at_level(python_logging.WARNING):
            process_github_webhook._repo_data_from_config(repository_config={})

    assert process_github_webhook.tox_python_version == "3.11"
    assert "tox-python-version" in caplog.text
    assert "deprecated" in caplog.text.lower()


def test_tox_python_version_nested_takes_priority_over_legacy(process_github_webhook, caplog):
    """When both nested 'tox.python-version' and legacy 'tox-python-version' are set,
    nested takes priority and no deprecation warning is logged."""
    original_get_value = process_github_webhook.config.get_value

    def patched_get_value(value, *args, **kwargs):
        # tox dict has python-version set
        if value == "tox":
            return {"args": "-x --no-header", "python-version": "3.12"}
        # Legacy key also set
        if value == "tox-python-version":
            return "3.9"
        return original_get_value(value, *args, **kwargs)

    with patch.object(process_github_webhook.config, "get_value", side_effect=patched_get_value):
        with caplog.at_level(python_logging.WARNING):
            process_github_webhook._repo_data_from_config(repository_config={})

    assert process_github_webhook.tox_python_version == "3.12"
    assert "deprecated" not in caplog.text.lower()
