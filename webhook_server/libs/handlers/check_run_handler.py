import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from github.CheckRun import CheckRun
from github.CommitStatus import CommitStatus
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.utils.constants import (
    AUTOMERGE_LABEL_STR,
    BUILD_CONTAINER_STR,
    CAN_BE_MERGED_STR,
    CONVENTIONAL_TITLE_STR,
    FAILURE_STR,
    IN_PROGRESS_STR,
    PYTHON_MODULE_INSTALL_STR,
    QUEUED_STR,
    SECURITY_COMMITTER_IDENTITY_STR,
    SECURITY_SUSPICIOUS_PATHS_STR,
    SUCCESS_STR,
    TOX_STR,
    VERIFIED_LABEL_STR,
)
from webhook_server.utils.github_retry import github_api_call
from webhook_server.utils.helpers import strip_ansi_codes

if TYPE_CHECKING:
    from github.BranchProtection import BranchProtection

    from webhook_server.libs.github_api import GithubWebhook
    from webhook_server.utils.context import WebhookContext


# GitHub caps output.text and output.summary at 65535 UTF-8 *bytes* and silently drops
# whatever exceeds it. Characters are not bytes: a log with a single 3-byte glyph in it
# is 2 bytes over budget at 65534 characters, which is how the end of a failing tox run
# (and the closing code fence) used to disappear. Budget in bytes, with room to spare.
MAX_OUTPUT_BYTES = 65_400
SUMMARY_MAX_BYTES = 6_000
SUMMARY_TAIL_LINES = 25
# One pathological line - a 40000-glyph assertion message, a minified bundle - used to eat the
# whole byte budget and push the failure identification out of the window. No single line may
# take more than this, so the budget is spent on the log instead of on one blob.
MAX_LINE_CHARS = 2_000
ERROR_SECTION_MAX_BYTES = 28_000
# A check's failure is identified by these; everything from the first match onwards is the error
# section, which is kept even when it sits in the middle of the log and would be cut as tail.
ERROR_ANCHORS = (
    "==== FAILURES ====",
    "==== ERRORS ====",
    "Traceback (most recent call last):",
    "error:",
    "Error:",
    "ERROR:",
)
# The list of what failed, sitting near the end of a pytest log. Kept as a block so hundreds of
# failures stay countable instead of being cut in the middle by a byte-counted tail.
FAILED_LIST_ANCHOR = "=== short test summary info ==="
# pytest/pox count line, e.g. '===== 400 failed, 16 passed, 16 warnings in 2.51s ====='
_VERDICT_COUNT_RE = re.compile(r"\b\d+ (?:failed|error|passed)")


def _tail_bytes(text: str, max_bytes: int) -> tuple[str, int]:
    """Keep the last max_bytes of UTF-8 encoded text.

    Args:
        text: Text to truncate.
        max_bytes: UTF-8 byte budget.

    Returns:
        Tuple of (tail of text within budget, number of bytes dropped).
    """
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text, 0
    return encoded[-max_bytes:].decode(errors="ignore"), len(encoded) - max_bytes


def _head_bytes(text: str, max_bytes: int) -> str:
    """Fit text into max_bytes keeping only its head.

    Used for the failure section: its tail necessarily overlaps the tail of the log that is kept
    alongside it, so keeping it here would spend budget printing the same bytes twice.

    Args:
        text: Text to fit.
        max_bytes: UTF-8 byte budget.

    Returns:
        Head of text within budget, with a marker naming how many bytes were cut.
    """
    encoded = text.encode()
    if len(encoded) <= max_bytes:
        return text
    marker = f"\n...[failure section truncated: {len(encoded) - max_bytes} bytes]...\n"
    available = max_bytes - len(marker.encode())
    return encoded[:available].decode(errors="ignore") + marker


def _cap_long_lines(text: str, max_chars: int = MAX_LINE_CHARS) -> str:
    """Collapse individual lines that are longer than max_chars, keeping their ends.

    Args:
        text: Log text.
        max_chars: Longest line to keep intact.

    Returns:
        Text where no line exceeds max_chars, each collapsed line marked in place.
    """
    lines: list[str] = []
    for line in text.splitlines():
        if len(line) > max_chars:
            head = max_chars // 2
            tail = max_chars - head
            lines.append(f"{line[:head]}...[line {len(line)} chars]...{line[-tail:]}")
        else:
            lines.append(line)
    return "\n".join(lines)


def _error_start(text: str) -> int | None:
    """Return the character offset of the first failure marker in a log.

    Args:
        text: Log text.

    Returns:
        Offset of the line that starts the failure section, or None when no marker is present.
    """
    offset = 0
    for line in text.splitlines(keepends=True):
        if any(anchor in line for anchor in ERROR_ANCHORS):
            return offset
        offset += len(line)
    return None


def _failed_list_start(text: str) -> int | None:
    """Return the character offset of the last 'short test summary info' block, if any.

    Args:
        text: Log text.

    Returns:
        Offset of the block that lists every failed test, or None when the log has no such block.
    """
    offset = 0
    start: int | None = None
    for line in text.splitlines(keepends=True):
        if FAILED_LIST_ANCHOR in line:
            start = offset
        offset += len(line)
    return start


def _error_section(text: str, max_bytes: int) -> str:
    """Extract the part of a log that identifies the failure.

    Args:
        text: Log text.
        max_bytes: Byte budget for the extracted section.

    Returns:
        Text from the first failure anchor to the end of the log, head-capped, or "" when the
        log contains no recognised failure marker. When the failure section is itself larger than
        the budget only its head survives - its tail is the end of the log, which the caller keeps
        anyway - and the cut is named in place.
    """
    start = _error_start(text)
    if start is None:
        return ""
    return _head_bytes(text[start:], max_bytes)


class CheckRunOutput(TypedDict, total=False):
    """TypedDict for check run output parameter."""

    title: str
    summary: str
    text: NotRequired[str | None]


class CheckRunHandler:
    def __init__(self, github_webhook: GithubWebhook, owners_file_handler: OwnersFileHandler | None = None):
        self.github_webhook = github_webhook
        self.ctx: WebhookContext | None = github_webhook.ctx
        self.owners_file_handler = owners_file_handler
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self._repository_private: bool | None = None
        # Branch protection caches are keyed by base branch ref: one handler instance can evaluate
        # several pull requests (e.g. the post-merge pass), and each base branch has its own rules
        self._branch_required_status_checks: dict[str, list[str]] = {}
        self._all_required_status_checks: dict[str, list[str]] = {}
        self._branch_protection: dict[str, BranchProtection] = {}
        if isinstance(self.owners_file_handler, OwnersFileHandler):
            self.labels_handler = LabelsHandler(
                github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
            )

    async def process_pull_request_check_run_webhook_data(self, pull_request: PullRequest | None = None) -> bool:
        """Return True if check_if_can_be_merged need to run"""
        if self.ctx:
            self.ctx.start_step("check_run_handler")

        _check_run: dict[str, Any] = self.hook_data["check_run"]
        check_run_name: str = _check_run["name"]

        if self.hook_data.get("action", "") != "completed":
            self.logger.debug(
                f"{self.log_prefix} check run {check_run_name} action is "
                f"{self.hook_data.get('action', 'N/A')} and not completed, skipping"
            )
            if self.ctx:
                self.ctx.complete_step("check_run_handler")
            return False

        check_run_status: str = _check_run["status"]
        check_run_conclusion: str = _check_run["conclusion"]
        self.logger.debug(
            f"{self.log_prefix} processing check_run - Name: {check_run_name} "
            f"Status: {check_run_status} Conclusion: {check_run_conclusion}"
        )

        if check_run_name == CAN_BE_MERGED_STR:
            if getattr(self, "labels_handler", None) and pull_request and check_run_conclusion == SUCCESS_STR:
                if await self.labels_handler.label_exists_in_pull_request(
                    label=AUTOMERGE_LABEL_STR, pull_request=pull_request
                ):
                    try:
                        await github_api_call(
                            pull_request.merge, merge_method="SQUASH", logger=self.logger, log_prefix=self.log_prefix
                        )
                        self.logger.info(
                            f"{self.log_prefix} Successfully auto-merged pull request #{pull_request.number}"
                        )
                        if self.ctx:
                            self.ctx.complete_step("check_run_handler")
                        return False
                    except Exception as ex:
                        self.logger.error(
                            f"{self.log_prefix} Failed to auto-merge pull request #{pull_request.number}: {ex}"
                        )
                        if self.ctx:
                            self.ctx.complete_step("check_run_handler")
                        return True

            else:
                self.logger.debug(f"{self.log_prefix} check run is {CAN_BE_MERGED_STR}, skipping")
                if self.ctx:
                    self.ctx.complete_step("check_run_handler")
                return False

        if self.ctx:
            self.ctx.complete_step("check_run_handler")
        return True

    async def set_check_queued(self, name: str, output: CheckRunOutput | None = None) -> None:
        """Set check run to queued status.

        Generic method for setting any check run (built-in or custom) to queued status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
            output: Optional output dictionary with title, summary, and text fields
        """
        await self.set_check_run_status(check_run=name, status=QUEUED_STR, output=output)

    async def set_check_in_progress(self, name: str) -> None:
        """Set check run to in_progress status.

        Generic method for setting any check run (built-in or custom) to in_progress status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
        """
        await self.set_check_run_status(check_run=name, status=IN_PROGRESS_STR)

    async def set_check_success(self, name: str, output: CheckRunOutput | None = None) -> None:
        """Set check run to success.

        Generic method for setting any check run (built-in or custom) to success status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
            output: Optional output dictionary with title, summary, and text fields
        """
        await self.set_check_run_status(check_run=name, conclusion=SUCCESS_STR, output=output)

    async def set_check_failure(self, name: str, output: CheckRunOutput | None = None) -> None:
        """Set check run to failure.

        Generic method for setting any check run (built-in or custom) to failure status.

        Args:
            name: The name of the check run (e.g., TOX_STR, PRE_COMMIT_STR, or custom check name)
            output: Optional output dictionary with title, summary, and text fields
        """
        await self.set_check_run_status(check_run=name, conclusion=FAILURE_STR, output=output)

    async def set_check_run_status(
        self,
        check_run: str,
        status: str = "",
        conclusion: str = "",
        output: CheckRunOutput | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {"name": check_run, "head_sha": self.github_webhook.last_commit.sha}

        if status:
            kwargs["status"] = status

        if conclusion:
            kwargs["conclusion"] = conclusion
            kwargs["status"] = "completed"  # Explicitly set status when conclusion is provided

        if output:
            kwargs["output"] = output

        msg: str = f"{self.log_prefix} check run {check_run} status: {status or conclusion}"

        try:
            self.logger.debug(
                f"{self.log_prefix} Setting check run for {check_run}, status={status}, conclusion={conclusion}"
            )
            await github_api_call(
                self.github_webhook.repository_by_github_app.create_check_run,
                **kwargs,
                logger=self.logger,
                log_prefix=self.log_prefix,
            )
            if conclusion in (SUCCESS_STR, IN_PROGRESS_STR):
                self.logger.info(msg)
            return

        except asyncio.CancelledError:
            raise  # Always re-raise CancelledError
        except Exception:
            self.logger.exception(f"{self.log_prefix} Failed to set check run status for {check_run}")
            kwargs["conclusion"] = FAILURE_STR
            kwargs["status"] = "completed"
            # GitHub rejects a payload it does not like (most often oversized output). Retrying it
            # verbatim fails the same way and leaves the check stuck in progress with nothing
            # reported, so drop the detail and at least report the failure.
            rejected_output = kwargs.pop("output", None)
            if isinstance(rejected_output, dict):
                kwargs["output"] = {
                    "title": rejected_output.get("title", check_run),
                    "summary": rejected_output.get("summary", ""),
                }
            try:
                await github_api_call(
                    self.github_webhook.repository_by_github_app.create_check_run,
                    **kwargs,
                    logger=self.logger,
                    log_prefix=self.log_prefix,
                )
            except asyncio.CancelledError:
                raise  # Always re-raise CancelledError
            except Exception:
                self.logger.exception(f"{self.log_prefix} Failed to report check run {check_run} as failed")

    def _redact_output(self, text: str) -> str:
        """Replace sensitive tokens, passwords, and credentials with *****."""
        _hased_str = "*****"

        pypi_config = self.github_webhook.pypi
        if isinstance(pypi_config, dict):
            pypi_token = pypi_config.get("token")
            if pypi_token:
                text = text.replace(pypi_token, _hased_str)

        if getattr(self.github_webhook, "container_repository_username", None):
            text = text.replace(self.github_webhook.container_repository_username, _hased_str)

        if getattr(self.github_webhook, "container_repository_password", None):
            text = text.replace(self.github_webhook.container_repository_password, _hased_str)

        if self.github_webhook.token:
            text = text.replace(self.github_webhook.token, _hased_str)

        return text

    def get_check_run_text(self, err: str, out: str) -> str:
        """Render the check log for output.text, within GitHub's 65535-byte limit.

        The failure comes first and everything after it follows, so a reader never has to scroll
        past thousands of passing lines to find what broke.

        Args:
            err: Command stderr.
            out: Command stdout.

        Returns:
            A fenced log excerpt no larger than GitHub accepts.
        """
        # Strip ANSI escape codes from output to prevent scrambled characters in GitHub UI.
        # Redact BEFORE truncation to prevent partial secret leaks at truncation boundaries.
        body = f"{strip_ansi_codes(err)}\n\n{strip_ansi_codes(out)}".strip()
        body = _cap_long_lines(self._redact_output(body))
        total_bytes = len(body.encode())

        if total_bytes <= MAX_OUTPUT_BYTES:
            return self._redact_output(f"```\n{body}\n```")

        # Log the full redacted output BEFORE truncation so server logs preserve the complete
        # build output for debugging (the body is already redacted at this point).
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                f"{self.log_prefix} Full check run output ({total_bytes} bytes, GitHub limit is "
                f"{MAX_OUTPUT_BYTES}):\n```\n{body}\n```"
            )

        error_start = _error_start(body)
        if error_start is not None:
            from_failure = body[error_start:]
            from_failure_bytes = len(from_failure.encode())
            if from_failure_bytes <= MAX_OUTPUT_BYTES:
                # Everything from the failure onwards fits: anchor the window there instead of at
                # the end of the log, so the failure is the first thing on the page.
                dropped = total_bytes - from_failure_bytes
                self.logger.info(
                    f"{self.log_prefix} Check run output {total_bytes} bytes exceeds the GitHub limit; "
                    f"kept everything from the failure onwards ({from_failure_bytes} bytes), dropped {dropped}"
                )
                marker = f"...[{dropped} bytes truncated from the start of the output]..."
                return self._redact_output(f"```\n{marker}\n\n{from_failure}\n```")

        # Over budget. Split the window between the two things a reader needs, in this order:
        # 1. the failed-test list (every failed test named once), kept as a whole block
        # 2. the failure section head, contiguous from the first marker, taking whatever is left
        # so the budget is always filled instead of posting 28 KB of a 65 KB allowance.
        budget = MAX_OUTPUT_BYTES - 80  # 80 = marker + blank line
        list_block = ""
        tail_start = _failed_list_start(body)
        if tail_start is not None and len(body[tail_start:].encode()) <= budget // 2:
            list_block = body[tail_start:]

        error_part = ""
        remaining = budget - len(list_block.encode())
        if error_start is not None:
            # With a failed-test list kept, the verdict is already on the page, so the failure
            # section may have everything that is left. Without one, leave room for the end of
            # the log and its verdict line.
            error_budget = remaining if list_block else min(ERROR_SECTION_MAX_BYTES, remaining)
            error_part = _head_bytes(body[error_start:], error_budget)
        leftover = remaining - len(error_part.encode())
        kept = ""
        if leftover > 1_000:
            kept, _ = _tail_bytes(body, leftover)

        dropped = total_bytes - len(error_part.encode()) - len(list_block.encode()) - len(kept.encode())
        self.logger.info(
            f"{self.log_prefix} Check run output {total_bytes} bytes exceeds the GitHub limit; kept "
            f"{len(error_part.encode())} bytes of failure section, "
            f"{len(list_block.encode())} bytes of failed-test list and {len(kept.encode())} bytes "
            f"of log tail, dropped {dropped}"
        )

        marker = f"...[{dropped} bytes truncated from the start of the output]..."
        parts = [part for part in (error_part, marker, kept, list_block) if part]
        # The wrapper is added after truncation so the closing fence can never be cut off
        return self._redact_output(f"```\n{chr(10).join(p + chr(10) for p in parts).rstrip()}\n```")

    def get_check_run_summary(self, err: str, out: str) -> str:
        """Render the reason for a check result as a short markdown excerpt.

        GitHub shows output.summary inline in the Checks tab and output.text only after a click
        on 'Details', so a check that only fills in text fails silently as far as a reviewer is
        concerned. The failure section comes first because it identifies the problem; the verdict
        lines come last because they say how bad it was.

        Args:
            err: Command stderr.
            out: Command stdout.

        Returns:
            A fenced excerpt of the failure and the verdict, or "" when both streams are empty.
        """
        body = _cap_long_lines(self._redact_output(f"{strip_ansi_codes(err)}\n{strip_ansi_codes(out)}".strip()))
        if not body:
            return ""

        error_part = _error_section(body, SUMMARY_MAX_BYTES - 500)
        verdict = "\n".join(body.splitlines()[-2:])
        if error_part and verdict not in error_part:
            excerpt = f"{error_part}\n\n{verdict}"
        elif error_part:
            excerpt = error_part
        else:
            excerpt = "\n".join(body.splitlines()[-SUMMARY_TAIL_LINES:])

        excerpt, _ = _tail_bytes(excerpt, SUMMARY_MAX_BYTES)
        # Tildes, not backticks: command output routinely contains ``` and would break the fence
        return f"~~~\n{excerpt}\n~~~"

    def get_check_run_title(self, fallback_title: str, err: str, out: str, *, passed: bool) -> str:
        """Return a one-line heading for output.title.

        GitHub renders output.title directly above the summary, while the check is already named
        after the check itself - repeating that name ('Tox' over 'tox') just pushes the content
        down the page. The heading therefore states the verdict, or the line that identifies the
        failure.

        Args:
            fallback_title: Human name of the check, used when no verdict line is found.
            err: Command stderr.
            out: Command stdout.
            passed: Whether the check succeeded.

        Returns:
            A redacted single line, at most 250 characters.
        """
        if passed:
            return f"{fallback_title} passed"

        body = strip_ansi_codes(f"{out}\n{err}")
        lines = [line.strip() for line in body.splitlines() if line.strip()]
        # The count line says how bad it is ('400 failed, 16 passed') and is the most useful
        # heading for a run with more failures than the log window can show.
        for line in reversed(lines):
            if _VERDICT_COUNT_RE.search(line) and ("failed" in line or "error" in line):
                return self._redact_output(line.strip("= "))[:250]
        for line in reversed(lines):
            if line.startswith(("FAILED ", "ERROR ")) or line.lower().startswith("error:"):
                return self._redact_output(line)[:250]
        return f"{fallback_title} failed"

    async def is_check_run_in_progress(self, check_run: str) -> bool:
        if self.github_webhook.last_commit:
            runs = await github_api_call(
                lambda: list(self.github_webhook.last_commit.get_check_runs()),
                logger=self.logger,
                log_prefix=self.log_prefix,
            )
            for run in runs:
                if run.name == check_run and run.status == IN_PROGRESS_STR:
                    self.logger.debug(f"{self.log_prefix} Check run {check_run} is in progress.")
                    return True
        return False

    async def required_check_failed_or_no_status(
        self,
        pull_request: PullRequest,
        last_commit_check_runs: list[CheckRun],
        last_commit_statuses: list[CommitStatus],
        check_runs_in_progress: list[str],
    ) -> str:
        failed_check_runs: list[str] = []

        # Find required checks that are missing entirely from check runs list
        required_checks = set(await self.all_required_status_checks(pull_request=pull_request))
        existing_check_names = {cr.name for cr in last_commit_check_runs}
        missing_required_checks = required_checks - existing_check_names

        # Add missing checks to no_status list (these haven't been created yet)
        no_status_check_runs: list[str] = list(missing_required_checks)

        # Add commit statuses (legacy API) to existing checks
        status_check_names = {status.context for status in last_commit_statuses}
        existing_check_names = existing_check_names | status_check_names

        # Recalculate missing checks after adding statuses
        missing_required_checks = required_checks - existing_check_names
        no_status_check_runs = list(missing_required_checks)

        # Check commit statuses for failures/pending
        self.logger.debug(f"{self.log_prefix} Status details: {[(s.context, s.state) for s in last_commit_statuses]}")

        # Filter to latest status per context (highest ID = most recent)
        status_by_context: dict[str, CommitStatus] = {}
        for status in last_commit_statuses:
            if status.context not in status_by_context or status.id > status_by_context[status.context].id:
                status_by_context[status.context] = status

        latest_statuses = list(status_by_context.values())
        self.logger.debug(
            f"{self.log_prefix} Filtered {len(last_commit_statuses)} statuses to {len(latest_statuses)} latest statuses"
        )

        for status in latest_statuses:
            if status.context not in required_checks:
                continue  # Not a required check

            if status.state == "success":
                continue  # Passed

            if status.state == "pending":
                # Skip if already marked as in-progress (to avoid duplicate reporting)
                if status.context in check_runs_in_progress:
                    continue
                if status.context not in no_status_check_runs:
                    no_status_check_runs.append(status.context)
            elif status.state in ("failure", "error"):
                if status.context not in failed_check_runs:
                    failed_check_runs.append(status.context)

        for check_run in last_commit_check_runs:
            # Skip check runs that have a corresponding success status
            status_contexts = {status.context for status in latest_statuses if status.state == "success"}
            if check_run.name in status_contexts:
                continue

            if (
                check_run.name == CAN_BE_MERGED_STR
                or check_run.conclusion == SUCCESS_STR
                or check_run.name not in await self.all_required_status_checks(pull_request=pull_request)
            ):
                continue

            if check_run.conclusion is None:
                if check_run.name not in no_status_check_runs:
                    no_status_check_runs.append(check_run.name)

            else:
                if check_run.name not in failed_check_runs:
                    failed_check_runs.append(check_run.name)

        self.logger.debug(f"{self.log_prefix} no_status_check_runs after processing check runs: {no_status_check_runs}")
        self.logger.debug(f"{self.log_prefix} failed_check_runs after processing check runs: {failed_check_runs}")

        msg = ""

        if failed_check_runs:
            exclude_in_progress = [
                failed_check_run
                for failed_check_run in failed_check_runs
                if failed_check_run not in check_runs_in_progress
            ]
            msg += f"Some check runs failed: {', '.join(exclude_in_progress)}\n"
        self.logger.debug(f"{self.log_prefix} failed_check_runs: {failed_check_runs}")

        if no_status_check_runs:
            msg += f"Some check runs not started: {', '.join(no_status_check_runs)}\n"
        self.logger.debug(f"{self.log_prefix} no_status_check_runs: {no_status_check_runs}")

        return msg

    async def all_required_status_checks(self, pull_request: PullRequest) -> list[str]:
        base_ref = await github_api_call(
            lambda: pull_request.base.ref,
            logger=self.logger,
            log_prefix=self.log_prefix,
        )
        # Cache to avoid repeated processing
        if base_ref in self._all_required_status_checks:
            return self._all_required_status_checks[base_ref]

        all_required_status_checks: list[str] = []
        branch_required_status_checks = await self.get_branch_required_status_checks(pull_request=pull_request)

        if self.github_webhook.tox:
            all_required_status_checks.append(TOX_STR)

        if self.github_webhook.verified_job:
            all_required_status_checks.append(VERIFIED_LABEL_STR)

        if self.github_webhook.build_and_push_container:
            all_required_status_checks.append(BUILD_CONTAINER_STR)

        if self.github_webhook.pypi:
            all_required_status_checks.append(PYTHON_MODULE_INSTALL_STR)

        if self.github_webhook.conventional_title:
            all_required_status_checks.append(CONVENTIONAL_TITLE_STR)

        # Add mandatory custom checks only (non-mandatory checks still run but don't affect can-be-merged)
        # Note: custom checks are validated in GithubWebhook._validate_custom_check_runs()
        # so name is guaranteed to exist
        for custom_check in self.github_webhook.custom_check_runs:
            if custom_check.get("mandatory", True):
                check_name = custom_check["name"]
                all_required_status_checks.append(check_name)

        # Add mandatory security checks
        if self.github_webhook.security_mandatory:
            if self.github_webhook.security_suspicious_paths:
                all_required_status_checks.append(SECURITY_SUSPICIOUS_PATHS_STR)

            if self.github_webhook.security_committer_identity_check:
                all_required_status_checks.append(SECURITY_COMMITTER_IDENTITY_STR)

        # Use ordered deduplication to combine branch and config checks without duplicates
        _all_required_status_checks = list(dict.fromkeys(branch_required_status_checks + all_required_status_checks))
        self.logger.debug(f"{self.log_prefix} All required status checks: {_all_required_status_checks}")
        self._all_required_status_checks[base_ref] = _all_required_status_checks
        return _all_required_status_checks

    async def _fetch_branch_protection(self, pull_request: PullRequest) -> BranchProtection:
        """Fetch branch protection of the pull request base branch.

        The result is cached on the handler instance, keyed by base branch ref so pull requests with
        different base branches never share another branch's rules. Handler instances are created per
        webhook event, so the cache never outlives a single event and a repo admin changing branch
        protection takes effect on the next event. Failures are never cached, so a transient error
        cannot turn into a permanently empty answer for the rest of the event.

        Args:
            pull_request: The GitHub pull request whose base branch protection is needed.

        Returns:
            The branch protection object.

        Raises:
            GithubException: GitHub refused the read - no protection configured, or the token lacks
                the permissions required to read it.
            Exception: Any other failure while reading branch protection.

        Callers must not treat a read failure as 'no protection': a repository whose protection
        cannot be read has unknown required status checks, and merge eligibility must fail closed.
        """
        base_ref = ""
        try:
            base_ref = await github_api_call(
                lambda: pull_request.base.ref,
                logger=self.logger,
                log_prefix=self.log_prefix,
            )
            cached_protection = self._branch_protection.get(base_ref)
            if cached_protection is not None:
                return cached_protection

            pull_request_branch = await github_api_call(
                self.repository.get_branch,
                base_ref,
                logger=self.logger,
                log_prefix=self.log_prefix,
            )
            branch_protection = await github_api_call(
                pull_request_branch.get_protection, logger=self.logger, log_prefix=self.log_prefix
            )
        except Exception:
            self.logger.exception(f"{self.log_prefix} Failed to get branch protection of {base_ref or 'base branch'}")
            raise

        self._branch_protection[base_ref] = branch_protection
        return branch_protection

    async def branch_protection_requires_up_to_date(self, pull_request: PullRequest) -> bool | None:
        """Report whether branch protection requires branches to be up to date before merging.

        This is GitHub's own 'strict' required status checks setting, read from GitHub on every call
        (cached only for the lifetime of the handler instance, i.e. one webhook event) so a repo admin
        toggling it is picked up immediately. Deliberately not read from config: stale config would fail
        the check for pull requests GitHub would happily merge.

        Unlike get_branch_required_status_checks, private repositories are not skipped - the 'strict'
        setting gates merging regardless of repository visibility.

        Unlike get_branch_required_status_checks, an unreadable branch protection returns None instead
        of raising: this gate is additive, so an unknown setting must not fail a pull request that
        every other requirement already accepts.

        Args:
            pull_request: The GitHub pull request whose base branch protection is needed.

        Returns:
            True if branches must be up to date before merging, False if they must not, None if the
            setting could not be read. Callers must skip the check on None instead of assuming a value.
        """
        try:
            branch_protection = await self._fetch_branch_protection(pull_request=pull_request)
        except Exception as ex:
            self.logger.info(
                f"{self.log_prefix} Branch protection up-to-date requirement unknown "
                f"({type(ex).__name__}), skipping behind check"
            )
            return None

        required_status_checks = await github_api_call(
            lambda: branch_protection.required_status_checks,
            logger=self.logger,
            log_prefix=self.log_prefix,
        )
        if required_status_checks is None:
            # No required status checks => no check run gates the merge button
            self.logger.debug(f"{self.log_prefix} Branch protection has no required status checks")
            return False

        strict = await github_api_call(
            lambda: bool(required_status_checks.strict),
            logger=self.logger,
            log_prefix=self.log_prefix,
        )
        self.logger.debug(f"{self.log_prefix} Branch protection required status checks strict: {strict}")
        return strict

    async def get_branch_required_status_checks(self, pull_request: PullRequest) -> list[str]:
        # Check if private repo first (cache to avoid repeated API calls)
        if self._repository_private is None:
            self._repository_private = await github_api_call(
                lambda: self.repository.private, logger=self.logger, log_prefix=self.log_prefix
            )

        if self._repository_private:
            self.logger.info(
                f"{self.log_prefix} Repository is private, skipping getting branch protection required status checks"
            )
            return []

        # Cache branch protection settings in instance variable to avoid repeated API calls
        base_ref = await github_api_call(
            lambda: pull_request.base.ref,
            logger=self.logger,
            log_prefix=self.log_prefix,
        )
        if base_ref in self._branch_required_status_checks:
            return self._branch_required_status_checks[base_ref]

        # A failed read propagates on purpose: unknown required checks must fail closed, otherwise a
        # permissions or connectivity error would let check_if_can_be_merged pass with checks missing
        branch_protection = await self._fetch_branch_protection(pull_request=pull_request)

        required_status_checks = await github_api_call(
            lambda: branch_protection.required_status_checks,
            logger=self.logger,
            log_prefix=self.log_prefix,
        )
        if required_status_checks is None:
            # GitHub confirms there are no required status checks on this branch - a known state, not
            # a read failure, so there is nothing to require
            self.logger.debug(
                f"{self.log_prefix} Branch protection has no required status checks, no branch checks required"
            )
            self._branch_required_status_checks[base_ref] = []
            return self._branch_required_status_checks[base_ref]

        branch_required_status_checks = await github_api_call(
            lambda: required_status_checks.contexts, logger=self.logger, log_prefix=self.log_prefix
        )
        self.logger.debug(f"{self.log_prefix} branch_required_status_checks: {branch_required_status_checks}")
        self._branch_required_status_checks[base_ref] = branch_required_status_checks
        return self._branch_required_status_checks[base_ref]

    async def required_check_in_progress(
        self,
        pull_request: PullRequest,
        last_commit_check_runs: list[CheckRun],
    ) -> tuple[str, list[str]]:
        self.logger.debug(f"{self.log_prefix} Check if any required check runs in progress.")

        check_runs_in_progress = [
            check_run.name
            for check_run in last_commit_check_runs
            if check_run.status == IN_PROGRESS_STR
            and check_run.name != CAN_BE_MERGED_STR
            and check_run.name in await self.all_required_status_checks(pull_request=pull_request)
        ]

        # Note: Status API doesn't have an "in_progress" state - only pending (queued),
        # success, failure, and error. We only check Check Runs for in-progress status.

        if check_runs_in_progress:
            self.logger.debug(
                f"{self.log_prefix} Some required check runs in progress {check_runs_in_progress}, "
                f"skipping check if {CAN_BE_MERGED_STR}."
            )
            return f"Some required check runs in progress {', '.join(check_runs_in_progress)}\n", check_runs_in_progress
        return "", []
