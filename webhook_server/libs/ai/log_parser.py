"""Log parsing utilities for extracting key information from test output.

Supports various test frameworks: pytest, tox, pre-commit.
Extracts: stack traces, error messages, file paths, line numbers.
Optimized for AI analysis with noise reduction and size limits.
"""

import re
from dataclasses import dataclass


@dataclass
class ParsedLog:
    """Structured representation of parsed test output."""

    summary: str  # Brief summary of the failure
    error_messages: list[str]  # Extracted error messages
    stack_traces: list[str]  # Stack traces
    file_paths: list[str]  # Files mentioned in errors
    line_numbers: dict[str, list[int]]  # File -> line numbers mapping
    test_names: list[str]  # Failed test names
    raw_excerpt: str  # Most relevant raw output excerpt
    framework: str  # Detected framework (pytest, tox, pre-commit, unknown)
    total_lines: int  # Total lines in original output
    truncated: bool  # Whether output was truncated


class LogParser:
    """Parse test output and extract key information for AI analysis."""

    # Regular expressions for common patterns
    PYTEST_ERROR_RE = re.compile(r"^E\s+(.+)$", re.MULTILINE)
    PYTEST_FAIL_RE = re.compile(r"^FAILED\s+(.+?)\s+-\s+(.+)$", re.MULTILINE)
    FILE_PATH_RE = re.compile(r'File "([^"]+)", line (\d+)')
    PYTHON_TRACEBACK_RE = re.compile(r"Traceback \(most recent call last\):")
    ASSERTION_ERROR_RE = re.compile(r"AssertionError:?\s*(.*)$", re.MULTILINE)
    ERROR_LINE_RE = re.compile(r"^\s*(\w+Error|Exception):?\s*(.*)$", re.MULTILINE)
    PRECOMMIT_FAILED_RE = re.compile(r"^(.+?)\s+Failed$", re.MULTILINE)

    MAX_OUTPUT_SIZE = 5000  # Max chars for Gemini

    @staticmethod
    def parse(test_output: str, framework_hint: str | None = None) -> ParsedLog:
        """Parse test output and extract key information.

        Args:
            test_output: Raw test output/logs
            framework_hint: Hint about the framework (pytest, tox, pre-commit)

        Returns:
            ParsedLog with extracted information
        """
        lines = test_output.split("\n")
        total_lines = len(lines)

        # Detect framework if not provided
        framework = framework_hint or LogParser._detect_framework(test_output)

        # Extract different components
        error_messages = LogParser._extract_error_messages(test_output)
        stack_traces = LogParser._extract_stack_traces(test_output)
        file_paths, line_numbers = LogParser._extract_file_references(test_output)
        test_names = LogParser._extract_test_names(test_output, framework)

        # Create summary
        summary = LogParser._create_summary(
            error_messages=error_messages,
            test_names=test_names,
            framework=framework,
        )

        # Get most relevant excerpt (around errors/failures)
        raw_excerpt = LogParser._extract_relevant_excerpt(
            test_output=test_output,
            error_messages=error_messages,
            stack_traces=stack_traces,
        )

        # Check if truncated
        truncated = len(raw_excerpt) >= LogParser.MAX_OUTPUT_SIZE

        return ParsedLog(
            summary=summary,
            error_messages=error_messages[:10],  # Limit to first 10
            stack_traces=stack_traces[:5],  # Limit to first 5
            file_paths=file_paths[:20],  # Limit to first 20
            line_numbers=line_numbers,
            test_names=test_names[:10],  # Limit to first 10
            raw_excerpt=raw_excerpt,
            framework=framework,
            total_lines=total_lines,
            truncated=truncated,
        )

    @staticmethod
    def _detect_framework(output: str) -> str:
        """Detect which test framework generated the output.

        Args:
            output: Test output

        Returns:
            Framework name: pytest, tox, pre-commit, or unknown
        """
        if "pytest" in output.lower() or re.search(r"test_\w+\.py::", output):
            return "pytest"
        if "tox" in output.lower() or re.search(r"py\d+:\s+(commands|FAIL)", output):
            return "tox"
        if "pre-commit" in output.lower() or "hook id:" in output.lower():
            return "pre-commit"
        return "unknown"

    @staticmethod
    def _extract_error_messages(output: str) -> list[str]:
        """Extract error messages from test output.

        Args:
            output: Test output

        Returns:
            List of error messages
        """
        errors = []

        # Pytest-style errors (lines starting with "E ")
        for match in LogParser.PYTEST_ERROR_RE.finditer(output):
            error_text = match.group(1).strip()
            if error_text and len(error_text) > 5:  # Skip very short lines
                errors.append(error_text)

        # Python exceptions
        for match in LogParser.ERROR_LINE_RE.finditer(output):
            error_type = match.group(1)
            error_msg = match.group(2).strip()
            full_error = f"{error_type}: {error_msg}" if error_msg else error_type
            if full_error not in errors:  # Avoid duplicates
                errors.append(full_error)

        # AssertionError messages
        for match in LogParser.ASSERTION_ERROR_RE.finditer(output):
            msg = match.group(1).strip()
            if msg:
                assertion_error = f"AssertionError: {msg}"
                if assertion_error not in errors:
                    errors.append(assertion_error)

        return errors

    @staticmethod
    def _extract_stack_traces(output: str) -> list[str]:
        """Extract Python stack traces from output.

        Args:
            output: Test output

        Returns:
            List of stack trace strings
        """
        traces = []
        lines = output.split("\n")

        i = 0
        while i < len(lines):
            # Look for traceback start
            if LogParser.PYTHON_TRACEBACK_RE.search(lines[i]):
                trace_lines = [lines[i]]
                i += 1

                # Collect traceback lines until we hit a blank line or error line
                while i < len(lines):
                    line = lines[i]
                    if not line.strip():
                        break
                    trace_lines.append(line)
                    i += 1

                    # Stop at error line
                    if LogParser.ERROR_LINE_RE.match(line):
                        break

                if len(trace_lines) > 1:  # Only add if we got more than just the header
                    traces.append("\n".join(trace_lines))
            else:
                i += 1

        return traces

    @staticmethod
    def _extract_file_references(output: str) -> tuple[list[str], dict[str, list[int]]]:
        """Extract file paths and line numbers from output.

        Args:
            output: Test output

        Returns:
            Tuple of (file_paths, line_numbers_dict)
        """
        file_paths = []
        line_numbers: dict[str, list[int]] = {}

        for match in LogParser.FILE_PATH_RE.finditer(output):
            file_path = match.group(1)
            line_num = int(match.group(2))

            if file_path not in file_paths:
                file_paths.append(file_path)

            if file_path not in line_numbers:
                line_numbers[file_path] = []

            if line_num not in line_numbers[file_path]:
                line_numbers[file_path].append(line_num)

        return file_paths, line_numbers

    @staticmethod
    def _extract_test_names(output: str, framework: str) -> list[str]:
        """Extract failed test names from output.

        Args:
            output: Test output
            framework: Detected framework

        Returns:
            List of test names
        """
        test_names = []

        if framework == "pytest":
            # Pytest FAILED lines: "FAILED test_file.py::test_name - AssertionError"
            for match in LogParser.PYTEST_FAIL_RE.finditer(output):
                test_name = match.group(1).strip()
                if test_name not in test_names:
                    test_names.append(test_name)

        elif framework == "pre-commit":
            # Pre-commit hook failures
            for match in LogParser.PRECOMMIT_FAILED_RE.finditer(output):
                hook_name = match.group(1).strip()
                if hook_name not in test_names:
                    test_names.append(hook_name)

        return test_names

    @staticmethod
    def _create_summary(
        error_messages: list[str],
        test_names: list[str],
        framework: str,
    ) -> str:
        """Create a brief summary of the failure.

        Args:
            error_messages: Extracted error messages
            test_names: Failed test names
            framework: Test framework

        Returns:
            Summary string
        """
        parts = []

        if test_names:
            if len(test_names) == 1:
                parts.append(f"Test failed: {test_names[0]}")
            else:
                parts.append(f"{len(test_names)} tests failed")

        if error_messages:
            # Use first error as primary indicator
            first_error = error_messages[0]
            if len(first_error) > 100:
                first_error = first_error[:100] + "..."
            parts.append(f"Error: {first_error}")

        if not parts:
            parts.append(f"{framework} failure (no specific error extracted)")

        return " | ".join(parts)

    @staticmethod
    def _extract_relevant_excerpt(
        test_output: str,
        error_messages: list[str],
        stack_traces: list[str],
    ) -> str:
        """Extract most relevant excerpt from output, focused on errors.

        Args:
            test_output: Full test output
            error_messages: Extracted error messages
            stack_traces: Extracted stack traces

        Returns:
            Relevant excerpt (max MAX_OUTPUT_SIZE chars)
        """
        # Priority: stack traces > error context > full output

        if stack_traces:
            # Use stack traces (most informative)
            excerpt = "\n\n".join(stack_traces[:3])  # First 3 stack traces
        elif error_messages:
            # Find context around first error
            excerpt = LogParser._get_context_around_error(test_output, error_messages[0])
        else:
            # No specific errors found, use tail of output
            lines = test_output.split("\n")
            # Last 50 lines usually contain the failure
            excerpt = "\n".join(lines[-50:])

        # Truncate to max size
        if len(excerpt) > LogParser.MAX_OUTPUT_SIZE:
            excerpt = excerpt[: LogParser.MAX_OUTPUT_SIZE] + "\n... [truncated]"

        return excerpt

    @staticmethod
    def _get_context_around_error(output: str, error_text: str, context_lines: int = 10) -> str:
        """Get lines around an error message for context.

        Args:
            output: Full output
            error_text: Error text to find
            context_lines: Number of lines before/after to include

        Returns:
            Context excerpt
        """
        lines = output.split("\n")

        # Find line with error
        error_line_idx = -1
        for i, line in enumerate(lines):
            if error_text in line:
                error_line_idx = i
                break

        if error_line_idx == -1:
            # Error not found, return tail
            return "\n".join(lines[-context_lines * 2 :])

        # Get context
        start = max(0, error_line_idx - context_lines)
        end = min(len(lines), error_line_idx + context_lines + 1)

        return "\n".join(lines[start:end])

    @staticmethod
    def format_for_ai(parsed_log: ParsedLog) -> str:
        """Format parsed log for AI consumption (optimized prompt).

        Args:
            parsed_log: Parsed log data

        Returns:
            Formatted string for AI analysis
        """
        parts = [
            f"Framework: {parsed_log.framework}",
            f"Summary: {parsed_log.summary}",
            "",
        ]

        if parsed_log.test_names:
            parts.append("Failed Tests:")
            for test in parsed_log.test_names:
                parts.append(f"  - {test}")
            parts.append("")

        if parsed_log.error_messages:
            parts.append("Error Messages:")
            for error in parsed_log.error_messages[:5]:  # Top 5
                parts.append(f"  - {error}")
            parts.append("")

        if parsed_log.file_paths:
            parts.append("Files Involved:")
            for file_path in parsed_log.file_paths[:10]:  # Top 10
                line_nums = parsed_log.line_numbers.get(file_path, [])
                if line_nums:
                    parts.append(f"  - {file_path} (lines: {', '.join(map(str, line_nums[:5]))})")
                else:
                    parts.append(f"  - {file_path}")
            parts.append("")

        if parsed_log.stack_traces:
            parts.append("Stack Trace (most recent):")
            parts.append(parsed_log.stack_traces[0])  # Most recent
            parts.append("")

        if parsed_log.truncated:
            parts.append(f"Note: Output truncated (original: {parsed_log.total_lines} lines)")

        return "\n".join(parts)
