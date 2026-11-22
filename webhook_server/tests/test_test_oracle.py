"""Tests for the built-in webhook_server.test_oracle package."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

import pytest
from github.GithubException import GithubException
from pi_sidecar_client import AIResult
from pydantic import ValidationError

from webhook_server.test_oracle import (
    DEFAULT_TRIGGERS,
    AnalyzeResult,
    call_test_oracle,
)
from webhook_server.test_oracle import (
    TestMapper as OracleTestMapper,
)
from webhook_server.test_oracle import (
    TestMapping as OracleTestMapping,
)
from webhook_server.test_oracle import (
    TestRecommendation as OracleTestRecommendation,
)
from webhook_server.test_oracle.analyzer import (
    _GIT_INIT_TIMEOUT_SECONDS,
    _GIT_NETWORK_TIMEOUT_SECONDS,
    _MANUAL_FAILURE_COMMENT,
    _MAX_CUSTOM_PROMPT_BYTES,
    _MAX_CUSTOM_PROMPT_CHARS,
    _MAX_PROMPT_CANDIDATES_PER_FILE,
    DEFAULT_TIMEOUT_MINUTES,
    PROMPT_FILENAME,
    _build_ai_prompt,
    _build_pr_diff,
    _checkout_pr_head,
    _detect_language,
    _format_pr_comment,
    _parse_ai_response,
    _post_failure_comment,
    _resolve_custom_prompt,
    _sanitize_error,
)
from webhook_server.test_oracle.test_mapper import (
    DEFAULT_TEST_PATTERNS,
    _is_test_file,
    _strip_source_prefix,
    _strip_test_prefix,
    prioritize_candidate_tests,
)
from webhook_server.tests.conftest import TEST_GITHUB_TOKEN


async def _direct_github_api_call(
    func: Callable[..., Any],
    *args: Any,
    logger: Any = None,
    log_prefix: str = "",
    **kwargs: Any,
) -> Any:
    """Invoke PyGithub callable directly without threads or retry sleeps."""
    return func(*args, **kwargs)


def _make_pr_file(
    filename: str,
    status: str = "modified",
    patch_text: str | None = "@@ -1,1 +1,2 @@\n+x = 1",
) -> Mock:
    """Create a mock PyGithub File object."""
    pr_file = Mock()
    pr_file.filename = filename
    pr_file.status = status
    pr_file.patch = patch_text
    return pr_file


class TestModels:
    """Tests for webhook_server.test_oracle.models and package exports."""

    def test_package_exports(self) -> None:
        """Verify public symbols exported by webhook_server.test_oracle."""
        assert DEFAULT_TRIGGERS == ["approved"]
        assert AnalyzeResult is not None
        assert OracleTestMapper is not None
        assert OracleTestMapping is not None
        assert OracleTestRecommendation is not None
        assert callable(call_test_oracle)

    def test_test_mapping_defaults_and_fields(self) -> None:
        """TestMapping populates defaults and explicit fields."""
        minimal = OracleTestMapping(source_file="src/app.py")
        assert minimal.source_file == "src/app.py"
        assert minimal.candidate_tests == []
        assert minimal.mapping_reason == ""

        full = OracleTestMapping(
            source_file="src/auth.py",
            candidate_tests=["tests/test_auth.py"],
            mapping_reason="Naming convention",
        )
        assert full.candidate_tests == ["tests/test_auth.py"]
        assert full.mapping_reason == "Naming convention"

    @pytest.mark.parametrize("raw_name", [None, "", "   ", "\t\n"])
    def test_test_recommendation_coerces_empty_test_name(self, raw_name: str | None) -> None:
        """None or whitespace-only test_name is coerced to '(all)'."""
        rec = OracleTestRecommendation.model_validate({
            "test_file": "tests/test_auth.py",
            "test_name": raw_name,
            "reason": "Modifies auth flow",
            "priority": "critical",
            "confidence": "high",
        })
        assert rec.test_name == "(all)"

    def test_test_recommendation_default_and_named(self) -> None:
        """TestRecommendation defaults test_name to '(all)' and preserves explicit names."""
        default_rec = OracleTestRecommendation(
            test_file="tests/test_auth.py",
            reason="Modifies auth",
            priority="standard",
            confidence="medium",
        )
        assert default_rec.test_name == "(all)"

        named_rec = OracleTestRecommendation(
            test_file="tests/test_auth.py",
            test_name="TestAuth::test_login",
            reason="Modifies login()",
            priority="critical",
            confidence="low",
        )
        assert named_rec.test_name == "TestAuth::test_login"

    def test_test_recommendation_rejects_invalid_literals(self) -> None:
        """Invalid priority or confidence raises ValidationError."""
        with pytest.raises(ValidationError):
            OracleTestRecommendation.model_validate({
                "test_file": "tests/test_a.py",
                "reason": "reason",
                "priority": "urgent",
                "confidence": "high",
            })

        with pytest.raises(ValidationError):
            OracleTestRecommendation.model_validate({
                "test_file": "tests/test_a.py",
                "reason": "reason",
                "priority": "critical",
                "confidence": "certain",
            })

    def test_analyze_result_defaults_and_populated(self) -> None:
        """AnalyzeResult provides sensible defaults and stores recommendations."""
        empty = AnalyzeResult()
        assert empty.ai_provider == ""
        assert empty.ai_model == ""
        assert empty.recommendations == []
        assert empty.summary == ""
        assert empty.review_posted is False

        rec = OracleTestRecommendation(
            test_file="tests/test_app.py",
            reason="Covers changed handler",
            priority="critical",
            confidence="high",
        )
        populated = AnalyzeResult(
            ai_provider="claude",
            ai_model="sonnet",
            recommendations=[rec],
            summary="1 test files recommended (1 critical, 0 standard)",
            review_posted=True,
        )
        assert populated.ai_provider == "claude"
        assert populated.ai_model == "sonnet"
        assert len(populated.recommendations) == 1
        assert populated.review_posted is True


class TestTestMapper:
    """Tests for webhook_server.test_oracle.test_mapper."""

    @pytest.mark.parametrize(
        "bad_pattern",
        [
            "",
            "/etc/passwd",
            "\\windows\\path\\*.py",
            "../tests/*.py",
            "tests/../secret/*.py",
            "tests\\..\\secret\\*.py",
            "..",
        ],
    )
    def test_validate_patterns_rejects_unsafe_patterns(self, tmp_path: Path, bad_pattern: str) -> None:
        """Pattern validation rejects empty, absolute, and '..' traversal patterns."""
        with pytest.raises(ValueError, match="Invalid test pattern"):
            OracleTestMapper(repo_path=str(tmp_path), test_patterns=[bad_pattern])

    def test_default_patterns_used_when_none_or_empty(self, tmp_path: Path) -> None:
        """When test_patterns is None or empty, DEFAULT_TEST_PATTERNS are used."""
        mapper_none = OracleTestMapper(repo_path=str(tmp_path), test_patterns=None)
        mapper_empty = OracleTestMapper(repo_path=str(tmp_path), test_patterns=[])
        assert mapper_none._patterns == list(DEFAULT_TEST_PATTERNS)
        assert mapper_empty._patterns == list(DEFAULT_TEST_PATTERNS)

    def test_shared_logger_and_log_prefix(self, tmp_path: Path) -> None:
        """TestMapper uses provided logger and prepends log_prefix to debug/info/warning logs."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_a.py").write_text("A" * 50, encoding="utf-8")
        (tests_dir / "test_b.py").write_text("B" * 50, encoding="utf-8")

        custom_logger = Mock()
        mapper = OracleTestMapper(
            repo_path=str(tmp_path),
            logger=custom_logger,
            log_prefix="[ORACLE]",
        )
        mapper.discover_test_files()
        custom_logger.debug.assert_called_once()
        assert custom_logger.debug.call_args[0][0].startswith("[ORACLE] Discovered 2 test files")

        mapper.get_test_file_contents(
            ["/abs/outside.py", "tests/missing.py", "tests/test_a.py", "tests/test_b.py"],
            max_file_chars=20,
            max_total_chars=20,
        )
        warning_msgs = [call.args[0] for call in custom_logger.warning.call_args_list]
        assert len(warning_msgs) == 2
        assert all(msg.startswith("[ORACLE] ") for msg in warning_msgs)
        custom_logger.info.assert_called_once()
        assert custom_logger.info.call_args[0][0].startswith("[ORACLE] Reached total test content budget")

    def test_default_patterns_discover_non_python_and_root_tests(self, tmp_path: Path) -> None:
        """Expanded DEFAULT_TEST_PATTERNS discovers multi-language and root-level tests while skipping infra."""
        expected_tests = [
            "dotnet/BarTests.cs",
            "foo_test.py",
            "pkg/server_test.go",
            "spec/user_spec.rb",
            "src/test/java/BarTests.java",
            "src/test/java/FooTest.java",
            "test_root.py",
            "tests/test_mod.py",
            "web/src/app.test.ts",
            "web/src/util.spec.tsx",
        ]
        excluded_or_non_tests = [
            "src/app.ts",
            "pkg/server.go",
            "jest.config.ts",
            "vitest.config.ts",
            "tests/conftest.py",
        ]
        for rel in expected_tests + excluded_or_non_tests:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("// file\n", encoding="utf-8")

        mapper = OracleTestMapper(repo_path=str(tmp_path))
        assert mapper.discover_test_files() == sorted(expected_tests)

    def test_discover_test_files_exclusions_and_caching(self, tmp_path: Path) -> None:
        """Discovery finds valid tests, excludes infra files and ignored dirs, and caches results."""
        files_to_create = [
            "tests/test_alpha.py",
            "tests/sub/test_beta.py",
            "tests/__init__.py",
            "tests/conftest.py",
            "tests/setup.py",
            "test_root.py",
            ".git/test_git.py",
            "node_modules/pkg/test_node.py",
            ".venv/lib/test_venv.py",
            "tests/__pycache__/test_cached.py",
        ]
        for rel in files_to_create:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# test\n", encoding="utf-8")

        mapper = OracleTestMapper(
            repo_path=str(tmp_path),
            test_patterns=["**/*.py"],
        )
        discovered = mapper.discover_test_files()
        assert discovered == ["test_root.py", "tests/sub/test_beta.py", "tests/test_alpha.py"]

        # Adding a file after discovery returns cached list
        (tmp_path / "tests/test_new.py").write_text("# new\n", encoding="utf-8")
        assert mapper.discover_test_files() == discovered

    def test_symlink_escape_ignored_in_discovery_and_refused_in_contents(self, tmp_path: Path) -> None:
        """Symlinks pointing outside repo root or into ignored dirs are rejected."""
        repo = tmp_path / "repo"
        repo.mkdir()
        outside = tmp_path / "secret.py"
        outside.write_text("outside-repo-content\n", encoding="utf-8")

        tests_dir = repo / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_valid.py").write_text("def test_ok(): pass\n", encoding="utf-8")
        os.symlink(outside, tests_dir / "test_escape.py")
        os.symlink(repo / "tests" / "does_not_exist.py", tests_dir / "test_dangling.py")

        venv_dir = repo / ".venv"
        venv_dir.mkdir()
        (venv_dir / "test_hidden.py").write_text("x = 1\n", encoding="utf-8")
        os.symlink(venv_dir / "test_hidden.py", tests_dir / "test_link_to_venv.py")

        mapper = OracleTestMapper(repo_path=str(repo))
        discovered = mapper.discover_test_files()
        assert discovered == ["tests/test_valid.py"]

        contents = mapper.get_test_file_contents([
            "tests/test_valid.py",
            "tests/test_escape.py",
            "tests/test_dangling.py",
            "../secret.py",
            str(outside),
        ])
        assert list(contents.keys()) == ["tests/test_valid.py"]
        assert "def test_ok()" in contents["tests/test_valid.py"]

    def test_map_changed_files_strategies(self, tmp_path: Path) -> None:
        """Test config files, self-mapping, non-source files, direct naming, tokens, and parallel dirs."""
        test_files = [
            "tests/test_foo.py",
            "tests/foo_test.py",
            "tests/test_auth_middleware.py",
            "tests/test_user_auth.py",
            "tests/test_author.py",
            "tests/test_oauth.py",
            "tests/mod/test_bar.py",
        ]
        for rel in test_files:
            path = tmp_path / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# test\n", encoding="utf-8")

        mapper = OracleTestMapper(repo_path=str(tmp_path))
        mappings = mapper.map_changed_files([
            "pyproject.toml",
            "tests/test_foo.py",
            "tests/test_deleted.py",
            "README.md",
            "src/pkg/foo.py",
            "src/pkg/auth.py",
            "src/pkg/mod/bar.py",
            "src/pkg/unmatched.py",
        ])

        by_source = {m.source_file: m for m in mappings}

        # Config file -> all tests
        assert by_source["pyproject.toml"].candidate_tests == sorted(test_files)
        assert "Config file" in by_source["pyproject.toml"].mapping_reason

        # Changed test file -> self-mapped if discovered, else empty
        assert by_source["tests/test_foo.py"].candidate_tests == ["tests/test_foo.py"]
        assert "itself a test" in by_source["tests/test_foo.py"].mapping_reason
        assert by_source["tests/test_deleted.py"].candidate_tests == []

        # Non-source file -> empty
        assert by_source["README.md"].candidate_tests == []
        assert "Non-source" in by_source["README.md"].mapping_reason

        # Direct naming -> test_foo.py and foo_test.py
        assert by_source["src/pkg/foo.py"].candidate_tests == ["tests/foo_test.py", "tests/test_foo.py"]

        # Delimiter-bounded token matching -> auth matches auth_middleware & user_auth, NOT author or oauth
        assert by_source["src/pkg/auth.py"].candidate_tests == [
            "tests/test_auth_middleware.py",
            "tests/test_user_auth.py",
        ]

        # Parallel directory structure -> src/pkg/mod/bar.py -> tests/mod/test_bar.py
        assert by_source["src/pkg/mod/bar.py"].candidate_tests == ["tests/mod/test_bar.py"]

        # Unmatched source file -> empty with fallback reason
        assert by_source["src/pkg/unmatched.py"].candidate_tests == []
        assert "No direct mapping" in by_source["src/pkg/unmatched.py"].mapping_reason

    def test_parallel_directory_and_prefix_helpers(self) -> None:
        """Verify _strip_source_prefix, _strip_test_prefix, and _is_test_file variants."""
        assert _strip_source_prefix(("src", "pkg", "mod", "foo.py")) == ["mod", "foo"]
        assert _strip_source_prefix(("src", "foo.py")) == ["foo"]
        assert _strip_source_prefix(("pkg", "mod", "foo.py")) == ["pkg", "mod", "foo"]
        assert _strip_test_prefix(("tests", "mod", "test_foo.py")) == ["mod", "test_foo"]
        assert _strip_test_prefix(("test", "mod", "foo_test.py")) == ["mod", "foo_test"]

        assert _is_test_file(Path("tests/test_a.py")) is True
        assert _is_test_file(Path("tests/a_test.py")) is True
        assert _is_test_file(Path("web/app.test.tsx")) is True
        assert _is_test_file(Path("web/app.spec.js")) is True
        assert _is_test_file(Path("pkg/handler_test.go")) is True
        assert _is_test_file(Path("spec/user_spec.rb")) is True
        assert _is_test_file(Path("src/AuthTest.java")) is True
        assert _is_test_file(Path("src/auth.py")) is False

    def test_get_test_file_contents_bounded_reads_and_truncation_caps(self, tmp_path: Path) -> None:
        """Bounded reads truncate large files without reading whole file and respect total budget."""
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "test_one.py").write_text("A" * 5000, encoding="utf-8")
        (tests_dir / "test_two.py").write_text("B" * 100, encoding="utf-8")
        (tests_dir / "test_three.py").write_text("C" * 100, encoding="utf-8")

        mapper = OracleTestMapper(repo_path=str(tmp_path))

        # Verify bounded read passes limit + 1 to handle.read() instead of reading the entire file
        read_sizes: list[int | None] = []
        original_open = Path.open

        def tracking_open(self_path: Path, *args: Any, **kwargs: Any) -> Any:
            real_handle = original_open(self_path, *args, **kwargs)

            class _BoundedReader:
                def __enter__(self) -> _BoundedReader:
                    return self

                def __exit__(self, *exc_info: Any) -> None:
                    real_handle.close()

                def read(self, size: int = -1) -> str:
                    read_sizes.append(size)
                    return real_handle.read(size)

            return _BoundedReader()

        with patch.object(Path, "open", tracking_open):
            per_file = mapper.get_test_file_contents(["tests/test_one.py"], max_file_chars=30, max_total_chars=500)

        assert read_sizes == [31]
        assert per_file["tests/test_one.py"] == f"{'A' * 30}\n... [truncated at 30 chars]"

        # Total budget cap: first file fits within per-file cap (40 chars), second hits remaining budget, third skipped
        budgeted = mapper.get_test_file_contents(
            ["tests/test_one.py", "tests/test_two.py", "tests/test_three.py"],
            max_file_chars=40,
            max_total_chars=80,
        )
        assert "tests/test_one.py" in budgeted
        assert "tests/test_three.py" not in budgeted

    def test_prioritize_candidate_tests_orders_direct_before_broad_deduplicated(self) -> None:
        """Direct mappings are sorted ahead of broad config-file candidates with duplicates removed."""
        mappings = [
            OracleTestMapping(
                source_file="pyproject.toml",
                candidate_tests=["tests/test_a.py", "tests/test_b.py", "tests/test_c.py"],
                mapping_reason="Config file change affects all tests",
            ),
            OracleTestMapping(
                source_file="Makefile",
                candidate_tests=["tests/test_a.py", "tests/test_d.py"],
                mapping_reason="Config file change affects all tests",
            ),
            OracleTestMapping(
                source_file="src/pkg/z.py",
                candidate_tests=["tests/test_c.py", "tests/test_b.py"],
                mapping_reason="Direct",
            ),
            OracleTestMapping(
                source_file="src/pkg/y.py",
                candidate_tests=["tests/test_b.py"],
                mapping_reason="Direct",
            ),
        ]

        assert prioritize_candidate_tests(mappings) == [
            "tests/test_b.py",
            "tests/test_c.py",
            "tests/test_a.py",
            "tests/test_d.py",
        ]
        assert prioritize_candidate_tests([]) == []


class TestAnalyzerHelpers:
    """Tests for helper functions in webhook_server.test_oracle.analyzer."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("tests/test_app.py", "python"),
            ("src/ui.tsx", "typescript"),
            ("pkg/srv.go", "go"),
            ("scripts/run.sh", "bash"),
            ("README.md", ""),
            ("Makefile", ""),
        ],
    )
    def test_detect_language(self, path: str, expected: str) -> None:
        """_detect_language maps known extensions and returns empty string otherwise."""
        assert _detect_language(path) == expected

    def test_sanitize_error(self) -> None:
        """_sanitize_error redacts single or multiple secrets and handles empty inputs."""
        assert (
            _sanitize_error("fatal: https://tok123@github.com", "tok123") == "fatal: https://***REDACTED***@github.com"
        )
        assert (
            _sanitize_error("err tok123 and hdr456 failed", ["tok123", "hdr456"])
            == "err ***REDACTED*** and ***REDACTED*** failed"
        )
        assert _sanitize_error("plain error", "") == "plain error"
        assert _sanitize_error("plain error", []) == "plain error"
        assert _sanitize_error("", "tok123") == ""

    def test_build_pr_diff_patches_binary_and_truncation(self) -> None:
        """_build_pr_diff handles patches, patch=None files, and max_chars truncation."""
        files = [
            _make_pr_file("src/a.py", status="modified", patch_text="@@ -1 +1 @@\n-old\n+new"),
            _make_pr_file("assets/logo.png", status="added", patch_text=None),
        ]
        diff = _build_pr_diff(files)
        assert "diff --git a/src/a.py b/src/a.py" in diff
        assert "+new" in diff
        assert "diff --git a/assets/logo.png b/assets/logo.png" in diff
        assert "[no patch available; status=added]" in diff

        # Truncation cap
        truncated = _build_pr_diff(files, max_chars=50)
        assert "... [diff truncated at 50 chars]" in truncated
        assert "assets/logo.png" not in truncated

    @pytest.mark.asyncio
    async def test_resolve_custom_prompt_raw_prompt_precedence_and_cap(self) -> None:
        """raw-prompt takes priority over base-branch file without GitHub API calls and is capped."""
        mock_webhook = Mock()
        mock_webhook.repository = Mock()
        mock_pr = Mock()
        mock_pr.base = Mock(ref="main")
        logger = Mock()

        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            result = await _resolve_custom_prompt(
                github_webhook=mock_webhook,
                pull_request=mock_pr,
                raw_prompt="  Inline override  ",
                logger=logger,
                log_prefix="[TEST]",
            )
            assert result == "Inline override"
            mock_webhook.repository.get_contents.assert_not_called()

            oversized = "X" * (_MAX_CUSTOM_PROMPT_CHARS + 25)
            capped = await _resolve_custom_prompt(
                github_webhook=mock_webhook,
                pull_request=mock_pr,
                raw_prompt=oversized,
                logger=logger,
                log_prefix="[TEST]",
            )
            assert capped == (
                f"{'X' * _MAX_CUSTOM_PROMPT_CHARS}\n... [custom prompt truncated at {_MAX_CUSTOM_PROMPT_CHARS} chars]"
            )

    @pytest.mark.asyncio
    async def test_resolve_custom_prompt_fetches_from_base_branch_and_caps(self, tmp_path: Path) -> None:
        """Fetches TESTS_ORACLE_PROMPT.md from pull_request.base.ref (never PR head checkout) and caps length."""
        # Create a local file in tmp_path simulating an untrusted PR head checkout (must never be read)
        (tmp_path / PROMPT_FILENAME).write_text("Untrusted PR head instructions", encoding="utf-8")

        content_obj = Mock()
        content_obj.size = 32
        content_obj.decoded_content = b"  Focus on base-branch rules.\xff  \n"

        mock_webhook = Mock()
        mock_webhook.repository = Mock()
        mock_webhook.repository.get_contents = Mock(return_value=content_obj)
        mock_pr = Mock()
        mock_pr.base = Mock(ref="release/1.0")
        mock_pr.head = Mock(ref="untrusted-pr-head")
        logger = Mock()

        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            result_none = await _resolve_custom_prompt(
                github_webhook=mock_webhook,
                pull_request=mock_pr,
                raw_prompt=None,
                logger=logger,
                log_prefix="[TEST]",
            )
            result_ws = await _resolve_custom_prompt(
                github_webhook=mock_webhook,
                pull_request=mock_pr,
                raw_prompt="   \n ",
                logger=logger,
                log_prefix="[TEST]",
            )

        assert result_none == "Focus on base-branch rules.\ufffd"
        assert result_ws == "Focus on base-branch rules.\ufffd"
        assert "Untrusted PR head" not in result_none
        for call in mock_webhook.repository.get_contents.call_args_list:
            assert call.args == (PROMPT_FILENAME,)
            assert call.kwargs == {"ref": "release/1.0"}

        # Base-branch prompt longer than _MAX_CUSTOM_PROMPT_CHARS is capped
        long_bytes = ("Y" * (_MAX_CUSTOM_PROMPT_CHARS + 10)).encode("utf-8")
        long_obj = Mock(size=len(long_bytes), decoded_content=long_bytes)
        mock_webhook.repository.get_contents = Mock(return_value=long_obj)

        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            capped = await _resolve_custom_prompt(
                github_webhook=mock_webhook,
                pull_request=mock_pr,
                raw_prompt=None,
                logger=logger,
                log_prefix="[TEST]",
            )

        assert capped == (
            f"{'Y' * _MAX_CUSTOM_PROMPT_CHARS}\n... [custom prompt truncated at {_MAX_CUSTOM_PROMPT_CHARS} chars]"
        )

    @pytest.mark.asyncio
    async def test_resolve_custom_prompt_ignores_404_and_handles_edge_cases(self) -> None:
        """Ignores 404 silently; warns and returns '' on non-404, generic errors, dirs, oversized, decode errors."""
        mock_webhook = Mock()
        mock_webhook.repository = Mock()
        mock_pr = Mock()
        mock_pr.base = Mock(ref="main")

        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            # 404 ignored silently
            logger = Mock()
            mock_webhook.repository.get_contents = Mock(
                side_effect=GithubException(404, {"message": "Not Found"}, None)
            )
            assert await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]") == ""
            logger.warning.assert_not_called()

            # Non-404 GithubException logs warning
            logger = Mock()
            mock_webhook.repository.get_contents = Mock(
                side_effect=GithubException(500, {"message": "Server Error"}, None)
            )
            assert await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]") == ""
            logger.warning.assert_called_once()

            # Generic exception during fetch logs warning
            logger = Mock()
            mock_webhook.repository.get_contents = Mock(side_effect=RuntimeError("network failure"))
            assert await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]") == ""
            logger.warning.assert_called_once()

            # Directory entry returns "" and logs warning
            logger = Mock()
            mock_webhook.repository.get_contents = Mock(return_value=[Mock(), Mock()])
            assert await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]") == ""
            logger.warning.assert_called_once()
            assert "is a directory" in logger.warning.call_args[0][0]

            # Oversized file (> _MAX_CUSTOM_PROMPT_BYTES) returns "" without reading decoded_content
            logger = Mock()
            oversized_obj = Mock()
            oversized_obj.size = _MAX_CUSTOM_PROMPT_BYTES + 1
            decoded_prop = PropertyMock(side_effect=AssertionError("should not read oversized content"))
            type(oversized_obj).decoded_content = decoded_prop
            mock_webhook.repository.get_contents = Mock(return_value=oversized_obj)
            assert await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]") == ""
            logger.warning.assert_called_once()
            assert "too large" in logger.warning.call_args[0][0]
            decoded_prop.assert_not_called()

            # Decode/read error on decoded_content logs warning and returns ""
            logger = Mock()
            bad_decode_obj = Mock()
            bad_decode_obj.size = 100
            type(bad_decode_obj).decoded_content = PropertyMock(side_effect=AssertionError("unsupported encoding"))
            mock_webhook.repository.get_contents = Mock(return_value=bad_decode_obj)
            assert await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]") == ""
            logger.warning.assert_called_once()
            assert "Failed to read" in logger.warning.call_args[0][0]

    @pytest.mark.asyncio
    async def test_resolve_custom_prompt_reraises_cancelled_error(self) -> None:
        """asyncio.CancelledError is re-raised during both fetch and content read."""
        mock_webhook = Mock()
        mock_webhook.repository = Mock()
        mock_pr = Mock()
        mock_pr.base = Mock(ref="main")
        logger = Mock()

        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            mock_webhook.repository.get_contents = Mock(side_effect=asyncio.CancelledError())
            with pytest.raises(asyncio.CancelledError):
                await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]")

            cancel_obj = Mock()
            cancel_obj.size = 10
            type(cancel_obj).decoded_content = PropertyMock(side_effect=asyncio.CancelledError())
            mock_webhook.repository.get_contents = Mock(return_value=cancel_obj)
            with pytest.raises(asyncio.CancelledError):
                await _resolve_custom_prompt(mock_webhook, mock_pr, None, logger, "[TEST]")

    def test_build_ai_prompt_includes_all_sections(self) -> None:
        """_build_ai_prompt renders diff, mappings, test contents, and optional custom prompt."""
        mappings = [
            OracleTestMapping(source_file="src/a.py", candidate_tests=["tests/test_a.py"], mapping_reason="Direct"),
            OracleTestMapping(source_file="README.md", candidate_tests=[], mapping_reason="Non-source"),
        ]
        prompt = _build_ai_prompt(
            pr_diff="diff --git a/src/a.py b/src/a.py\n+x = 1",
            test_mappings=mappings,
            test_contents={"tests/test_a.py": "def test_a(): assert True"},
            custom_prompt="Run integration tests too.",
        )
        assert "## PR Diff" in prompt
        assert "### src/a.py" in prompt
        assert "  - tests/test_a.py" in prompt
        assert "(no direct mapping found)" in prompt
        assert "```python\ndef test_a(): assert True\n```" in prompt
        assert "## Additional Instructions" in prompt
        assert "Run integration tests too." in prompt

        prompt_no_custom = _build_ai_prompt(
            pr_diff="diff",
            test_mappings=[],
            test_contents={},
            custom_prompt="",
        )
        assert "## Test File Contents" not in prompt_no_custom
        assert "## Additional Instructions" not in prompt_no_custom

    def test_build_ai_prompt_caps_candidates_per_file(self) -> None:
        """_build_ai_prompt caps candidates per mapping at _MAX_PROMPT_CANDIDATES_PER_FILE with overflow notice."""
        total = _MAX_PROMPT_CANDIDATES_PER_FILE + 7
        many_candidates = [f"tests/test_{i:03d}.py" for i in range(total)]
        exact_candidates = [f"tests/exact_{i:03d}.py" for i in range(_MAX_PROMPT_CANDIDATES_PER_FILE)]
        mappings = [
            OracleTestMapping(source_file="pyproject.toml", candidate_tests=many_candidates, mapping_reason="Config"),
            OracleTestMapping(source_file="src/exact.py", candidate_tests=exact_candidates, mapping_reason="Direct"),
        ]

        prompt = _build_ai_prompt(pr_diff="diff", test_mappings=mappings, test_contents={})
        assert f"  - tests/test_{_MAX_PROMPT_CANDIDATES_PER_FILE - 1:03d}.py" in prompt
        assert f"  - tests/test_{_MAX_PROMPT_CANDIDATES_PER_FILE:03d}.py" not in prompt
        assert "  - ... (+7 more)" in prompt
        assert f"  - tests/exact_{_MAX_PROMPT_CANDIDATES_PER_FILE - 1:03d}.py" in prompt
        assert prompt.count("... (+") == 1

    def test_parse_ai_response_direct_json_and_malformed_items(self) -> None:
        """Direct JSON array parses valid items and skips non-dict/malformed entries."""
        logger = Mock()
        payload = [
            {
                "test_file": "tests/test_auth.py",
                "test_name": "test_login",
                "reason": "Covers changed login()",
                "priority": "critical",
                "confidence": "high",
            },
            "not-a-dict",
            {"test_file": "tests/test_bad.py", "priority": "invalid"},
        ]
        recs = _parse_ai_response(json.dumps(payload), logger=logger, log_prefix="[TEST]")
        assert recs is not None
        assert len(recs) == 1
        assert recs[0].test_file == "tests/test_auth.py"
        assert recs[0].test_name == "test_login"
        assert logger.warning.call_count == 2

    def test_parse_ai_response_fenced_code_blocks(self) -> None:
        """Extracts JSON array from markdown fenced code blocks, skipping invalid/empty earlier blocks."""
        raw = (
            "Here is my analysis:\n"
            "```json\nnot valid json\n```\n"
            "```json\n[]\n```\n"
            "```json\n"
            '[{"test_file": "tests/test_api.py", "test_name": null, '
            '"reason": "API changed", "priority": "standard", "confidence": "medium"}]\n'
            "```\n"
        )
        recs = _parse_ai_response(raw)
        assert recs is not None
        assert len(recs) == 1
        assert recs[0].test_file == "tests/test_api.py"
        assert recs[0].test_name == "(all)"

        # Empty fenced array returns [] without warning
        logger = Mock()
        assert _parse_ai_response("```json\n[]\n```", logger=logger) == []
        logger.warning.assert_not_called()

    def test_parse_ai_response_prose_with_brackets_raw_decode(self) -> None:
        """Scans '[' offsets via JSONDecoder.raw_decode when surrounded by prose with brackets."""
        raw = (
            "Note [see section 2] and [not json] and [] before results: "
            '[{"test_file": "tests/test_x.py", "reason": "Direct hit", '
            '"priority": "critical", "confidence": "high"}] '
            "Hope this helps [end]."
        )
        recs = _parse_ai_response(raw)
        assert recs is not None
        assert len(recs) == 1
        assert recs[0].test_file == "tests/test_x.py"

        # Empty array inside prose returns [] cleanly without warning
        logger = Mock()
        assert _parse_ai_response("No tests needed: []", logger=logger) == []
        logger.warning.assert_not_called()

    @pytest.mark.parametrize("bad_output", ["", "   \n ", "Sorry, I cannot analyze this diff.", '{"key": "value"}'])
    def test_parse_ai_response_invalid_returns_none_and_logs(self, bad_output: str) -> None:
        """Empty or non-array AI outputs return None and log a warning."""
        logger = Mock()
        assert _parse_ai_response(bad_output, logger=logger, log_prefix="[TEST]") is None
        logger.warning.assert_called_once()

    @pytest.mark.parametrize(
        "malformed_payload",
        [
            json.dumps(["not-a-dict", {"test_file": "tests/test_bad.py", "priority": "invalid"}]),
            '```json\n[{"test_file": "tests/test_bad.py", "priority": "invalid"}]\n```',
            'Here are results: [{"test_file": "tests/test_bad.py", "priority": "invalid"}] done.',
        ],
    )
    def test_parse_ai_response_all_malformed_items_returns_none(self, malformed_payload: str) -> None:
        """Non-empty JSON array where all items are malformed returns None (never empty list [])."""
        logger = Mock()
        assert _parse_ai_response(malformed_payload, logger=logger, log_prefix="[TEST]") is None
        assert logger.warning.call_count >= 1

    def test_parse_ai_response_explicit_empty_array_returns_empty_list(self) -> None:
        """Explicit valid empty array [] returns [] without warning."""
        logger = Mock()
        result = _parse_ai_response("[]", logger=logger, log_prefix="[TEST]")
        assert result == []
        logger.warning.assert_not_called()

    def test_format_pr_comment_critical_standard_and_empty(self) -> None:
        """_format_pr_comment renders critical/standard sections, '(all)' vs named, and empty state."""
        recs = [
            OracleTestRecommendation(
                test_file="tests/test_auth.py",
                test_name="TestAuth::test_login",
                reason="Login updated",
                priority="critical",
                confidence="high",
            ),
            OracleTestRecommendation(
                test_file="tests/test_auth.py",
                test_name="(all)",
                reason="Shared fixtures affected",
                priority="standard",
                confidence="medium",
            ),
        ]
        comment = _format_pr_comment(recs, ai_provider="claude", ai_model="sonnet")
        assert "### Critical (directly affected)" in comment
        assert "- [ ] `tests/test_auth.py::TestAuth::test_login` — Login updated (High confidence)" in comment
        assert "### Standard (regression safety)" in comment
        assert "- [ ] `tests/test_auth.py` — Shared fixtures affected (Medium confidence)" in comment
        assert "**1 test files** recommended (1 critical, 1 standard)" in comment
        assert "AI Provider: claude (sonnet)" in comment

        empty_comment = _format_pr_comment([], ai_provider="gemini", ai_model="gemini-2.5-pro")
        assert "No tests need to run for this PR." in empty_comment
        assert "### Critical" not in empty_comment
        assert "### Standard" not in empty_comment
        assert "**0 test files** recommended (0 critical, 0 standard)" in empty_comment
        assert "AI Provider: gemini (gemini-2.5-pro)" in empty_comment


class TestCallTestOracle:
    """Tests for call_test_oracle orchestration."""

    @pytest.fixture
    def mock_github_webhook(self) -> Mock:
        """Create a mock GithubWebhook with test-oracle config."""
        mock_webhook = Mock()
        mock_webhook.logger = Mock()
        mock_webhook.log_prefix = "[TEST]"
        mock_webhook.token = TEST_GITHUB_TOKEN
        mock_webhook.mask_sensitive = True
        mock_webhook.repository = Mock()
        mock_webhook.repository.clone_url = "https://github.com/test-org/test-repo.git"
        mock_webhook.repository.get_contents = Mock(side_effect=GithubException(404, {"message": "Not Found"}, None))
        mock_webhook.config = Mock()
        mock_webhook.config.get_value = Mock(
            return_value={
                "ai-provider": "claude",
                "ai-model": "sonnet",
                "test-patterns": ["tests/**/*.py"],
                "triggers": ["approved"],
                "timeout-minutes": 15,
            }
        )
        return mock_webhook

    @pytest.fixture
    def mock_pull_request(self) -> Mock:
        """Create a mock PullRequest with changed files."""
        mock_pr = Mock()
        mock_pr.number = 42
        mock_pr.base = Mock(ref="main")
        mock_pr.head = Mock(sha="000000000000")
        mock_pr.get_files = Mock(
            return_value=[
                _make_pr_file("src/removed.py", status="removed", patch_text="@@ -1 +0,0 @@\n-old"),
                _make_pr_file("assets/bin.png", status="added", patch_text=None),
                _make_pr_file("src/auth.py", status="modified", patch_text="@@ -1 +1 @@\n-a\n+b"),
            ]
        )
        mock_pr.create_review_comment = Mock()
        mock_pr.create_issue_comment = Mock()
        return mock_pr

    @pytest.mark.asyncio
    async def test_not_configured_skips_silently(self, mock_github_webhook: Mock, mock_pull_request: Mock) -> None:
        """Skips silently when test-oracle is not configured."""
        mock_github_webhook.config.get_value = Mock(return_value=None)

        with patch("webhook_server.test_oracle.analyzer.call_ai", new_callable=AsyncMock) as mock_call_ai:
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)
            mock_call_ai.assert_not_called()
            mock_pull_request.get_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_missing_provider_or_model_logs_error_and_returns(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Logs an error and returns when ai-provider or ai-model is missing."""
        mock_github_webhook.config.get_value = Mock(return_value={"ai-provider": "claude"})

        with patch("webhook_server.test_oracle.analyzer.call_ai", new_callable=AsyncMock) as mock_call_ai:
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)
            mock_call_ai.assert_not_called()
            mock_github_webhook.logger.error.assert_called_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config_triggers", "trigger", "should_run"),
        [
            (None, "approved", True),
            (None, "pr-opened", False),
            (["pr-opened", "pr-synchronized"], "pr-opened", True),
            (["pr-opened"], "approved", False),
            (["approved"], None, True),
        ],
    )
    async def test_trigger_filtering(
        self,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
        config_triggers: list[str] | None,
        trigger: str | None,
        should_run: bool,
    ) -> None:
        """Trigger filtering respects defaults, configured triggers, and trigger=None always-run."""
        cfg: dict[str, Any] = {"ai-provider": "claude", "ai-model": "sonnet"}
        if config_triggers is not None:
            cfg["triggers"] = config_triggers
        mock_github_webhook.config.get_value = Mock(return_value=cfg)
        mock_pull_request.get_files = Mock(return_value=[])

        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger=trigger,
            )

        if should_run:
            mock_pull_request.get_files.assert_called_once()
        else:
            mock_pull_request.get_files.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_changed_files_returns_without_cloning_or_ai(
        self, mock_github_webhook: Mock, mock_pull_request: Mock
    ) -> None:
        """Empty PR file list returns cleanly without creating temp dir, cloning, or calling AI."""
        mock_pull_request.get_files = Mock(return_value=[])

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.run_command", new_callable=AsyncMock) as mock_run_cmd,
            patch("webhook_server.test_oracle.analyzer.call_ai", new_callable=AsyncMock) as mock_call_ai,
        ):
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

        mock_run_cmd.assert_not_called()
        mock_call_ai.assert_not_called()
        mock_pull_request.create_review_comment.assert_not_called()
        mock_pull_request.create_issue_comment.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_analysis_posts_file_review_comment_and_cleans_up(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Clones shallow PR head, calls AI with tools=[], posts file review comment, cleans up temp dir."""
        checkout_dir = tmp_path / "pr-test-oracle-run"
        checkout_dir.mkdir()
        (checkout_dir / "tests").mkdir()
        (checkout_dir / "tests" / "test_auth.py").write_text("def test_login(): pass\n", encoding="utf-8")

        ai_json = json.dumps([
            {
                "test_file": "tests/test_auth.py",
                "test_name": "test_login",
                "reason": "auth.py modified",
                "priority": "critical",
                "confidence": "high",
            }
        ])

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ) as mock_run_cmd,
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=True, text=ai_json, error=""),
            ) as mock_call_ai,
        ):
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger="approved",
            )

        # Verify isolated shallow checkout commands: clean clone_url, auth via env extraheader, explicit timeouts
        expected_auth_b64 = base64.b64encode(f"x-access-token:{TEST_GITHUB_TOKEN}".encode()).decode("ascii")
        assert mock_run_cmd.await_count == 3
        calls = mock_run_cmd.await_args_list
        cmds = [call.kwargs["command"] for call in calls]
        assert f"git init {checkout_dir}" in cmds[0]
        assert calls[0].kwargs["timeout"] == _GIT_INIT_TIMEOUT_SECONDS
        assert _GIT_INIT_TIMEOUT_SECONDS == 30

        assert "fetch --depth=1" in cmds[1]
        assert "refs/pull/42/head" in cmds[1]
        assert "https://github.com/test-org/test-repo.git" in cmds[1]
        assert TEST_GITHUB_TOKEN not in cmds[1]
        assert expected_auth_b64 not in cmds[1]
        assert calls[1].kwargs["timeout"] == _GIT_NETWORK_TIMEOUT_SECONDS
        assert _GIT_NETWORK_TIMEOUT_SECONDS == 120
        fetch_env = calls[1].kwargs["env"]
        assert fetch_env["GIT_TERMINAL_PROMPT"] == "0"
        assert fetch_env["GIT_CONFIG_COUNT"] == "1"
        assert fetch_env["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
        assert fetch_env["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected_auth_b64}"

        assert "checkout FETCH_HEAD" in cmds[2]
        assert calls[2].kwargs["timeout"] == _GIT_NETWORK_TIMEOUT_SECONDS

        for call in calls:
            assert call.kwargs["redact_secrets"] == [TEST_GITHUB_TOKEN, expected_auth_b64]
            assert call.kwargs["mask_sensitive"] is True

        # Verify call_ai invoked with tools=[] and configured provider/model/timeout
        mock_call_ai.assert_awaited_once()
        assert mock_call_ai.await_args is not None
        ai_kwargs = mock_call_ai.await_args.kwargs
        assert ai_kwargs["tools"] == []
        assert ai_kwargs["ai_provider"] == "claude"
        assert ai_kwargs["ai_model"] == "sonnet"
        assert ai_kwargs["timeout_minutes"] == 15
        assert ai_kwargs["cwd"] == str(checkout_dir)
        assert "tests/test_auth.py" in ai_kwargs["prompt"]

        # File-level review comment posted on first non-removed file with patch (src/auth.py)
        mock_pull_request.create_review_comment.assert_called_once()
        review_kwargs = mock_pull_request.create_review_comment.call_args.kwargs
        assert review_kwargs["commit"] == "000000000000"
        assert review_kwargs["path"] == "src/auth.py"
        assert review_kwargs["subject_type"] == "file"
        assert "tests/test_auth.py::test_login" in review_kwargs["body"]
        mock_pull_request.create_issue_comment.assert_not_called()

        # Guaranteed temp dir cleanup
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    async def test_checkout_pr_head_invalid_url_and_no_token(self, tmp_path: Path) -> None:
        """_checkout_pr_head rejects invalid clone_url when token set and omits auth env when token empty."""
        logger = Mock()
        with patch("webhook_server.test_oracle.analyzer.run_command", new_callable=AsyncMock) as mock_run_cmd:
            ok = await _checkout_pr_head(
                repo_path=str(tmp_path),
                clone_url="not-a-valid-url",
                github_token=TEST_GITHUB_TOKEN,
                pr_number=7,
                log_prefix="[TEST]",
                mask_sensitive=True,
                logger=logger,
            )
        assert ok is False
        mock_run_cmd.assert_not_called()
        logger.error.assert_called_once()
        assert "Invalid clone URL" in logger.error.call_args[0][0]

        with patch(
            "webhook_server.test_oracle.analyzer.run_command",
            new_callable=AsyncMock,
            return_value=(True, "", ""),
        ) as mock_run_cmd:
            ok = await _checkout_pr_head(
                repo_path=str(tmp_path),
                clone_url="https://github.com/test-org/test-repo.git",
                github_token="",
                pr_number=7,
                log_prefix="[TEST]",
                mask_sensitive=True,
                logger=logger,
            )
        assert ok is True
        assert mock_run_cmd.await_count == 3
        fetch_call = mock_run_cmd.await_args_list[1]
        assert "GIT_CONFIG_COUNT" not in fetch_call.kwargs["env"]
        assert fetch_call.kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
        assert fetch_call.kwargs["redact_secrets"] is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("fail_step", [0, 1, 2])
    @pytest.mark.parametrize("trigger", ["approved", None])
    async def test_checkout_failure_logs_redacted_error_and_cleans_up(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
        fail_step: int,
        trigger: str | None,
    ) -> None:
        """Failure at init/fetch/checkout redacts token and auth_b64, cleans up, and posts notice only when manual."""
        checkout_dir = tmp_path / f"pr-test-oracle-fail-{fail_step}-{trigger}"
        checkout_dir.mkdir()
        auth_b64 = base64.b64encode(f"x-access-token:{TEST_GITHUB_TOKEN}".encode()).decode("ascii")

        results = [(True, "", ""), (True, "", ""), (True, "", "")]
        results[fail_step] = (False, "", f"fatal: auth failed for {TEST_GITHUB_TOKEN} and basic {auth_b64}")

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                side_effect=results,
            ),
            patch("webhook_server.test_oracle.analyzer.call_ai", new_callable=AsyncMock) as mock_call_ai,
        ):
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger=trigger,
            )

        mock_call_ai.assert_not_called()
        mock_github_webhook.logger.error.assert_called_once()
        logged_err = mock_github_webhook.logger.error.call_args[0][0]
        assert TEST_GITHUB_TOKEN not in logged_err
        assert auth_b64 not in logged_err
        assert logged_err.count("***REDACTED***") == 2
        if trigger is None:
            mock_pull_request.create_issue_comment.assert_called_once_with(_MANUAL_FAILURE_COMMENT)
        else:
            mock_pull_request.create_issue_comment.assert_not_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("trigger", ["approved", None])
    async def test_ai_failure_logs_error_and_handles_trigger_comment(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
        trigger: str | None,
    ) -> None:
        """When call_ai fails: logs error, posts failure notice only for manual trigger, and cleans up."""
        checkout_dir = tmp_path / f"pr-test-oracle-ai-fail-{trigger}"
        checkout_dir.mkdir()
        mock_github_webhook.config.get_value = Mock(return_value={"ai-provider": "claude", "ai-model": "sonnet"})

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=False, text="", error="Sidecar timed out"),
            ) as mock_call_ai,
        ):
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger=trigger,
            )

        assert mock_call_ai.await_args is not None
        assert mock_call_ai.await_args.kwargs["timeout_minutes"] == DEFAULT_TIMEOUT_MINUTES
        mock_github_webhook.logger.error.assert_called_once()
        assert "Sidecar timed out" in mock_github_webhook.logger.error.call_args[0][0]
        mock_pull_request.create_review_comment.assert_not_called()
        if trigger is None:
            mock_pull_request.create_issue_comment.assert_called_once_with(_MANUAL_FAILURE_COMMENT)
        else:
            mock_pull_request.create_issue_comment.assert_not_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("trigger", ["approved", None])
    @pytest.mark.parametrize(
        "unparseable_text",
        [
            "Sorry, I could not analyze this PR.",
            '[{"test_file": "tests/test_bad.py", "priority": "invalid"}]',
        ],
    )
    async def test_unparseable_ai_response_never_posts_no_tests_comment(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
        trigger: str | None,
        unparseable_text: str,
    ) -> None:
        """Unparseable AI output logs error, never posts 'No tests need to run', and notifies only when manual."""
        checkout_dir = tmp_path / f"pr-test-oracle-unparseable-{trigger}"
        checkout_dir.mkdir(exist_ok=True)

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=True, text=unparseable_text, error=""),
            ),
        ):
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger=trigger,
            )

        mock_github_webhook.logger.error.assert_called_once()
        assert "unparseable response" in mock_github_webhook.logger.error.call_args[0][0]
        mock_pull_request.create_review_comment.assert_not_called()
        if trigger is None:
            mock_pull_request.create_issue_comment.assert_called_once_with(_MANUAL_FAILURE_COMMENT)
            posted = mock_pull_request.create_issue_comment.call_args[0][0]
            assert "No tests need to run for this PR" not in posted
        else:
            mock_pull_request.create_issue_comment.assert_not_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "review_exc",
        [
            GithubException(422, {"message": "Validation Failed"}, None),
            OSError("connection reset by peer"),
            RuntimeError("unexpected transport failure"),
        ],
    )
    async def test_review_comment_exception_falls_back_to_issue_comment(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
        review_exc: Exception,
    ) -> None:
        """GithubException or transport/generic Exception on create_review_comment falls back to issue comment."""
        checkout_dir = tmp_path / "pr-test-oracle-fallback"
        checkout_dir.mkdir()
        mock_pull_request.create_review_comment.side_effect = review_exc
        ai_json = json.dumps([
            {
                "test_file": "tests/test_auth.py",
                "reason": "Direct hit",
                "priority": "critical",
                "confidence": "high",
            }
        ])

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=True, text=ai_json, error=""),
            ),
        ):
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

        mock_pull_request.create_review_comment.assert_called_once()
        mock_pull_request.create_issue_comment.assert_called_once()
        assert "tests/test_auth.py" in mock_pull_request.create_issue_comment.call_args[0][0]
        mock_github_webhook.logger.warning.assert_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    async def test_review_comment_cancelled_error_reraised_without_fallback(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """asyncio.CancelledError during create_review_comment is re-raised without falling back to issue comment."""
        checkout_dir = tmp_path / "pr-test-oracle-review-cancel"
        checkout_dir.mkdir()
        mock_pull_request.create_review_comment.side_effect = asyncio.CancelledError()
        ai_json = json.dumps([
            {
                "test_file": "tests/test_auth.py",
                "reason": "Direct hit",
                "priority": "critical",
                "confidence": "high",
            }
        ])

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=True, text=ai_json, error=""),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

        mock_pull_request.create_review_comment.assert_called_once()
        mock_pull_request.create_issue_comment.assert_not_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    async def test_no_patchable_target_file_falls_back_to_issue_comment(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """When all changed files are removed or lack patches, falls back to create_issue_comment."""
        checkout_dir = tmp_path / "pr-test-oracle-no-patch"
        checkout_dir.mkdir()
        mock_pull_request.get_files = Mock(
            return_value=[
                _make_pr_file("src/old.py", status="removed", patch_text="@@ -1 +0,0 @@\n-x"),
                _make_pr_file("img.bin", status="added", patch_text=None),
            ]
        )
        ai_json = json.dumps([
            {
                "test_file": "tests/test_old.py",
                "reason": "Removed module",
                "priority": "standard",
                "confidence": "medium",
            }
        ])

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=True, text=ai_json, error=""),
            ),
        ):
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

        mock_pull_request.create_review_comment.assert_not_called()
        mock_pull_request.create_issue_comment.assert_called_once()
        mock_github_webhook.logger.warning.assert_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    async def test_empty_recommendations_post_issue_comment(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """Empty AI recommendation list posts a regular issue comment instead of a review comment."""
        checkout_dir = tmp_path / "pr-test-oracle-empty-recs"
        checkout_dir.mkdir()

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                return_value=AIResult(success=True, text="[]", error=""),
            ),
        ):
            await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

        mock_pull_request.create_review_comment.assert_not_called()
        mock_pull_request.create_issue_comment.assert_called_once()
        assert "No tests need to run for this PR." in mock_pull_request.create_issue_comment.call_args[0][0]
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    async def test_cancelled_error_reraised_and_cleans_up(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
    ) -> None:
        """asyncio.CancelledError is re-raised and still triggers temp dir cleanup."""
        checkout_dir = tmp_path / "pr-test-oracle-cancel"
        checkout_dir.mkdir()

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                side_effect=asyncio.CancelledError(),
            ),
        ):
            with pytest.raises(asyncio.CancelledError):
                await call_test_oracle(github_webhook=mock_github_webhook, pull_request=mock_pull_request)

        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("trigger", ["approved", None])
    async def test_unexpected_exception_logged_and_cleans_up(
        self,
        tmp_path: Path,
        mock_github_webhook: Mock,
        mock_pull_request: Mock,
        trigger: str | None,
    ) -> None:
        """Unexpected exceptions are caught, logged, cleaned up, and notify only for manual trigger."""
        checkout_dir = tmp_path / f"pr-test-oracle-err-{trigger}"
        checkout_dir.mkdir()

        with (
            patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call),
            patch("webhook_server.test_oracle.analyzer.tempfile.mkdtemp", return_value=str(checkout_dir)),
            patch(
                "webhook_server.test_oracle.analyzer.run_command",
                new_callable=AsyncMock,
                return_value=(True, "", ""),
            ),
            patch(
                "webhook_server.test_oracle.analyzer.call_ai",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Boom"),
            ),
        ):
            await call_test_oracle(
                github_webhook=mock_github_webhook,
                pull_request=mock_pull_request,
                trigger=trigger,
            )

        mock_github_webhook.logger.exception.assert_called_once()
        assert "failed unexpectedly" in mock_github_webhook.logger.exception.call_args[0][0]
        if trigger is None:
            mock_pull_request.create_issue_comment.assert_called_once_with(_MANUAL_FAILURE_COMMENT)
        else:
            mock_pull_request.create_issue_comment.assert_not_called()
        assert not checkout_dir.exists()

    @pytest.mark.asyncio
    async def test_post_failure_comment_handles_errors_and_reraises_cancel(self, mock_pull_request: Mock) -> None:
        """_post_failure_comment swallows comment errors with a warning and re-raises CancelledError."""
        logger = Mock()
        with patch("webhook_server.test_oracle.analyzer.github_api_call", side_effect=_direct_github_api_call):
            mock_pull_request.create_issue_comment.side_effect = RuntimeError("API unavailable")
            await _post_failure_comment(pull_request=mock_pull_request, logger=logger, log_prefix="[TEST]")
            logger.warning.assert_called_once()

            mock_pull_request.create_issue_comment.side_effect = asyncio.CancelledError()
            with pytest.raises(asyncio.CancelledError):
                await _post_failure_comment(pull_request=mock_pull_request, logger=logger, log_prefix="[TEST]")
