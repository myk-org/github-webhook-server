import re

import pytest

from webhook_server.libs.handlers.pull_request_handler import PullRequestHandler


class TestPrepareRetestWelcomeMsg:
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
                (
                    " * `/retest build-container` - Rebuild and test container image\n "
                    "* `/retest all` - Run all available tests\n"
                ),
            ),
            (
                False,
                False,
                True,
                False,
                False,
                " * `/retest python-module-install` - Test Python package installation\n "
                "* `/retest all` - Run all available tests\n",
            ),
            (
                False,
                False,
                False,
                True,
                False,
                (
                    " * `/retest pre-commit` - Run pre-commit hooks and checks\n "
                    "* `/retest all` - Run all available tests\n"
                ),
            ),
            (
                True,
                True,
                True,
                True,
                True,
                (
                    " * `/retest tox` - Run Python test suite with tox\n "
                    "* `/retest build-container` - Rebuild and test container image\n "
                    "* `/retest python-module-install` - Test Python package installation\n "
                    "* `/retest pre-commit` - Run pre-commit hooks and checks\n "
                    "* `/retest conventional-title` - Validate commit message format\n "
                    "* `/retest all` - Run all available tests\n"
                ),
            ),
            (
                False,
                False,
                False,
                False,
                True,
                " * `/retest conventional-title` - Validate commit message format\n "
                "* `/retest all` - Run all available tests\n",
            ),
        ],
    )
    def test_prepare_retest_welcome_comment(
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


class TestWelcomeMessageNewlineStructure:
    """Regression tests for welcome message markdown formatting.

    These tests ensure that conditional sections in the welcome message
    maintain proper newline structure to prevent headers from being
    glued to previous content.
    """

    def _create_handler(self, process_github_webhook, owners_file_handler, **config):
        """Create a PullRequestHandler with the specified configuration."""
        # Set default values for all config options
        defaults = {
            "tox": False,
            "build_and_push_container": False,
            "pypi": False,
            "pre_commit": False,
            "conventional_title": False,
            "parent_committer": "test-user",
            "auto_verified_and_merged_users": [],
            "create_issue_for_new_pr": False,
            "issue_url_for_welcome_msg": "https://github.com/test/repo/issues/1",
            "minimum_lgtm": 1,
            "pull_request": None,
        }
        defaults.update(config)

        for key, value in defaults.items():
            setattr(process_github_webhook, key, value)

        # Mock the owners file handler attributes needed for welcome message generation
        owners_file_handler.all_pull_request_approvers = ["approver1", "approver2"]
        owners_file_handler.all_pull_request_reviewers = ["reviewer1", "reviewer2"]

        return PullRequestHandler(github_webhook=process_github_webhook, owners_file_handler=owners_file_handler)

    def test_container_section_enabled_has_header_on_own_line(self, process_github_webhook, owners_file_handler):
        """When container operations are enabled, the header must start on its own line."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=True,
            tox=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        # Container Operations header should be on its own line (preceded by newline)
        assert "\n#### Container Operations\n" in welcome_msg, (
            "#### Container Operations header should be on its own line"
        )

    def test_cherry_pick_section_has_header_on_own_line_when_container_enabled(
        self, process_github_webhook, owners_file_handler
    ):
        """When container operations are enabled, Cherry-pick header must still be on its own line."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=True,
            tox=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        # Cherry-pick Operations header should be on its own line
        assert "\n#### Cherry-pick Operations\n" in welcome_msg, (
            "#### Cherry-pick Operations header should be on its own line"
        )

    def test_cherry_pick_section_has_header_on_own_line_when_container_disabled(
        self, process_github_webhook, owners_file_handler
    ):
        """When container operations are disabled, Cherry-pick header must still be on its own line.

        This is a critical regression test - when the container section is disabled,
        the Cherry-pick header should not be glued to the previous section.
        """
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=False,
            tox=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        # Cherry-pick Operations header should be on its own line, not glued to previous content
        assert "\n#### Cherry-pick Operations\n" in welcome_msg, (
            "#### Cherry-pick Operations header should be on its own line even when container section is disabled"
        )
        # Ensure it's not glued to retest section content
        assert "all`#### Cherry-pick" not in welcome_msg, "Cherry-pick header should not be glued to retest section"

    def test_no_excessive_blank_lines_between_sections(self, process_github_webhook, owners_file_handler):
        """Sections should not have more than 2 consecutive blank lines."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=True,
            tox=True,
            pre_commit=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        # Check for triple or more blank lines (more than 2 consecutive newlines with only whitespace)
        triple_blank_pattern = re.compile(r"\n\s*\n\s*\n\s*\n")
        match = triple_blank_pattern.search(welcome_msg)
        assert match is None, (
            f"Found excessive blank lines in welcome message at position {match.start() if match else 'N/A'}"
        )

    def test_all_level_4_headers_on_own_lines(self, process_github_webhook, owners_file_handler):
        """All #### headers in the welcome message should start on their own line."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=True,
            tox=True,
            pre_commit=True,
            conventional_title=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        # Find all #### headers
        header_pattern = re.compile(r"#### [A-Za-z]")
        headers = list(header_pattern.finditer(welcome_msg))

        for match in headers:
            pos = match.start()
            # Header should be at start of message or preceded by newline
            if pos > 0:
                char_before = welcome_msg[pos - 1]
                assert char_before == "\n", (
                    f"Header '{match.group()}' at position {pos} is not on its own line. "
                    f"Preceded by: '{repr(welcome_msg[max(0, pos - 20) : pos])}'"
                )

    @pytest.mark.parametrize(
        "build_and_push_container,tox,expected_container_section",
        [
            pytest.param(True, True, True, id="container_enabled"),
            pytest.param(False, True, False, id="container_disabled"),
            pytest.param(True, False, True, id="container_enabled_no_tox"),
            pytest.param(False, False, False, id="all_disabled"),
        ],
    )
    def test_section_presence_matches_config(
        self,
        process_github_webhook,
        owners_file_handler,
        build_and_push_container,
        tox,
        expected_container_section,
    ):
        """Container section should only appear when build_and_push_container is enabled."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=build_and_push_container,
            tox=tox,
        )

        welcome_msg = handler._prepare_welcome_comment()

        container_section_present = "#### Container Operations" in welcome_msg
        assert container_section_present == expected_container_section, (
            f"Container section presence ({container_section_present}) does not match "
            f"expected ({expected_container_section}) for build_and_push_container={build_and_push_container}"
        )

        # Cherry-pick section should always be present
        assert "#### Cherry-pick Operations" in welcome_msg, "Cherry-pick Operations section should always be present"

    def test_testing_validation_section_structure(self, process_github_webhook, owners_file_handler):
        """The Testing & Validation section should have proper structure."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=True,
            tox=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        # Find the Testing & Validation section
        assert "#### Testing & Validation" in welcome_msg

        # The order should be: Testing & Validation -> (retest content) -> Container Operations -> Cherry-pick
        testing_pos = welcome_msg.find("#### Testing & Validation")
        container_pos = welcome_msg.find("#### Container Operations")
        cherry_pick_pos = welcome_msg.find("#### Cherry-pick Operations")

        assert testing_pos < container_pos < cherry_pick_pos, (
            "Sections should be in order: Testing & Validation -> Container Operations -> Cherry-pick"
        )

    def test_retest_content_between_testing_header_and_container_section(
        self, process_github_webhook, owners_file_handler
    ):
        """Retest commands should appear between Testing & Validation header and Container section."""
        handler = self._create_handler(
            process_github_webhook,
            owners_file_handler,
            build_and_push_container=True,
            tox=True,
        )

        welcome_msg = handler._prepare_welcome_comment()

        testing_pos = welcome_msg.find("#### Testing & Validation")
        container_pos = welcome_msg.find("#### Container Operations")

        # Get content between the two headers
        section_between = welcome_msg[testing_pos:container_pos]

        # Should contain retest commands
        assert "/retest tox" in section_between, "Retest tox command should be in Testing & Validation section"
