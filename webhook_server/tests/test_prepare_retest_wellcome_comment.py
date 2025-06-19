import pytest

from webhook_server.libs.pull_request_handler import PullRequestHandler


class TestPrepareRetestWellcomeMsg:
    @pytest.mark.parametrize(
        "tox, build_and_push_container, pypi, pre_commit, conventional_title, expected",
        [
            (False, False, False, False, False, " * No retest actions are configured for this repository"),
            (
                True,
                False,
                False,
                False,
                False,
                " * `/retest tox` - Run Python test suite with tox\n * `/retest all` - Run all available tests\n",
            ),
            (
                False,
                True,
                False,
                False,
                False,
                " * `/retest build-container` - Rebuild and test container image\n * `/retest all` - Run all available tests\n",
            ),
            (
                False,
                False,
                True,
                False,
                False,
                " * `/retest python-module-install` - Test Python package installation\n * `/retest all` - Run all available tests\n",
            ),
            (
                False,
                False,
                False,
                True,
                False,
                " * `/retest pre-commit` - Run pre-commit hooks and checks\n * `/retest all` - Run all available tests\n",
            ),
            (
                True,
                True,
                True,
                True,
                True,
                " * `/retest tox` - Run Python test suite with tox\n * `/retest build-container` - Rebuild and test container image\n * `/retest python-module-install` - Test Python package installation\n * `/retest pre-commit` - Run pre-commit hooks and checks\n * `/retest conventional-title` - Validate commit message format\n * `/retest all` - Run all available tests\n",
            ),
            (
                False,
                False,
                False,
                False,
                True,
                " * `/retest conventional-title` - Validate commit message format\n * `/retest all` - Run all available tests\n",
            ),
        ],
    )
    def test_prepare_retest_wellcome_comment(
        self,
        process_github_webhook,
        owners_file_handler,
        tox,
        build_and_push_container,
        pypi,
        pre_commit,
        conventional_title,
        expected,
    ):
        process_github_webhook.tox = tox
        process_github_webhook.build_and_push_container = build_and_push_container
        process_github_webhook.pypi = pypi
        process_github_webhook.pre_commit = pre_commit
        process_github_webhook.conventional_title = conventional_title
        process_github_webhook.pull_request = None
        pull_request_handler = PullRequestHandler(
            github_webhook=process_github_webhook, owners_file_handler=owners_file_handler
        )

        assert pull_request_handler._prepare_retest_welcome_comment == expected
