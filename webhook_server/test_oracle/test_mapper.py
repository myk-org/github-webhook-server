"""Static analysis to map changed PR files to candidate test files."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from webhook_server.test_oracle.models import TestMapping
from webhook_server.utils.helpers import get_logger_with_params

DEFAULT_TEST_PATTERNS: tuple[str, ...] = (
    "tests/**/*.py",
    "**/test_*.py",
    "**/*_test.py",
    "**/*.test.js",
    "**/*.test.ts",
    "**/*.test.jsx",
    "**/*.test.tsx",
    "**/*.spec.js",
    "**/*.spec.ts",
    "**/*.spec.jsx",
    "**/*.spec.tsx",
    "**/*_test.go",
    "**/*_spec.rb",
    "**/*Test.java",
    "**/*Tests.java",
    "**/*Tests.cs",
)
MAX_TEST_FILE_CHARS: int = 25_000
MAX_TOTAL_TEST_CHARS: int = 120_000

# Test infrastructure files that should not be treated as test cases
_EXCLUDED_FILES: frozenset[str] = frozenset({
    "__init__.py",
    "conftest.py",
    "setup.py",
    "conftest.js",
    "jest.config.js",
    "jest.config.ts",
    "vitest.config.ts",
    "karma.conf.js",
})

# Directories skipped during test file discovery
_IGNORED_DIRS: frozenset[str] = frozenset({
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
})

# Config files whose changes may affect all tests
_CONFIG_FILES: frozenset[str] = frozenset({
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "tox.ini",
    "tox.toml",
    "pytest.ini",
    "conftest.py",
    ".env",
    "requirements.txt",
    "requirements-dev.txt",
    "Makefile",
})

# Recognized source file extensions eligible for candidate test mapping
_SOURCE_EXTENSIONS: frozenset[str] = frozenset({
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".java",
    ".rb",
    ".rs",
    ".cs",
    ".php",
    ".sh",
    ".bash",
})


def _validate_test_patterns(patterns: list[str]) -> None:
    """Validate glob patterns, rejecting absolute paths and '..' components."""
    for pattern in patterns:
        if not pattern or pattern.startswith(("/", "\\")) or Path(pattern).is_absolute():
            msg = f"Invalid test pattern (absolute or empty path): {pattern!r}"
            raise ValueError(msg)
        if ".." in re.split(r"[/\\]", pattern):
            msg = f"Invalid test pattern (contains '..' path component): {pattern!r}"
            raise ValueError(msg)


def _is_test_file(path: Path) -> bool:
    """Return True if a file path matches common test file naming conventions."""
    name = path.name
    stem = path.stem
    return (
        name.startswith("test_")
        or name.endswith("_test.py")
        or name.endswith((".test.js", ".test.ts", ".test.jsx", ".test.tsx"))
        or name.endswith((".spec.js", ".spec.ts", ".spec.jsx", ".spec.tsx"))
        or name.endswith("_test.go")
        or name.endswith("_spec.rb")
        or stem.endswith(("Test", "Tests"))
    )


def _strip_source_prefix(parts: tuple[str, ...]) -> list[str]:
    """Strip common source prefixes (src and package directory) and file suffix."""
    parts_list = list(parts)
    if parts_list and parts_list[0] == "src":
        parts_list = parts_list[1:]
        if len(parts_list) > 1:
            parts_list = parts_list[1:]
    if parts_list:
        parts_list[-1] = Path(parts_list[-1]).stem
    return parts_list


def _strip_test_prefix(parts: tuple[str, ...]) -> list[str]:
    """Strip common test root prefixes (tests/test) and file suffix."""
    parts_list = list(parts)
    if parts_list and parts_list[0] in ("tests", "test"):
        parts_list = parts_list[1:]
    if parts_list:
        parts_list[-1] = Path(parts_list[-1]).stem
    return parts_list


def prioritize_candidate_tests(mappings: list[TestMapping]) -> list[str]:
    """Order candidate test files with directly mapped tests before broad config-file matches."""
    direct: set[str] = set()
    broad: set[str] = set()
    for mapping in mappings:
        if Path(mapping.source_file).name in _CONFIG_FILES:
            broad.update(mapping.candidate_tests)
        else:
            direct.update(mapping.candidate_tests)
    return sorted(direct) + sorted(broad - direct)


class TestMapper:
    """Maps changed source files to candidate test files using static analysis."""

    def __init__(
        self,
        repo_path: str,
        test_patterns: list[str] | None = None,
        logger: logging.Logger | None = None,
        log_prefix: str = "",
    ) -> None:
        """Initialize with repository path and validated test glob patterns.

        Args:
            repo_path: Path to the repository root.
            test_patterns: Glob patterns for test files. Defaults to common multi-language patterns.
            logger: Optional shared logger instance. Defaults to the webhook server logger.
            log_prefix: Optional prefix prepended to log messages.

        Raises:
            ValueError: If any pattern is absolute or contains '..' path components.
        """
        self._repo: Path = Path(repo_path).resolve()
        patterns = list(test_patterns) if test_patterns else list(DEFAULT_TEST_PATTERNS)
        _validate_test_patterns(patterns)
        self._patterns: list[str] = patterns
        self._test_files: list[str] | None = None
        self._logger: logging.Logger = logger if logger is not None else get_logger_with_params()
        self._log_prefix: str = log_prefix

    def discover_test_files(self) -> list[str]:
        """Discover test files matching configured patterns within the repository root.

        Enforces symlink escape protection so untrusted PR symlinks cannot reference
        files outside the repository.

        Returns:
            Sorted list of POSIX test file paths relative to repo root.
        """
        if self._test_files is not None:
            return self._test_files

        test_files: set[str] = set()
        for pattern in self._patterns:
            for path in self._repo.glob(pattern):
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if not resolved.is_relative_to(self._repo) or not resolved.is_file():
                    continue
                if path.name in _EXCLUDED_FILES:
                    continue
                try:
                    rel_path = path.relative_to(self._repo)
                    resolved_rel = resolved.relative_to(self._repo)
                except ValueError:
                    continue
                if any(part in _IGNORED_DIRS for part in rel_path.parts) or any(
                    part in _IGNORED_DIRS for part in resolved_rel.parts
                ):
                    continue
                test_files.add(rel_path.as_posix())

        self._test_files = sorted(test_files)
        prefix = f"{self._log_prefix} " if self._log_prefix else ""
        self._logger.debug(f"{prefix}Discovered {len(self._test_files)} test files in {self._repo}")
        return self._test_files

    def map_changed_files(self, changed_files: list[str]) -> list[TestMapping]:
        """Map changed PR files to candidate test files.

        Args:
            changed_files: List of changed file paths from the PR.

        Returns:
            List of TestMapping objects describing candidate tests per changed file.
        """
        test_files = self.discover_test_files()
        test_files_set = set(test_files)
        mappings: list[TestMapping] = []

        for changed_file in changed_files:
            path = Path(changed_file)
            normalized = path.as_posix()

            if path.name in _CONFIG_FILES:
                mappings.append(
                    TestMapping(
                        source_file=changed_file,
                        candidate_tests=list(test_files),
                        mapping_reason="Config file change affects all tests",
                    )
                )
                continue

            if _is_test_file(path):
                mappings.append(
                    TestMapping(
                        source_file=changed_file,
                        candidate_tests=[normalized] if normalized in test_files_set else [],
                        mapping_reason="Changed file is itself a test",
                    )
                )
                continue

            if path.suffix not in _SOURCE_EXTENSIONS:
                mappings.append(
                    TestMapping(
                        source_file=changed_file,
                        candidate_tests=[],
                        mapping_reason="Non-source file; AI will determine relevant tests from diff",
                    )
                )
                continue

            candidates = self._find_candidates(path, test_files)
            reason = (
                "Naming convention and directory structure mapping"
                if candidates
                else "No direct mapping found; AI will determine relevant tests from diff"
            )
            mappings.append(
                TestMapping(
                    source_file=changed_file,
                    candidate_tests=candidates,
                    mapping_reason=reason,
                )
            )

        return mappings

    def _find_candidates(self, source_path: Path, test_files: list[str]) -> list[str]:
        """Find candidate test files for a source file using naming, token, and directory matching."""
        candidates: set[str] = set()
        stem = source_path.stem
        source_module_parts = _strip_source_prefix(source_path.parts)
        # Match stem only as a delimiter-bounded token (e.g. auth -> test_auth_flow, not test_author)
        token_pattern = re.compile(rf"(?:^|[._-]){re.escape(stem)}(?:$|[._-])") if stem else None
        direct_stems = {f"test_{stem}", f"{stem}_test", f"{stem}.test", f"{stem}.spec"} if stem else set()

        for test_file in test_files:
            test_path = Path(test_file)
            test_stem = test_path.stem

            if test_stem in direct_stems:
                candidates.add(test_file)
                continue

            if token_pattern is not None and token_pattern.search(test_stem) is not None:
                candidates.add(test_file)
                continue

            test_module_parts = _strip_test_prefix(test_path.parts)
            if (
                source_module_parts
                and test_module_parts
                and source_module_parts[:-1] == test_module_parts[:-1]
                and test_module_parts[-1] in {f"test_{source_module_parts[-1]}", f"{source_module_parts[-1]}_test"}
            ):
                candidates.add(test_file)

        return sorted(candidates)

    def get_test_file_contents(
        self,
        test_files: list[str],
        *,
        max_file_chars: int = MAX_TEST_FILE_CHARS,
        max_total_chars: int = MAX_TOTAL_TEST_CHARS,
    ) -> dict[str, str]:
        """Read candidate test file contents with symlink escape guards and char budgets.

        Args:
            test_files: List of test file paths relative to repo root.
            max_file_chars: Maximum characters to include per file.
            max_total_chars: Maximum total characters across all returned files.

        Returns:
            Dict mapping file path to file contents (truncated with notice when over budget).
        """
        contents: dict[str, str] = {}
        total_chars = 0
        prefix = f"{self._log_prefix} " if self._log_prefix else ""

        for test_file in test_files:
            if total_chars >= max_total_chars:
                self._logger.info(
                    f"{prefix}Reached total test content budget ({max_total_chars} chars); "
                    "skipping remaining test files"
                )
                break

            if Path(test_file).is_absolute():
                self._logger.warning(f"{prefix}Skipping absolute test file path: {test_file}")
                continue

            try:
                full_path = (self._repo / test_file).resolve()
            except OSError:
                continue

            if not full_path.is_relative_to(self._repo) or not full_path.is_file():
                self._logger.warning(f"{prefix}Skipping invalid or out-of-repo test file: {test_file}")
                continue

            remaining = max_total_chars - total_chars
            limit = min(max_file_chars, remaining)
            if limit <= 0:
                break

            try:
                with full_path.open(encoding="utf-8", errors="replace") as handle:
                    raw_content = handle.read(limit + 1)
            except OSError:
                self._logger.warning(f"{prefix}Failed to read test file: {test_file}")
                continue

            if len(raw_content) > limit:
                content = f"{raw_content[:limit]}\n... [truncated at {limit} chars]"
            else:
                content = raw_content

            contents[test_file] = content
            total_chars += len(content)

        return contents
