import pytest


class TestPrepareRetestWellcomeMsg:
    @pytest.mark.parametrize(
        "tox, build_and_push_container, pypi, pre_commit, conventional_title, expected",
        [
            (False, False, False, False, False, " * This repository does not support retest actions"),
            (True, False, False, False, False, " * `/retest tox`: Retest tox\n * `/retest all`: Retest all\n"),
            (
                False,
                True,
                False,
                False,
                False,
                " * `/retest build-container`: Retest build-container\n * `/retest all`: Retest all\n",
            ),
            (
                False,
                False,
                True,
                False,
                False,
                " * `/retest python-module-install`: Retest python-module-install\n * `/retest all`: Retest all\n",
            ),
            (
                False,
                False,
                False,
                True,
                False,
                " * `/retest pre-commit`: Retest pre-commit\n * `/retest all`: Retest all\n",
            ),
            (
                True,
                True,
                True,
                True,
                True,
                " * `/retest tox`: Retest tox\n * `/retest build-container`: Retest build-container\n * `/retest python-module-install`: Retest python-module-install\n * `/retest pre-commit`: Retest pre-commit\n * `/retest conventional-title`: Retest conventional-title\n * `/retest all`: Retest all\n",
            ),
            (
                False,
                False,
                False,
                False,
                True,
                " * `/retest conventional-title`: Retest conventional-title\n * `/retest all`: Retest all\n",
            ),
        ],
    )
    def test_prepare_retest_wellcome_comment(
        self, process_github_webhook, tox, build_and_push_container, pypi, pre_commit, conventional_title, expected
    ):
        process_github_webhook.tox = tox
        process_github_webhook.build_and_push_container = build_and_push_container
        process_github_webhook.pypi = pypi
        process_github_webhook.pre_commit = pre_commit
        process_github_webhook.conventional_title = conventional_title

        assert process_github_webhook._prepare_retest_welcome_comment == expected
