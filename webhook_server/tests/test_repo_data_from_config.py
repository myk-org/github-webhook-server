def test_repo_data_from_config_repository_found(process_github_webhook):
    process_github_webhook._repo_data_from_config(repository_config={})

    assert process_github_webhook.repository_full_name == "my-org/test-repo"
    assert process_github_webhook.github_app_id == 123456
    assert process_github_webhook.pypi == {"token": "PYPI TOKEN"}
    assert process_github_webhook.verified_job
    assert process_github_webhook.tox_python_version == "3.8"
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
