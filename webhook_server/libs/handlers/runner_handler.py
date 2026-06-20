import asyncio
import contextlib
import os
import re
import shlex
import shutil
from asyncio import Task
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

import shortuuid
from github import GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.ai_cli import call_ai, get_ai_config
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler, CheckRunOutput
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.utils import helpers as helpers_module
from webhook_server.utils.constants import (
    AI_RESOLVED_CONFLICTS_LABEL,
    BUILD_CONTAINER_STR,
    CHERRY_PICKED_LABEL,
    CONVENTIONAL_TITLE_STR,
    GITHUB_WEB_FLOW_LOGIN,
    GITHUB_WEB_FLOW_USER_ID,
    PRE_COMMIT_STR,
    PREK_STR,
    PYTHON_MODULE_INSTALL_STR,
    SECURITY_COMMITTER_IDENTITY_STR,
    SECURITY_SUSPICIOUS_PATHS_STR,
    TOX_STR,
)
from webhook_server.utils.github_repository_settings import get_repository_github_app_token
from webhook_server.utils.github_retry import github_api_call
from webhook_server.utils.helpers import _redact_secrets, run_command
from webhook_server.utils.notification_utils import send_slack_message

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


@dataclass(frozen=True, slots=True)
class CheckConfig:
    """Configuration for a check run.

    Attributes:
        name: The name of the check run (e.g., "tox", "pre-commit", or custom check name).
        command: The command template to execute. Can contain {worktree_path} placeholder.
        title: The display title for the check run output.
        use_cwd: If True, execute command with cwd set to worktree_path.
                 If False, command should include worktree_path in args.
    """

    name: str
    command: str
    title: str
    use_cwd: bool = False


def _count_files_changed(stat_output: str) -> int:
    """Count files changed from git diff --stat output.

    Parses the summary line (e.g., '2 files changed, 15 insertions(+)').
    Returns 0 if the output is empty or unparseable.
    """
    for line in reversed(stat_output.strip().splitlines()):
        match = re.match(r"\s*(\d+) files? changed", line)
        if match:
            return int(match.group(1))
    return 0


class RunnerHandler:
    def __init__(self, github_webhook: "GithubWebhook", owners_file_handler: OwnersFileHandler | None = None):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler or OwnersFileHandler(github_webhook=self.github_webhook)
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository

        self.check_run_handler = CheckRunHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )

    @contextlib.asynccontextmanager
    async def _checkout_worktree(
        self,
        pull_request: PullRequest | None = None,
        is_merged: bool = False,
        checkout: str = "",
        tag_name: str = "",
        skip_merge: bool = False,
    ) -> AsyncGenerator[tuple[bool, str, str, str]]:
        """Create worktree from existing clone for handler operations.

        Uses centralized clone from github_webhook.clone_repo_dir and creates
        a worktree for isolated checkout operations. No cloning happens here.

        Args:
            pull_request: Pull request object
            is_merged: Whether PR is merged
            checkout: Specific branch/commit to checkout
            tag_name: Tag name to checkout
            skip_merge: Skip merging base branch into worktree (used by cherry-pick
                which manages its own branch setup)

        Yields:
            tuple: (success: bool, worktree_path: str, stdout: str, stderr: str)
        """
        pr_number: int | None = None
        base_ref: str | None = None
        if pull_request:
            pr_number = await github_api_call(
                lambda: pull_request.number, logger=self.logger, log_prefix=self.log_prefix
            )
            base_ref = await github_api_call(
                lambda: pull_request.base.ref, logger=self.logger, log_prefix=self.log_prefix
            )

        # Determine what to checkout
        checkout_target = ""
        if checkout:
            checkout_target = checkout
        elif tag_name:
            checkout_target = tag_name
        elif is_merged and pull_request and base_ref is not None:
            checkout_target = base_ref
        elif pull_request and pr_number is not None:
            checkout_target = f"origin/pr/{pr_number}"
        else:
            raise RuntimeError(
                f"{self.log_prefix} Unable to determine checkout target: "
                "no checkout/tag_name provided and pull_request is missing."
            )

        # Use centralized clone
        repo_dir = self.github_webhook.clone_repo_dir
        self.logger.debug(f"{self.log_prefix} Creating worktree from {repo_dir} with checkout: {checkout_target}")

        # Check if checkout_target is already checked out in main clone
        # This prevents "already used by worktree" error when target branch matches current branch
        rc, current_branch, _ = await run_command(
            command=f"git -C {repo_dir} rev-parse --abbrev-ref HEAD",
            log_prefix=self.log_prefix,
            mask_sensitive=self.github_webhook.mask_sensitive,
        )

        if rc and current_branch.strip():
            current = current_branch.strip()
            # Normalize checkout_target (remove origin/ prefix if present)
            target = checkout_target.replace("origin/", "")

            if current == target:
                # Current branch matches target - use main clone directly
                self.logger.debug(
                    f"{self.log_prefix} Branch {target} already checked out in main clone, "
                    "using main clone instead of worktree"
                )
                yield (True, repo_dir, "", "")
                return

        # Create worktree for this operation
        async with helpers_module.git_worktree_checkout(
            repo_dir=repo_dir,
            checkout=checkout_target,
            log_prefix=self.log_prefix,
            mask_sensitive=self.github_webhook.mask_sensitive,
        ) as (success, worktree_path, out, err):
            result: tuple[bool, str, str, str] = (success, worktree_path, out, err)

            # Merge base branch if needed (for PR testing)
            if success and pull_request and not is_merged and not tag_name and not skip_merge:
                merge_ref = base_ref
                if merge_ref is None:
                    merge_ref = await github_api_call(
                        lambda: pull_request.base.ref, logger=self.logger, log_prefix=self.log_prefix
                    )
                git_cmd = f"git -C {worktree_path}"
                rc, out, err = await run_command(
                    command=f"{git_cmd} merge origin/{merge_ref} -m 'Merge {merge_ref}'",
                    log_prefix=self.log_prefix,
                    mask_sensitive=self.github_webhook.mask_sensitive,
                )
                if not rc:
                    result = (False, worktree_path, out, err)

            yield result

    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        shutil.rmtree("/tmp/storage-run-1000/containers", ignore_errors=True)
        shutil.rmtree("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    async def run_podman_command(self, command: str, redact_secrets: list[str] | None = None) -> tuple[bool, str, str]:
        rc, out, err = await run_command(
            command=command,
            log_prefix=self.log_prefix,
            redact_secrets=redact_secrets,
            mask_sensitive=self.github_webhook.mask_sensitive,
        )

        if rc:
            return rc, out, err

        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return await run_command(
                command=command,
                log_prefix=self.log_prefix,
                redact_secrets=redact_secrets,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )

        return rc, out, err

    async def run_check(self, pull_request: PullRequest, check_config: CheckConfig) -> None:
        """Unified check execution method for both built-in and custom checks.

        This method handles the common lifecycle for all command-based checks:
        1. Set check to in_progress
        2. Checkout worktree
        3. Execute command
        4. Report success or failure

        Args:
            pull_request: The pull request to run the check on.
            check_config: Configuration for the check (name, command, title, use_cwd).
        """
        try:
            if await self.check_run_handler.is_check_run_in_progress(check_run=check_config.name):
                self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {check_config.name}.")

            self.logger.info(f"{self.log_prefix} Starting check: {check_config.name}")
            await self.check_run_handler.set_check_in_progress(name=check_config.name)

            async with self._checkout_worktree(pull_request=pull_request) as (success, worktree_path, out, err):
                output: CheckRunOutput = {
                    "title": check_config.title,
                    "summary": "",
                    "text": None,
                }

                if not success:
                    self.logger.error(f"{self.log_prefix} Repository preparation failed for {check_config.name}")
                    output["text"] = self.check_run_handler.get_check_run_text(out=out, err=err)
                    return await self.check_run_handler.set_check_failure(name=check_config.name, output=output)

                # Build command with worktree path substitution
                # Use replace() instead of format() to avoid KeyError on other braces in user commands
                cmd = check_config.command.replace("{worktree_path}", worktree_path)
                # NOTE: Removed debug log of command to prevent secret leakage

                # Execute command - use cwd if configured, otherwise command should include paths
                cwd = worktree_path if check_config.use_cwd else None
                try:
                    rc, out, err = await run_command(
                        command=cmd,
                        log_prefix=self.log_prefix,
                        mask_sensitive=self.github_webhook.mask_sensitive,
                        cwd=cwd,
                    )
                except TimeoutError:
                    self.logger.error(f"{self.log_prefix} Check {check_config.name} timed out")
                    output["text"] = "Command execution timed out"
                    return await self.check_run_handler.set_check_failure(name=check_config.name, output=output)

                output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

                if rc:
                    self.logger.info(f"{self.log_prefix} Check {check_config.name} completed successfully")
                    return await self.check_run_handler.set_check_success(name=check_config.name, output=output)
                else:
                    self.logger.info(f"{self.log_prefix} Check {check_config.name} failed")
                    return await self.check_run_handler.set_check_failure(name=check_config.name, output=output)

        except asyncio.CancelledError:
            self.logger.debug(f"{self.log_prefix} Check {check_config.name} cancelled")
            raise  # Always re-raise CancelledError
        except Exception as ex:
            self.logger.exception(f"{self.log_prefix} Check {check_config.name} failed with unexpected error")
            error_output: CheckRunOutput = {
                "title": check_config.title,
                "summary": "Unexpected error during check execution",
                "text": f"Error: {ex}",
            }
            await self.check_run_handler.set_check_failure(name=check_config.name, output=error_output)
            raise

    async def run_tox(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.tox:
            self.logger.debug(f"{self.log_prefix} Tox not configured for this repository")
            return

        python_ver = (
            f"--python={self.github_webhook.tox_python_version}" if self.github_webhook.tox_python_version else ""
        )
        # Wrap PyGithub property access to avoid blocking
        base_ref = await github_api_call(lambda: pull_request.base.ref, logger=self.logger, log_prefix=self.log_prefix)
        _tox_tests = self.github_webhook.tox.get(base_ref, "")

        # Build tox command with {worktree_path} placeholder
        cmd = f"uvx {python_ver} {TOX_STR} --workdir {{worktree_path}} --root {{worktree_path}} -c {{worktree_path}}"
        if _tox_tests and _tox_tests != "all":
            tests = _tox_tests.replace(" ", "")
            cmd += f" -e {tests}"

        if self.github_webhook.tox_args:
            cmd += f" {self.github_webhook.tox_args}"

        check_config = CheckConfig(name=TOX_STR, command=cmd, title="Tox")
        await self.run_check(pull_request=pull_request, check_config=check_config)

    async def run_pre_commit(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.pre_commit:
            self.logger.debug(f"{self.log_prefix} Pre-commit not configured for this repository")
            return

        cmd = f"uvx --directory {{worktree_path}} {PREK_STR} run --all-files"
        check_config = CheckConfig(name=PRE_COMMIT_STR, command=cmd, title="Pre-Commit")
        await self.run_check(pull_request=pull_request, check_config=check_config)

    async def run_security_suspicious_paths(self) -> None:
        """Check if PR modifies security-sensitive paths.

        Fails the check run if any changed files match the configured suspicious path prefixes.
        Uses changed_files from owners_file_handler (already computed during PR processing).
        """
        suspicious_paths = self.github_webhook.security_suspicious_paths
        if not suspicious_paths:
            self.logger.debug(f"{self.log_prefix} No suspicious paths configured, skipping security check")
            return

        await self.check_run_handler.set_check_in_progress(name=SECURITY_SUSPICIOUS_PATHS_STR)

        try:
            changed_files = self.owners_file_handler.changed_files
            matched_files = [f for f in changed_files if any(f.startswith(prefix) for prefix in suspicious_paths)]

            if matched_files:
                files_list = "\n".join(f"- `{f}`" for f in matched_files)
                output: CheckRunOutput = {
                    "title": "\u274c Security: Suspicious Paths Detected",
                    "summary": f"{len(matched_files)} file(s) modify security-sensitive paths",
                    "text": (
                        f"## Suspicious Path Detection\n\n"
                        f"This PR modifies files in security-sensitive locations:\n\n"
                        f"{files_list}\n\n"
                        f"**Configured suspicious path prefixes:**\n"
                        + "\n".join(f"- `{p}`" for p in suspicious_paths)
                        + "\n\n"
                        "These paths control development tooling, CI/CD workflows, or IDE configurations "
                        "and require careful review to prevent supply-chain attacks."
                    ),
                }
                self.logger.warning(f"{self.log_prefix} PR modifies suspicious paths: {matched_files}")
                await self.check_run_handler.set_check_failure(name=SECURITY_SUSPICIOUS_PATHS_STR, output=output)
            else:
                output = {
                    "title": "Security: Suspicious Paths",
                    "summary": "No security-sensitive paths modified",
                    "text": (
                        "No changed files match the configured suspicious path prefixes.\n\n"
                        "**Configured prefixes:**\n" + "\n".join(f"- `{p}`" for p in suspicious_paths)
                    ),
                }
                await self.check_run_handler.set_check_success(name=SECURITY_SUSPICIOUS_PATHS_STR, output=output)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception(f"{self.log_prefix} Security suspicious paths check failed")
            await self.check_run_handler.set_check_failure(
                name=SECURITY_SUSPICIOUS_PATHS_STR,
                output={"title": "Security check error", "summary": "Unexpected error during check", "text": None},
            )

    async def run_security_committer_identity(self) -> None:
        """Check if the last committer matches the PR author.

        Uses a unified trusted-committers list that includes static config entries,
        the GitHub App bot login, web-flow, and API users from github-tokens.
        """
        if not self.github_webhook.security_committer_identity_check:
            self.logger.debug(f"{self.log_prefix} Committer identity check disabled, skipping")
            return

        await self.check_run_handler.set_check_in_progress(name=SECURITY_COMMITTER_IDENTITY_STR)

        try:
            parent_committer = self.github_webhook.parent_committer
            last_committer = self.github_webhook.last_committer

            # SECURITY: "unknown" check MUST precede the trusted-committers check.
            # An unverifiable committer identity should always fail.
            if last_committer == "unknown":
                output: CheckRunOutput = {
                    "title": "❌ Security: Committer Identity Unknown",
                    "summary": "Committer identity could not be verified",
                    "text": (
                        "## Committer Identity Check\n\n"
                        f"**PR author:** `{parent_committer}`\n"
                        "**Last commit committer:** unknown\n\n"
                        "Committer identity could not be verified — "
                        "last commit has no associated GitHub user.\n\n"
                        "This may indicate:\n"
                        "- A commit was made with a local Git identity not linked to a GitHub account\n"
                        "- The committer's email is not verified on GitHub\n\n"
                        "Please verify the commit authorship before merging."
                    ),
                }
                self.logger.warning(
                    f"{self.log_prefix} Committer identity unknown: "
                    f"PR author={parent_committer}, last committer has no GitHub user"
                )
                await self.check_run_handler.set_check_failure(name=SECURITY_COMMITTER_IDENTITY_STR, output=output)

            elif last_committer.lower() != parent_committer.lower():
                if last_committer.lower() in self.github_webhook.security_trusted_committers:
                    # Extra guard: verify web-flow by immutable user ID to prevent impersonation
                    if last_committer.lower() == GITHUB_WEB_FLOW_LOGIN:
                        last_committer_id = self.github_webhook.last_committer_id
                        if last_committer_id != GITHUB_WEB_FLOW_USER_ID:
                            self.logger.warning(
                                f"{self.log_prefix} Committer login is 'web-flow' but user ID "
                                f"{last_committer_id} does not match GitHub's web-flow ID "
                                f"{GITHUB_WEB_FLOW_USER_ID} — possible impersonation"
                            )
                            output = {
                                "title": "❌ Security: Committer Identity Suspicious",
                                "summary": (
                                    f"Committer claims to be web-flow but has unexpected user ID {last_committer_id}"
                                ),
                                "text": (
                                    f"## Committer Identity Check\n\n"
                                    f"**PR author:** `{parent_committer}`\n"
                                    f"**Last commit committer:** `{last_committer}` (ID: {last_committer_id})\n"
                                    f"**Expected web-flow ID:** {GITHUB_WEB_FLOW_USER_ID}\n\n"
                                    f"The committer login is `web-flow` but the user ID does not match "
                                    f"GitHub's official web-flow account. This may indicate an impersonation attempt."
                                ),
                            }
                            await self.check_run_handler.set_check_failure(
                                name=SECURITY_COMMITTER_IDENTITY_STR, output=output
                            )
                            return

                    # Trusted committer — pass
                    self.logger.info(
                        f"{self.log_prefix} Committer identity: '{last_committer}' is in unified trusted list"
                    )
                    output = {
                        "title": "Security: Committer Identity",
                        "summary": f"Committer '{last_committer}' is trusted",
                        "text": (
                            f"## Committer Identity Check\n\n"
                            f"**PR author:** `{parent_committer}`\n"
                            f"**Last commit committer:** `{last_committer}`\n\n"
                            f"The committer differs from the PR author but is in the trusted committers list.\n"
                            f"This is expected for automated workflows (bots, CI tools, org identities, "
                            f"GitHub web operations)."
                        ),
                    }
                    await self.check_run_handler.set_check_success(name=SECURITY_COMMITTER_IDENTITY_STR, output=output)
                else:
                    # Untrusted mismatch — fail
                    output = {
                        "title": "❌ Security: Committer Identity Mismatch",
                        "summary": f"Last committer '{last_committer}' differs from PR author '{parent_committer}'",
                        "text": (
                            f"## Committer Identity Check\n\n"
                            f"**PR author:** `{parent_committer}`\n"
                            f"**Last commit committer:** `{last_committer}`\n\n"
                            f"The last commit in this PR was made by a different user than the PR author. "
                            f"This may indicate:\n"
                            f"- An unauthorized commit was pushed to the PR branch\n"
                            f"- A bot or automation tool committed with unexpected credentials\n"
                            f"- A legitimate co-author contribution (review carefully)\n\n"
                            f"Please verify this is expected before merging."
                        ),
                    }
                    self.logger.warning(
                        f"{self.log_prefix} Committer identity mismatch: "
                        f"PR author={parent_committer}, last committer={last_committer}"
                    )
                    await self.check_run_handler.set_check_failure(name=SECURITY_COMMITTER_IDENTITY_STR, output=output)
            else:
                # Match — pass
                output = {
                    "title": "Security: Committer Identity",
                    "summary": "Committer identity verified",
                    "text": (
                        f"The last commit committer (`{last_committer}`) matches the PR author (`{parent_committer}`)."
                    ),
                }
                await self.check_run_handler.set_check_success(name=SECURITY_COMMITTER_IDENTITY_STR, output=output)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception(f"{self.log_prefix} Security committer identity check failed")
            await self.check_run_handler.set_check_failure(
                name=SECURITY_COMMITTER_IDENTITY_STR,
                output={"title": "Security check error", "summary": "Unexpected error during check", "text": None},
            )

    def _build_oci_annotations(
        self,
        pull_request: PullRequest | None = None,
        tag: str = "",
    ) -> str:
        """Build OCI annotation flags for podman build command.

        Returns a string of --annotation flags to append to the build command.
        """
        if not self.github_webhook.container_oci_annotations_enabled:
            return ""

        annotations: dict[str, str] = {}
        auto = self.github_webhook.container_oci_auto_annotations

        # Auto-populated annotations
        if auto.get("created", True):
            annotations["org.opencontainers.image.created"] = datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if auto.get("source", True):
            annotations["org.opencontainers.image.source"] = (
                f"https://github.com/{self.github_webhook.repository_full_name}"
            )

        if auto.get("revision", True):
            if pull_request:
                annotations["org.opencontainers.image.revision"] = pull_request.head.sha
            elif self.github_webhook.hook_data.get("head_commit", {}).get("id"):
                annotations["org.opencontainers.image.revision"] = self.github_webhook.hook_data["head_commit"]["id"]

        if auto.get("version", True) and tag:
            annotations["org.opencontainers.image.version"] = tag

        if auto.get("title", True):
            annotations["org.opencontainers.image.title"] = self.github_webhook.repository_name

        # Static annotations override auto-populated ones
        annotations.update(self.github_webhook.container_oci_static_annotations)

        if not annotations:
            return ""

        return " ".join(f"--annotation {shlex.quote(f'{k}={v}')}" for k, v in annotations.items())

    async def run_build_container(
        self,
        pull_request: PullRequest | None = None,
        set_check: bool = True,
        push: bool = False,
        is_merged: bool = False,
        tag: str = "",
        command_args: str = "",
        reviewed_user: str | None = None,
    ) -> None:
        if not self.github_webhook.build_and_push_container:
            return

        if (
            self.owners_file_handler
            and reviewed_user
            and pull_request
            and not await self.owners_file_handler.is_user_valid_to_run_commands(
                reviewed_user=reviewed_user, pull_request=pull_request
            )
        ):
            return

        if pull_request and set_check:
            if await self.check_run_handler.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR) and not is_merged:
                self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {BUILD_CONTAINER_STR}.")

        if set_check:
            await self.check_run_handler.set_check_in_progress(name=BUILD_CONTAINER_STR)

        _container_repository_and_tag = self.github_webhook.container_repository_and_tag(
            pull_request=pull_request, is_merged=is_merged, tag=tag
        )
        no_cache: str = " --no-cache" if is_merged else ""

        async with self._checkout_worktree(
            pull_request=pull_request,
            is_merged=is_merged,
            tag_name=tag,
        ) as (success, worktree_path, out, err):
            output: CheckRunOutput = {
                "title": "Build container",
                "summary": "",
                "text": None,
            }

            if not success:
                output["text"] = self.check_run_handler.get_check_run_text(out=out, err=err)
                if pull_request and set_check:
                    await self.check_run_handler.set_check_failure(name=BUILD_CONTAINER_STR, output=output)
                return

            # Build container build command with worktree path
            # Use configured context subdirectory for build context (default: repo root)
            _context = self.github_webhook.container_context
            if _context:
                resolved_context = os.path.realpath(os.path.join(worktree_path, _context))
                resolved_worktree = os.path.realpath(worktree_path)
                is_under_worktree = resolved_context.startswith(resolved_worktree + os.sep)
                if not is_under_worktree and resolved_context != resolved_worktree:
                    self.logger.error(
                        f"{self.log_prefix} Container context '{_context}' resolves outside "
                        f"worktree ({resolved_context}), rejecting for security"
                    )
                    output["text"] = f"Container build context '{_context}' escapes repository root"
                    if pull_request and set_check:
                        await self.check_run_handler.set_check_failure(name=BUILD_CONTAINER_STR, output=output)
                    return
                build_context: str = resolved_context
            else:
                build_context = worktree_path

            build_cmd: str = (
                f"--network=host {no_cache} -f "
                f"{worktree_path}/{self.github_webhook.dockerfile} "
                f"{build_context} -t {_container_repository_and_tag}"
            )

            oci_annotation_flags = self._build_oci_annotations(pull_request=pull_request, tag=tag)
            if oci_annotation_flags:
                build_cmd = f"{oci_annotation_flags} {build_cmd}"

            if self.github_webhook.container_build_args:
                build_args = " ".join(f"--build-arg {arg}" for arg in self.github_webhook.container_build_args)
                build_cmd = f"{build_args} {build_cmd}"

            if self.github_webhook.container_command_args:
                build_cmd = f"{' '.join(self.github_webhook.container_command_args)} {build_cmd}"

            if command_args:
                build_cmd = f"{command_args} {build_cmd}"

            podman_build_cmd: str = f"podman build {build_cmd}"
            self.logger.debug(f"{self.log_prefix} Podman build command to run: {podman_build_cmd}")

            build_rc, build_out, build_err = await self.run_podman_command(
                command=podman_build_cmd,
            )
            output["text"] = self.check_run_handler.get_check_run_text(err=build_err, out=build_out)

            if build_rc:
                self.logger.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_check_success(name=BUILD_CONTAINER_STR, output=output)
            else:
                self.logger.error(f"{self.log_prefix} Failed to build {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_check_failure(name=BUILD_CONTAINER_STR, output=output)

            if push and build_rc:
                cmd = (
                    f"podman push --creds "
                    f"{self.github_webhook.container_repository_username}:"
                    f"{self.github_webhook.container_repository_password} "
                    f"{_container_repository_and_tag}"
                )
                push_rc, _, _ = await self.run_podman_command(
                    command=cmd,
                    redact_secrets=[
                        self.github_webhook.container_repository_username,
                        self.github_webhook.container_repository_password,
                    ],
                )
                if push_rc:
                    push_msg: str = f"New container for {_container_repository_and_tag} published"
                    if pull_request:
                        await github_api_call(
                            pull_request.create_issue_comment, push_msg, logger=self.logger, log_prefix=self.log_prefix
                        )

                    if self.github_webhook.slack_webhook_url:
                        message = f"""
```
{self.github_webhook.repository_full_name} {push_msg}.
```
"""
                        send_slack_message(
                            message=message,
                            webhook_url=self.github_webhook.slack_webhook_url,
                            logger=self.logger,
                            log_prefix=self.log_prefix,
                        )

                    self.logger.info(f"{self.log_prefix} Done push {_container_repository_and_tag}")
                else:
                    err_msg: str = f"Failed to build and push {_container_repository_and_tag}"
                    if pull_request:
                        await github_api_call(
                            pull_request.create_issue_comment, err_msg, logger=self.logger, log_prefix=self.log_prefix
                        )

                    if self.github_webhook.slack_webhook_url:
                        message = f"""
```
{self.github_webhook.repository_full_name} {err_msg}.
```
                        """
                        send_slack_message(
                            message=message,
                            webhook_url=self.github_webhook.slack_webhook_url,
                            logger=self.logger,
                            log_prefix=self.log_prefix,
                        )

    async def run_install_python_module(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.pypi:
            return

        cmd = "uvx pip wheel --no-cache-dir -w {worktree_path}/dist {worktree_path}"
        check_config = CheckConfig(name=PYTHON_MODULE_INSTALL_STR, command=cmd, title="Python module installation")
        await self.run_check(pull_request=pull_request, check_config=check_config)

    async def run_conventional_title_check(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.conventional_title:
            return

        output: CheckRunOutput = {
            "title": "Conventional Title",
            "summary": "PR title follows Conventional Commits format",
            "text": (
                f"**Format:** `<type>[optional scope]: <description>`\n\n"
                f"**Your title:** `{pull_request.title}`\n\n"
                f"This title complies with the Conventional Commits v1.0.0 specification."
            ),
        }

        if await self.check_run_handler.is_check_run_in_progress(check_run=CONVENTIONAL_TITLE_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {CONVENTIONAL_TITLE_STR}.")

        await self.check_run_handler.set_check_in_progress(name=CONVENTIONAL_TITLE_STR)
        title = pull_request.title
        is_wildcard = self.github_webhook.conventional_title.strip() == "*"

        if is_wildcard:
            allowed_names: list[str] = []
            title_valid = bool(re.match(r"^[\w-]+(\([^)]+\))?!?: .+", title))
            self.logger.debug(f"{self.log_prefix} Conventional title check (wildcard) for title: {title}")
        else:
            allowed_names = [name.strip() for name in self.github_webhook.conventional_title.split(",") if name.strip()]
            title_valid = any(re.match(rf"^{re.escape(_name)}(\([^)]+\))?!?: .+", title) for _name in allowed_names)
            self.logger.debug(
                f"{self.log_prefix} Conventional title check for title: {title}, allowed: {allowed_names}"
            )

        if title_valid:
            await self.check_run_handler.set_check_success(name=CONVENTIONAL_TITLE_STR, output=output)
        else:
            if is_wildcard:
                types_display = "any valid type (wildcard `*` configured)"
            else:
                types_display = ", ".join(f"`{t}`" for t in allowed_names)

            type_rule = (
                "Type can be any valid token (wildcard `*` configured)"
                if is_wildcard
                else "Type must be one of the configured types"
            )

            output["title"] = "❌ Conventional Title"
            output["summary"] = "Conventional Commit Format Violation"
            output["text"] = f"""## Conventional Commits Validation Failed

**Your PR title:**
> {title}

**Required format:**
```
<type>[optional scope]: <description>
```

**Configured types for this repository:**
{types_display}

**Valid examples:**
- `feat: add user authentication`
- `fix(parser): handle edge case in URL parsing`
- `feat!: breaking change in API response`
- `refactor(core)!: major architectural change`
- `docs: update installation guide`

**Format rules:**
- {type_rule}
- Optional scope in parentheses: `(scope)`
- Optional breaking change indicator: `!`
- **Mandatory**: colon followed by space `: `
- **Mandatory**: non-empty description after the space

**Note:** The Conventional Commits specification allows custom types beyond the standard recommendations.
Your team can configure additional types in the repository settings.

**Resources:**
- [Conventional Commits v1.0.0 Specification](https://www.conventionalcommits.org/en/v1.0.0/)
"""
            # AI-suggested title (if ai-features configured)
            ai_suggestion = await self._get_ai_title_suggestion(
                pull_request=pull_request,
                title=title,
                allowed_names=allowed_names,
                is_wildcard=is_wildcard,
            )

            ai_mode = self._get_ai_conventional_title_mode()

            if ai_suggestion and ai_mode == "fix":
                # Validate the suggestion before applying
                if is_wildcard:
                    suggestion_valid = bool(re.match(r"^[\w-]+(\([^)]+\))?!?: .+", ai_suggestion))
                else:
                    suggestion_valid = any(
                        re.match(rf"^{re.escape(_name)}(\([^)]+\))?!?: .+", ai_suggestion) for _name in allowed_names
                    )

                if suggestion_valid and ai_suggestion != title:
                    self.logger.info(f"{self.log_prefix} AI fixing PR title from '{title}' to '{ai_suggestion}'")
                    try:
                        await github_api_call(
                            pull_request.edit, title=ai_suggestion, logger=self.logger, log_prefix=self.log_prefix
                        )
                        output["title"] = "Conventional Title"
                        output["summary"] = "PR title auto-fixed by AI"
                        output["text"] = (
                            f"**AI Auto-Fix Applied**\n\n"
                            f"Title updated from: `{title}`\n"
                            f"Title updated to: `{ai_suggestion}`\n"
                        )
                        return await self.check_run_handler.set_check_success(
                            name=CONVENTIONAL_TITLE_STR, output=output
                        )
                    except Exception:
                        self.logger.exception(f"{self.log_prefix} Failed to auto-fix PR title")
                        if output["text"] is not None:
                            output["text"] += (
                                f"\n\n---\n\n### AI Auto-Fix Failed\n\n"
                                f"Suggested title: `{ai_suggestion}`\n"
                                f"Failed to update PR title automatically. Please update manually."
                            )
                else:
                    self.logger.warning(
                        f"{self.log_prefix} AI suggestion invalid or unchanged, skipping auto-fix: {ai_suggestion}"
                    )
                    if output["text"] is not None:
                        output["text"] += (
                            f"\n\n---\n\n### AI Auto-Fix Skipped\n\n"
                            f"AI suggested: `{ai_suggestion}`\n"
                            f"Suggestion was invalid or unchanged."
                        )

            elif ai_suggestion and ai_mode == "suggest" and output["text"] is not None:
                output["text"] += f"\n\n---\n\n### AI-Suggested Title\n\n> {ai_suggestion}\n"

            await self.check_run_handler.set_check_failure(name=CONVENTIONAL_TITLE_STR, output=output)

    def _get_ai_conventional_title_mode(self) -> str | None:
        """Get the conventional-title AI mode from config.

        Returns:
            "suggest" for suggestion mode, "fix" for auto-fix mode, or None if disabled.
        """
        ai_config = self.github_webhook.ai_features
        if not ai_config:
            return None

        ct_config = ai_config.get("conventional-title")
        if not isinstance(ct_config, dict) or not ct_config.get("enabled"):
            return None

        mode = ct_config.get("mode", "suggest")
        if mode not in ("suggest", "fix"):
            self.logger.warning(f"{self.log_prefix} Invalid conventional-title mode '{mode}', defaulting to 'suggest'")
            return "suggest"
        return mode

    async def _get_ai_title_suggestion(
        self, pull_request: PullRequest, title: str, allowed_names: list[str], *, is_wildcard: bool
    ) -> str | None:
        """Get an AI-suggested conventional title when validation fails.

        Returns the suggestion string or None if AI features are not configured or on error.
        """
        mode = self._get_ai_conventional_title_mode()
        if not mode:
            return None

        ai_result = get_ai_config(self.github_webhook.ai_features)
        if not ai_result:
            return None

        ai_provider, ai_model = ai_result

        ai_config = self.github_webhook.ai_features
        ct_config = ai_config.get("conventional-title", {}) if ai_config else {}
        timeout_minutes = ct_config.get("timeout-minutes", 10) if isinstance(ct_config, dict) else 10

        if is_wildcard:
            types_info = "Any type name is accepted (wildcard mode)."
        else:
            types_info = f"Allowed types: {', '.join(allowed_names)}"

        try:
            base_ref = await github_api_call(
                lambda: pull_request.base.ref, logger=self.logger, log_prefix=self.log_prefix
            )

            async with self._checkout_worktree(pull_request=pull_request) as (wt_success, worktree_path, _, _):
                if not wt_success:
                    self.logger.warning(f"{self.log_prefix} Failed to create worktree for AI title suggestion")
                    return None

                prompt = (
                    "You are in a git repository checked out to a PR branch.\n"
                    f"Run `git diff origin/{base_ref}` to see the changes in this PR.\n"
                    f"Run `git log origin/{base_ref}..HEAD --oneline` to see the commit messages.\n"
                    f"Based on the diff and commit messages, suggest a conventional commit title.\n\n"
                    f"Current PR title: {title}\n"
                    f"{types_info}\n"
                    f"Required format: <type>[optional scope]: <description>\n"
                    f"Output ONLY the corrected title on a single line.\n"
                    f"Do NOT include any explanation, reasoning, markdown, or quotes.\n"
                    f"Example output: feat: add user authentication"
                )

                ai_result = await call_ai(
                    prompt=prompt,
                    ai_provider=ai_provider,
                    ai_model=ai_model,
                    cwd=worktree_path,
                    timeout_minutes=timeout_minutes,
                    tools=["read", "grep", "find", "ls"],  # Read-only — AI inspects repo to suggest title
                )

                if ai_result.success:
                    # Clean up the response - take first line, strip backticks/quotes
                    suggestion = ai_result.text.strip().splitlines()[0].strip().strip("`").strip('"').strip("'")
                    self.logger.info(f"{self.log_prefix} AI suggested title: {suggestion}")
                    return suggestion

                self.logger.warning(
                    f"{self.log_prefix} AI title suggestion failed: {ai_result.error or ai_result.text}"
                )
                return None

        except Exception:
            self.logger.exception(f"{self.log_prefix} AI title suggestion failed unexpectedly")
            return None

    async def run_custom_check(
        self,
        pull_request: PullRequest,
        check_config: dict[str, Any],
    ) -> None:
        """Run a custom check defined in repository configuration.

        This method wraps the unified run_check() method for custom checks.
        Custom checks use cwd mode (execute command in worktree directory).

        Note: name and command validation happens in GithubWebhook._validate_custom_check_runs()
        when custom checks are first loaded. Invalid checks are filtered out at that stage.
        """
        # name and command are guaranteed to exist (validated at load time)
        check_name = check_config["name"]
        command = check_config["command"]

        # Wrap command in shell to support shell syntax (env vars, pipes, subshells, etc.)
        # This is safe for custom checks since they are explicitly user-defined commands.
        # Using shlex.quote() ensures the command is properly escaped when passed as
        # a single argument to /bin/sh -c, so shlex.split() produces:
        # ['/bin/sh', '-c', 'JIRA_TOKEN="xxx" tox -e verify-bugs-are-open-gh']
        shell_wrapped_command = f"/bin/sh -c {shlex.quote(command)}"

        # Custom checks run with cwd set to worktree directory
        unified_config = CheckConfig(
            name=check_name,
            command=shell_wrapped_command,
            title=f"Custom Check: {check_name}",
            use_cwd=True,
        )
        await self.run_check(pull_request=pull_request, check_config=unified_config)

    async def is_branch_exists(self, branch: str) -> bool:
        try:
            await github_api_call(
                self.repository.get_branch,
                branch,
                logger=self.logger,
                log_prefix=self.log_prefix,
            )
            return True
        except GithubException as ex:
            if ex.status == 404:
                return False
            raise

    async def _find_signoff_source(
        self,
        pull_request: PullRequest,
    ) -> tuple[Any, str | None]:
        """Find a commit with a Signed-off-by trailer and return it with the sign-off email.

        Checks the merge commit first (squash-merge). If the merge commit has no
        Signed-off-by, falls back to scanning the PR's individual commits in
        reverse order (regular merge).

        Returns (source_commit, signoff_email) or (None, None) if no sign-off found.
        """
        # Try the merge commit first (squash-merge case)
        merge_sha = await github_api_call(
            lambda: pull_request.merge_commit_sha, logger=self.logger, log_prefix=self.log_prefix
        )
        if merge_sha:
            merge_commit = await github_api_call(
                self.github_webhook.repository.get_commit, merge_sha, logger=self.logger, log_prefix=self.log_prefix
            )
            commit_msg = await github_api_call(
                lambda: merge_commit.commit.message, logger=self.logger, log_prefix=self.log_prefix
            )
            signoff_match = re.findall(r"(?m)^Signed-off-by:\s*(.+?)\s*<([^>\n]+)>\s*$", commit_msg)
            if signoff_match:
                return merge_commit, signoff_match[-1][1]

        # Fall back to PR commits (regular merge case)
        commits = await github_api_call(
            lambda: list(pull_request.get_commits()), logger=self.logger, log_prefix=self.log_prefix
        )
        for commit in reversed(commits):
            commit_msg = await github_api_call(
                lambda c=commit: c.commit.message, logger=self.logger, log_prefix=self.log_prefix
            )
            signoff_match = re.findall(r"(?m)^Signed-off-by:\s*(.+?)\s*<([^>\n]+)>\s*$", commit_msg)
            if signoff_match:
                return commit, signoff_match[-1][1]

        return None, None

    async def _restore_original_author_for_cherry_pick(
        self,
        pull_request: PullRequest,
        git_cmd: str,
        github_token: str,
    ) -> bool:
        """Amend cherry-picked commit to restore the original PR author for DCO compliance.

        GitHub squash-merges rewrite the author email to the noreply format
        (e.g., 86722603+user@users.noreply.github.com). When git cherry-pick
        replays such a commit, the DCO check fails because the author email
        no longer matches the Signed-off-by trailer.

        This method first checks the merge commit (via ``pull_request.merge_commit_sha``)
        for a Signed-off-by trailer (squash-merge case). If not found, it falls back
        to scanning the PR's individual commits (regular merge case).

        The author identity is built from the source commit's git author name
        (preserved by GitHub) combined with the email from the Signed-off-by trailer.

        Returns True if the commit was amended, False if no amendment was needed or possible.
        """
        try:
            # Try merge commit first (squash-merge), fall back to PR commits (regular merge)
            source_commit, signoff_email = await self._find_signoff_source(pull_request)
            if not source_commit or not signoff_email:
                self.logger.debug(f"{self.log_prefix} No Signed-off-by found, skipping author restore")
                return False

            # Author name from the source commit's git author (GitHub preserves the display name)
            # Author email from the Signed-off-by trailer (commit email may be noreply)
            author_name = await github_api_call(
                lambda: source_commit.commit.author.name, logger=self.logger, log_prefix=self.log_prefix
            )
            author_email = signoff_email

            author_spec = f"{author_name} <{author_email}>"
            redact_list = [github_token, author_spec, author_email, author_name]

            # Check if the cherry-picked commit author already matches (both name and email)
            needs_author_amend = True
            rc, current_author_info, _ = await run_command(
                command=f"{git_cmd} log -1 --format=%an%n%ae",
                log_prefix=self.log_prefix,
                redact_secrets=redact_list,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                self.logger.warning(
                    f"{self.log_prefix} Could not read current author info, proceeding with author amend"
                )
            else:
                info_lines = current_author_info.strip().splitlines()
                if len(info_lines) == 2 and info_lines[0] == author_name and info_lines[1] == author_email:
                    needs_author_amend = False

            # Read the current commit message to fix Signed-off-by trailers
            rc, current_msg, _ = await run_command(
                command=f"{git_cmd} log -1 --format=%B",
                log_prefix=self.log_prefix,
                redact_secrets=redact_list,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            needs_message_amend = False
            amended_msg: str | None = None
            if not rc:
                self.logger.warning(f"{self.log_prefix} Could not read commit message, amending author only")
            else:
                # Remove all existing Signed-off-by trailers and add the correct one
                msg_lines = current_msg.rstrip().splitlines()
                filtered_lines = [line for line in msg_lines if not re.match(r"Signed-off-by:\s*", line)]
                while filtered_lines and not filtered_lines[-1].strip():
                    filtered_lines.pop()
                filtered_lines.append("")
                filtered_lines.append(f"Signed-off-by: {author_name} <{author_email}>")
                amended_msg = "\n".join(filtered_lines) + "\n"
                needs_message_amend = amended_msg != current_msg

            if not needs_author_amend and not needs_message_amend:
                self.logger.debug(f"{self.log_prefix} Author and Signed-off-by already match, no amend needed")
                return False

            # Amend the commit author and optionally the message
            msg_flag = f"-m {shlex.quote(amended_msg)}" if needs_message_amend and amended_msg else "--no-edit"
            rc, _, err = await run_command(
                command=f"{git_cmd} commit --amend --author={shlex.quote(author_spec)} {msg_flag}",
                log_prefix=self.log_prefix,
                redact_secrets=redact_list,
                mask_sensitive=self.github_webhook.mask_sensitive,
            )

            if not rc:
                redacted_err = _redact_secrets(
                    err,
                    redact_list,
                    mask_sensitive=self.github_webhook.mask_sensitive,
                )
                self.logger.warning(
                    f"{self.log_prefix} Failed to amend cherry-pick author for DCO compliance: {redacted_err}"
                )
                return False

            self.logger.info(f"{self.log_prefix} Restored original author on cherry-pick for DCO compliance")
            return True
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception(f"{self.log_prefix} Failed to restore original author for cherry-pick")
            return False

    async def _resolve_cherry_pick_with_ai(
        self,
        worktree_path: str,
        git_cmd: str,
        github_token: str,
        commit_hash: str,
        target_branch: str,
        pr_title: str,
    ) -> tuple[bool, str]:
        """Attempt to resolve cherry-pick conflicts using AI.

        Args:
            worktree_path: Path to the git worktree with conflicts.
            git_cmd: Git command prefix for this worktree.
            github_token: Token to redact from logs.
            commit_hash: The commit being cherry-picked (for context gathering).
            target_branch: The branch being cherry-picked onto.

        Returns:
            Tuple of (success, original_diff_stat). The diff stat is passed to
            _verify_cherry_pick_scope to avoid a redundant subprocess call.
        """
        ai_config = self.github_webhook.ai_features
        if not ai_config:
            self.logger.debug(f"{self.log_prefix} AI cherry-pick conflict resolution not enabled")
            return False, ""

        cherry_pick_ai_config = ai_config.get("resolve-cherry-pick-conflicts-with-ai")
        if not isinstance(cherry_pick_ai_config, dict) or not cherry_pick_ai_config.get("enabled"):
            self.logger.debug(f"{self.log_prefix} AI cherry-pick conflict resolution not enabled")
            return False, ""

        ai_result = get_ai_config(ai_config)
        if not ai_result:
            self.logger.debug(f"{self.log_prefix} AI features not fully configured (missing provider/model)")
            return False, ""

        ai_provider, ai_model = ai_result

        # Gather commit context for the AI prompt
        rc, commit_message, _ = await run_command(
            command=f"{git_cmd} log --oneline -1 {commit_hash}",
            log_prefix=self.log_prefix,
            redact_secrets=[github_token],
            mask_sensitive=self.github_webhook.mask_sensitive,
        )
        if not rc:
            self.logger.warning(f"{self.log_prefix} Could not retrieve commit message for AI context")
            commit_message = ""
        else:
            commit_message = commit_message.strip()

        rc, commit_diff_stat, _ = await run_command(
            command=f"{git_cmd} diff {commit_hash}^..{commit_hash} --stat",
            log_prefix=self.log_prefix,
            redact_secrets=[github_token],
            mask_sensitive=self.github_webhook.mask_sensitive,
        )
        if not rc:
            self.logger.warning(f"{self.log_prefix} Could not retrieve commit diff stat for AI context")
            commit_diff_stat = ""
        else:
            commit_diff_stat = commit_diff_stat.strip()

        system_prompt = (
            "You are an expert software engineer resolving git cherry-pick merge conflicts. "
            "You have access to file reading and editing tools. "
            "Your goal is to resolve all conflicts while preserving the intent of the original commit."
        )

        prompt = (
            "You are in a git repository with cherry-pick merge conflicts. "
            "Resolve ALL conflicts in ALL files.\n\n"
            f"## Original Commit Context\n"
            f"**Commit:** `{commit_hash}`\n"
            f"**Message:** {commit_message}\n"
            f"**Target branch:** `{target_branch}`\n"
            f"**PR title:** {pr_title}\n\n"
            f"**Original commit changed files:**\n```\n{commit_diff_stat}\n```\n\n"
            "## Instructions\n\n"
            "### Conflict Resolution Rules\n"
            "- **Prefer the cherry-picked changes.** Only use HEAD (target branch) when "
            "the cherry-picked code references APIs/functions that don't exist on the target branch.\n"
            "- Standard conflict markers (<<<<<<< HEAD, =======, >>>>>>>): "
            "HEAD is the target branch. Resolve in favor of the cherry-picked changes "
            "unless they reference code not present on the target branch.\n"
            "- File 'deleted in HEAD and modified in <commit>': This means the file "
            "does not exist on the target branch. If the cherry-pick is introducing "
            "this file to the target branch, keep the file and 'git add' it. "
            "If the file was intentionally removed from the target branch and the "
            "changes are not relevant, 'git rm' it.\n"
            "- File 'added in both' or 'renamed': Merge the content, keeping both "
            "sides' intent.\n\n"
            "After resolving all conflicts, "
            "make sure the result is syntactically valid.\n\n"
            "After resolving, read each resolved file to verify your edits are syntactically valid "
            "and semantically consistent with the original commit's purpose."
        )

        self.logger.info(f"{self.log_prefix} Attempting AI conflict resolution with {ai_provider}/{ai_model}")

        timeout_minutes = cherry_pick_ai_config.get("timeout-minutes", 10)

        try:
            ai_call_result = await call_ai(
                prompt=prompt,
                ai_provider=ai_provider,
                ai_model=ai_model,
                cwd=worktree_path,
                timeout_minutes=timeout_minutes,
                system_prompt=system_prompt,
                # Read + edit/write for conflict resolution, NO bash
                tools=["read", "edit", "write", "grep", "find", "ls"],
            )

            if not ai_call_result.success:
                self.logger.warning(
                    f"{self.log_prefix} AI conflict resolution failed: {ai_call_result.error or ai_call_result.text}"
                )
                return False, ""

            self.logger.info(f"{self.log_prefix} AI conflict resolution completed, finalizing cherry-pick")

            # Stage resolved files
            rc, _, err = await run_command(
                command=f"{git_cmd} add -A",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                self.logger.error(f"{self.log_prefix} Failed to stage AI-resolved files: {err}")
                return False, commit_diff_stat

            # Check if cherry-pick is still in progress (it may have auto-completed
            # after staging resolved files, e.g. for modify/delete conflicts)
            rc_check, _, err_check = await run_command(
                command=f"{git_cmd} rev-parse --verify CHERRY_PICK_HEAD",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if rc_check:
                # Cherry-pick still in progress, finalize it
                rc, _, err = await run_command(
                    command=f"{git_cmd} -c core.editor=true cherry-pick --continue",
                    log_prefix=self.log_prefix,
                    redact_secrets=[github_token],
                    mask_sensitive=self.github_webhook.mask_sensitive,
                )
                if not rc:
                    self.logger.error(f"{self.log_prefix} cherry-pick --continue failed after AI resolution: {err}")
                    return False, commit_diff_stat
            else:
                if err_check and "needed a single revision" not in err_check.lower():
                    self.logger.error(f"{self.log_prefix} Unexpected CHERRY_PICK_HEAD check error: {err_check}")
                    return False, commit_diff_stat
                self.logger.info(f"{self.log_prefix} Cherry-pick already completed after staging resolved files")

            self.logger.info(f"{self.log_prefix} AI successfully resolved cherry-pick conflicts")
            return True, commit_diff_stat

        except Exception:
            self.logger.exception(f"{self.log_prefix} AI conflict resolution failed unexpectedly")
            return False, ""

    async def _verify_cherry_pick_scope(
        self,
        git_cmd: str,
        github_token: str,
        original_diff_stat: str,
    ) -> None:
        """Compare original commit scope vs cherry-picked commit scope.

        Uses the pre-fetched original_diff_stat from _resolve_cherry_pick_with_ai
        to avoid a redundant subprocess call. Only runs git diff for the
        cherry-picked commit.

        Logs a warning if the cherry-picked commit has significantly fewer
        file changes than the original. This is informational only and
        never fails the cherry-pick.
        """
        try:
            rc, cherry_picked_stat, _ = await run_command(
                command=f"{git_cmd} diff HEAD^..HEAD --stat",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                self.logger.warning(
                    f"{self.log_prefix} Could not retrieve cherry-picked diff stat for scope verification"
                )
                return

            original_count = _count_files_changed(original_diff_stat)
            cherry_picked_count = _count_files_changed(cherry_picked_stat)

            if original_count > 0 and cherry_picked_count < original_count:
                self.logger.warning(
                    f"{self.log_prefix} Cherry-pick scope reduced: original commit changed "
                    f"{original_count} file(s), cherry-picked commit changed {cherry_picked_count} file(s)"
                )
            else:
                self.logger.info(
                    f"{self.log_prefix} Cherry-pick scope verified: original={original_count} file(s), "
                    f"cherry-picked={cherry_picked_count} file(s)"
                )
        except (OSError, ValueError):
            self.logger.exception(f"{self.log_prefix} Failed to verify cherry-pick scope (non-fatal)")

    async def cherry_pick(
        self,
        pull_request: PullRequest,
        target_branch: str,
        assign_to_pr_owner: bool = True,
    ) -> None:
        pr_author = await github_api_call(
            lambda: pull_request.user.login, logger=self.logger, log_prefix=self.log_prefix
        )
        source_branch = await github_api_call(
            lambda: pull_request.base.ref, logger=self.logger, log_prefix=self.log_prefix
        )

        self.logger.info(
            f"{self.log_prefix} Cherry-pick from {source_branch} to {target_branch}, PR owner: {pr_author}"
        )

        new_branch_name = f"{CHERRY_PICKED_LABEL}-{pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not await self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.error(err_msg)
            await github_api_call(
                pull_request.create_issue_comment, err_msg, logger=self.logger, log_prefix=self.log_prefix
            )

        else:
            await self.check_run_handler.set_check_in_progress(name=CHERRY_PICKED_LABEL)
            commit_hash = pull_request.merge_commit_sha
            commit_msg_striped = pull_request.title.replace("'", "")
            pull_request_url = pull_request.html_url
            github_token = self.github_webhook.token

            async with self._checkout_worktree(pull_request=pull_request, skip_merge=True) as (
                success,
                worktree_path,
                out,
                err,
            ):
                git_cmd = f"git --work-tree={worktree_path} --git-dir={worktree_path}/.git"
                pr_title = f"{CHERRY_PICKED_LABEL}: [{target_branch}] {commit_msg_striped}"
                pr_body = (
                    f"Cherry-pick from `{source_branch}` branch, original PR: {pull_request_url}, PR owner: {pr_author}"
                )
                repo_full_name = self.github_webhook.repository_full_name

                setup_commands: list[str] = [
                    f"{git_cmd} fetch origin {source_branch}",
                    f"{git_cmd} checkout {target_branch}",
                    f"{git_cmd} pull origin {target_branch}",
                    f"{git_cmd} checkout -b {new_branch_name} origin/{target_branch}",
                ]
                cherry_pick_command = f"{git_cmd} cherry-pick {commit_hash}"
                push_command = f"{git_cmd} push origin {new_branch_name}"

                output: CheckRunOutput = {
                    "title": "Cherry-pick details",
                    "summary": "",
                    "text": None,
                }
                if not success:
                    output["text"] = self.check_run_handler.get_check_run_text(out=out, err=err)
                    await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                    return

                for cmd in setup_commands:
                    rc, out, err = await run_command(
                        command=cmd,
                        log_prefix=self.log_prefix,
                        redact_secrets=[github_token],
                        mask_sensitive=self.github_webhook.mask_sensitive,
                    )
                    if not rc:
                        output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                        await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                        redacted_out = _redact_secrets(
                            out,
                            [github_token],
                            mask_sensitive=self.github_webhook.mask_sensitive,
                        )
                        redacted_err = _redact_secrets(
                            err,
                            [github_token],
                            mask_sensitive=self.github_webhook.mask_sensitive,
                        )
                        self.logger.error(f"{self.log_prefix} Cherry pick failed: {redacted_out} --- {redacted_err}")
                        local_branch_name = f"{pull_request.head.ref}-{target_branch}"
                        await github_api_call(
                            pull_request.create_issue_comment,
                            f"**Manual cherry-pick is needed**\nCherry pick failed for "
                            f"{commit_hash} to {target_branch}:\n"
                            f"To cherry-pick run:\n"
                            "```\n"
                            f"git remote update\n"
                            f"git checkout {target_branch}\n"
                            f"git pull origin {target_branch}\n"
                            f"git checkout -b {local_branch_name}\n"
                            f"git cherry-pick {commit_hash}\n"
                            f"# If the above fails with 'is a merge but no -m option', run:\n"
                            f"# git cherry-pick -m 1 {commit_hash}\n"
                            f"git push origin {local_branch_name}\n"
                            "```",
                            logger=self.logger,
                            log_prefix=self.log_prefix,
                        )
                        return

                # Run cherry-pick separately to detect conflicts
                rc, out, err = await run_command(
                    command=cherry_pick_command,
                    log_prefix=self.log_prefix,
                    redact_secrets=[github_token],
                    mask_sensitive=self.github_webhook.mask_sensitive,
                )

                # Retry with -m 1 if the commit is a merge commit
                if not rc and "is a merge but no -m option was given" in err:
                    self.logger.info(f"{self.log_prefix} Merge commit detected, retrying cherry-pick with -m 1")
                    cherry_pick_command_m1 = f"{git_cmd} cherry-pick -m 1 {commit_hash}"
                    rc, out, err = await run_command(
                        command=cherry_pick_command_m1,
                        log_prefix=self.log_prefix,
                        redact_secrets=[github_token],
                        mask_sensitive=self.github_webhook.mask_sensitive,
                    )

                cherry_pick_had_conflicts = False
                if not rc:
                    # Only attempt AI resolution for actual merge conflicts
                    is_conflict = "CONFLICT" in err or "CONFLICT" in out
                    if is_conflict:
                        ai_resolved, original_diff_stat = await self._resolve_cherry_pick_with_ai(
                            worktree_path=worktree_path,
                            git_cmd=git_cmd,
                            github_token=github_token,
                            commit_hash=commit_hash,
                            target_branch=target_branch,
                            pr_title=commit_msg_striped,
                        )
                    else:
                        ai_resolved = False
                        original_diff_stat = ""
                    if not ai_resolved:
                        # AI not configured, disabled, or failed — manual fallback
                        output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                        await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                        redacted_out = _redact_secrets(
                            out,
                            [github_token],
                            mask_sensitive=self.github_webhook.mask_sensitive,
                        )
                        redacted_err = _redact_secrets(
                            err,
                            [github_token],
                            mask_sensitive=self.github_webhook.mask_sensitive,
                        )
                        self.logger.error(f"{self.log_prefix} Cherry pick failed: {redacted_out} --- {redacted_err}")
                        local_branch_name = f"{pull_request.head.ref}-{target_branch}"
                        await github_api_call(
                            pull_request.create_issue_comment,
                            f"**Manual cherry-pick is needed**\nCherry pick failed for "
                            f"{commit_hash} to {target_branch}:\n"
                            f"To cherry-pick run:\n"
                            "```\n"
                            f"git remote update\n"
                            f"git checkout {target_branch}\n"
                            f"git pull origin {target_branch}\n"
                            f"git checkout -b {local_branch_name}\n"
                            f"git cherry-pick {commit_hash}\n"
                            f"# If the above fails with 'is a merge but no -m option', run:\n"
                            f"# git cherry-pick -m 1 {commit_hash}\n"
                            f"git push origin {local_branch_name}\n"
                            "```",
                            logger=self.logger,
                            log_prefix=self.log_prefix,
                        )
                        return
                    cherry_pick_had_conflicts = True

                # Post-resolution verification: compare original vs cherry-picked commit scope
                if cherry_pick_had_conflicts:
                    await self._verify_cherry_pick_scope(
                        git_cmd=git_cmd,
                        github_token=github_token,
                        original_diff_stat=original_diff_stat,
                    )

                # Restore original PR author on cherry-pick for DCO compliance
                await self._restore_original_author_for_cherry_pick(
                    pull_request=pull_request,
                    git_cmd=git_cmd,
                    github_token=github_token,
                )

                # Run pre-commit auto-fix if enabled for the repo
                if self.github_webhook.pre_commit:
                    self.logger.info(f"{self.log_prefix} Running pre-commit on cherry-pick worktree")
                    pre_commit_cmd = f"uvx --directory {shlex.quote(worktree_path)} {PREK_STR} run --all-files"
                    rc_pc, _out_pc, _err_pc = await run_command(
                        command=pre_commit_cmd,
                        log_prefix=self.log_prefix,
                        redact_secrets=[github_token],
                        mask_sensitive=self.github_webhook.mask_sensitive,
                    )
                    if not rc_pc:
                        # Pre-commit returned non-zero — check if any files were
                        # actually modified before committing.
                        rc_diff, out_diff, err_diff = await run_command(
                            command=f"{git_cmd} diff --name-only",
                            log_prefix=self.log_prefix,
                            redact_secrets=[github_token],
                            mask_sensitive=self.github_webhook.mask_sensitive,
                        )
                        if not rc_diff:
                            # git diff itself failed — report as worktree error, not pre-commit
                            redacted_err_diff = _redact_secrets(
                                err_diff, [github_token], mask_sensitive=self.github_webhook.mask_sensitive
                            )
                            redacted_out_diff = _redact_secrets(
                                out_diff, [github_token], mask_sensitive=self.github_webhook.mask_sensitive
                            )
                            self.logger.error(
                                f"{self.log_prefix} git diff failed after pre-commit: {redacted_err_diff}"
                            )
                            output["text"] = self.check_run_handler.get_check_run_text(
                                err=redacted_err_diff, out=redacted_out_diff
                            )
                            await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                            await github_api_call(
                                pull_request.create_issue_comment,
                                "Cherry-pick failed: git diff error after pre-commit run. Check worktree state.",
                                logger=self.logger,
                                log_prefix=self.log_prefix,
                            )
                            return
                        elif out_diff.strip():
                            self.logger.info(f"{self.log_prefix} Pre-commit modified files, committing fixes")
                            rc_add, _, err_add = await run_command(
                                command=f"{git_cmd} add -A",
                                log_prefix=self.log_prefix,
                                redact_secrets=[github_token],
                                mask_sensitive=self.github_webhook.mask_sensitive,
                            )
                            if not rc_add:
                                self.logger.error(f"{self.log_prefix} git add failed after pre-commit fix: {err_add}")
                                output["text"] = self.check_run_handler.get_check_run_text(err=err_add, out="")
                                await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                                await github_api_call(
                                    pull_request.create_issue_comment,
                                    "Cherry-pick pre-commit auto-fix failed during `git add`. "
                                    "Manual intervention needed.",
                                    logger=self.logger,
                                    log_prefix=self.log_prefix,
                                )
                                return
                            rc_commit, _, err_commit = await run_command(
                                command=(
                                    f"{git_cmd} commit -m"
                                    f" {shlex.quote('pre-commit auto-fix for cherry-pick')}"
                                    " --signoff --no-verify"
                                ),
                                log_prefix=self.log_prefix,
                                redact_secrets=[github_token],
                                mask_sensitive=self.github_webhook.mask_sensitive,
                            )
                            if not rc_commit:
                                self.logger.error(
                                    f"{self.log_prefix} git commit failed after pre-commit fix: {err_commit}"
                                )
                                output["text"] = self.check_run_handler.get_check_run_text(err=err_commit, out="")
                                await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                                await github_api_call(
                                    pull_request.create_issue_comment,
                                    "Cherry-pick pre-commit auto-fix failed during `git commit`. "
                                    "Manual intervention needed.",
                                    logger=self.logger,
                                    log_prefix=self.log_prefix,
                                )
                                return
                        else:
                            # Pre-commit failed with no fixable changes — abort
                            self.logger.error(f"{self.log_prefix} Pre-commit failed with unfixable errors")
                            output["text"] = self.check_run_handler.get_check_run_text(err=_err_pc, out=_out_pc)
                            await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                            await github_api_call(
                                pull_request.create_issue_comment,
                                "Cherry-pick pre-commit check failed with unfixable errors. "
                                "Manual intervention needed.",
                                logger=self.logger,
                                log_prefix=self.log_prefix,
                            )
                            return
                    else:
                        self.logger.debug(f"{self.log_prefix} Pre-commit passed without modifications")

                # Push the branch
                rc, out, err = await run_command(
                    command=push_command,
                    log_prefix=self.log_prefix,
                    redact_secrets=[github_token],
                    mask_sensitive=self.github_webhook.mask_sensitive,
                )
                if not rc:
                    output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                    await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                    self.logger.error(f"{self.log_prefix} Cherry pick push failed")
                    return

                cherry_picked_label = f"{CHERRY_PICKED_LABEL}-from-{source_branch}"[:49]

                label_flags = f" --label {shlex.quote(cherry_picked_label)}"
                if cherry_pick_had_conflicts:
                    label_flags += f" --label {shlex.quote(AI_RESOLVED_CONFLICTS_LABEL)}"

                gh_pr_command = (
                    f"gh pr create --repo {shlex.quote(repo_full_name)}"
                    f" --base {shlex.quote(target_branch)}"
                    f" --head {shlex.quote(new_branch_name)}"
                    f"{label_flags}"
                    f" --title {shlex.quote(pr_title)}"
                    f" --body {shlex.quote(pr_body)}"
                )

                # Use GitHub App installation token for PR creation
                # so the PR is owned by the app bot, allowing repo collaborators to push
                try:
                    app_token = await github_api_call(
                        get_repository_github_app_token,
                        config_=self.github_webhook.config,
                        repository_name=self.github_webhook.repository_full_name,
                        logger=self.logger,
                        log_prefix=self.log_prefix,
                    )
                except Exception:
                    self.logger.exception(
                        f"{self.log_prefix} Failed to get GitHub App token, falling back to webhook token"
                    )
                    app_token = None
                pr_create_token = app_token or github_token

                # Run gh pr create with GH_TOKEN passed via env
                rc, out, err = await run_command(
                    command=gh_pr_command,
                    log_prefix=self.log_prefix,
                    redact_secrets=[github_token, pr_create_token],
                    mask_sensitive=self.github_webhook.mask_sensitive,
                    env={**os.environ, "GH_TOKEN": pr_create_token},
                )
                if not rc:
                    output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                    await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)
                    body = (
                        "**Cherry-pick branch created, but PR creation failed**\n"
                        f"Branch `{new_branch_name}` was pushed to the repository.\n"
                        f"Create the PR manually:\n"
                        "```\n"
                        f"gh pr create --repo {repo_full_name}"
                        f" --base {target_branch}"
                        f" --head {new_branch_name}"
                        f" --label {cherry_picked_label}"
                        + (f" --label {AI_RESOLVED_CONFLICTS_LABEL}" if cherry_pick_had_conflicts else "")
                        + f" --title '{pr_title}'"
                        f" --body '{pr_body}'\n"
                        "```"
                    )
                    await github_api_call(
                        pull_request.create_issue_comment,
                        body,
                        logger=self.logger,
                        log_prefix=self.log_prefix,
                    )
                    redacted_out = _redact_secrets(
                        out,
                        [github_token, pr_create_token],
                        mask_sensitive=self.github_webhook.mask_sensitive,
                    )
                    redacted_err = _redact_secrets(
                        err,
                        [github_token, pr_create_token],
                        mask_sensitive=self.github_webhook.mask_sensitive,
                    )
                    self.logger.error(
                        f"{self.log_prefix} Cherry pick PR creation failed: {redacted_out} --- {redacted_err}"
                    )
                    return

                # gh pr create outputs the PR URL (e.g., https://github.com/org/repo/pull/123)
                cherry_pick_pr_url = out.strip()

                # Get the cherry-pick PR object
                try:
                    pr_number = int(cherry_pick_pr_url.rstrip("/").split("/")[-1])
                    cherry_pick_pr = await github_api_call(
                        self.repository.get_pull, pr_number, logger=self.logger, log_prefix=self.log_prefix
                    )
                except Exception:
                    self.logger.exception(
                        f"{self.log_prefix} Failed to get cherry-pick PR from URL: {cherry_pick_pr_url}"
                    )
                    cherry_pick_pr = None

                if cherry_pick_pr:
                    # Assign the PR to the original author (or fallback approver)
                    if assign_to_pr_owner:
                        try:
                            await github_api_call(
                                cherry_pick_pr.add_to_assignees,
                                pr_author,
                                logger=self.logger,
                                log_prefix=self.log_prefix,
                            )
                            self.logger.info(
                                f"{self.log_prefix} Assigned {pr_author} to cherry-pick PR #{cherry_pick_pr.number}"
                            )
                        except Exception:
                            self.logger.debug(
                                f"{self.log_prefix} Could not assign {pr_author} to cherry-pick PR"
                                " (may not be a collaborator), trying fallback"
                            )
                            try:
                                fallback_approvers = self.owners_file_handler.root_approvers
                                if fallback_approvers:
                                    await github_api_call(
                                        cherry_pick_pr.add_to_assignees,
                                        fallback_approvers[0],
                                        logger=self.logger,
                                        log_prefix=self.log_prefix,
                                    )
                                    self.logger.info(
                                        f"{self.log_prefix} Assigned fallback approver"
                                        f" {fallback_approvers[0]} to cherry-pick PR #{cherry_pick_pr.number}"
                                    )
                                else:
                                    self.logger.warning(
                                        f"{self.log_prefix} No fallback approvers found in OWNERS file"
                                        f" for cherry-pick PR #{cherry_pick_pr.number}"
                                    )
                            except Exception:
                                self.logger.exception(
                                    f"{self.log_prefix} Could not assign any user"
                                    f" to cherry-pick PR #{cherry_pick_pr.number}"
                                )

                    # Add labels to the created PR via PyGithub (auto-creates labels if needed)
                    try:
                        labels_to_add = [cherry_picked_label]
                        if cherry_pick_had_conflicts:
                            labels_to_add.append(AI_RESOLVED_CONFLICTS_LABEL)
                        await github_api_call(
                            cherry_pick_pr.add_to_labels, *labels_to_add, logger=self.logger, log_prefix=self.log_prefix
                        )
                        self.logger.info(
                            f"{self.log_prefix} Added labels {labels_to_add} to cherry-pick PR #{cherry_pick_pr.number}"
                        )
                    except Exception:
                        self.logger.exception(f"{self.log_prefix} Failed to add labels to cherry-pick PR")
                        # Labels are critical for auto-verify skip — warn if they couldn't be added
                        try:
                            await github_api_call(
                                pull_request.create_issue_comment,
                                f"**Warning:** Failed to add labels to cherry-pick PR {cherry_pick_pr_url}. "
                                f"Please manually add the `{cherry_picked_label}` label"
                                + (f" and `{AI_RESOLVED_CONFLICTS_LABEL}` label" if cherry_pick_had_conflicts else "")
                                + " to ensure correct auto-verify behavior.",
                                logger=self.logger,
                                log_prefix=self.log_prefix,
                            )
                        except Exception:
                            self.logger.exception(f"{self.log_prefix} Failed to post label warning comment")

                    # Request review from original PR author (independent of label success)
                    try:
                        await github_api_call(
                            cherry_pick_pr.create_review_request,
                            reviewers=[pr_author],
                            logger=self.logger,
                            log_prefix=self.log_prefix,
                        )
                    except Exception:
                        self.logger.debug(
                            f"{self.log_prefix} Could not request review from {pr_author} (may not be a collaborator)"
                        )
                else:
                    # PR was created but we couldn't fetch it — labels/reviewer not added
                    await github_api_call(
                        pull_request.create_issue_comment,
                        f"**Warning:** Cherry-pick PR was created ({cherry_pick_pr_url}) but failed to add labels. "
                        f"Please manually add the `{cherry_picked_label}` label"
                        + (f" and `{AI_RESOLVED_CONFLICTS_LABEL}` label" if cherry_pick_had_conflicts else "")
                        + " to ensure correct auto-verify behavior.",
                        logger=self.logger,
                        log_prefix=self.log_prefix,
                    )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
            await self.check_run_handler.set_check_success(name=CHERRY_PICKED_LABEL, output=output)

            if cherry_pick_had_conflicts:
                ai_config = self.github_webhook.ai_features
                ai_result = get_ai_config(ai_config)
                ai_provider, ai_model = ai_result if ai_result else ("unknown", "unknown")
                try:
                    await github_api_call(
                        pull_request.create_issue_comment,
                        f"**Cherry-pick conflicts were resolved by AI**\n\n"
                        f"Cherry-picked PR {pull_request.title} into {target_branch}: {cherry_pick_pr_url}\n"
                        f"Conflicts were automatically resolved by AI ({ai_provider}/{ai_model}).\n\n"
                        f"@{pr_author} **Manual verification is required** — "
                        f"please review the changes and test before merging.",
                        logger=self.logger,
                        log_prefix=self.log_prefix,
                    )
                except asyncio.CancelledError:
                    raise
                except GithubException:
                    self.logger.exception(
                        f"{self.log_prefix} Failed to post AI-conflict-resolution comment"
                        f" on original PR #{pull_request.number}"
                    )

                if cherry_pick_pr:
                    try:
                        await github_api_call(
                            cherry_pick_pr.create_issue_comment,
                            f"**⚠️ This cherry-pick had conflicts resolved by AI ({ai_provider}/{ai_model})**\n\n"
                            f"@{pr_author} — AI automatically resolved merge conflicts for this cherry-pick. "
                            f"Please review the changes carefully and verify correctness before merging.",
                            logger=self.logger,
                            log_prefix=self.log_prefix,
                        )
                        self.logger.info(
                            f"{self.log_prefix} Posted AI-conflict-resolution comment on cherry-pick"
                            f" PR #{cherry_pick_pr.number}"
                        )
                    except asyncio.CancelledError:
                        raise
                    except GithubException:
                        self.logger.exception(
                            f"{self.log_prefix} Failed to post AI-conflict-resolution comment"
                            f" on cherry-pick PR #{cherry_pick_pr.number}"
                        )
            else:
                await github_api_call(
                    pull_request.create_issue_comment,
                    f"Cherry-picked PR {pull_request.title} into {target_branch}: {cherry_pick_pr_url}",
                    logger=self.logger,
                    log_prefix=self.log_prefix,
                )

    async def rebase_pr(self, pull_request: PullRequest, reviewed_user: str) -> None:
        """Rebase a PR branch onto its base branch.

        Fetches the base branch and rebases the PR head branch onto it,
        then force-pushes the result. For bot-owned PRs (e.g., cherry-pick PRs),
        validates the user is the cherry-pick initiator (PR assignee) or a maintainer.

        Args:
            pull_request: The pull request to rebase
            reviewed_user: User who requested the rebase
        """
        pr_state = await github_api_call(lambda: pull_request.state, logger=self.logger, log_prefix=self.log_prefix)
        if pr_state != "open":
            msg = "Rebase can only be used on open PRs"
            self.logger.debug(f"{self.log_prefix} {msg}")
            await github_api_call(
                pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
            )
            return

        # Reject fork PRs — force-push would target the base repo
        head_repo_full_name = await github_api_call(
            lambda: pull_request.head.repo.full_name, logger=self.logger, log_prefix=self.log_prefix
        )
        if head_repo_full_name != self.github_webhook.repository_full_name:
            msg = "Rebase is not supported for fork PRs — the head branch is in a different repository."
            self.logger.debug(f"{self.log_prefix} {msg}")
            await github_api_call(
                pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
            )
            return

        pr_user_login = await github_api_call(
            lambda: pull_request.user.login, logger=self.logger, log_prefix=self.log_prefix
        )

        # Check if PR was created by our app's bot
        # If app_bot_login is not set, we can't detect bot PRs — treat as user-owned
        if self.github_webhook.app_bot_login:
            is_bot_pr = pr_user_login == self.github_webhook.app_bot_login
        else:
            is_bot_pr = False
            self.logger.warning(
                f"{self.log_prefix} app_bot_login not set — cannot detect bot PRs, treating as user-owned"
            )

        if is_bot_pr:
            # For bot-owned PRs, validate the user is the PR assignee or a maintainer
            assignees = await github_api_call(
                lambda: [a.login for a in pull_request.assignees],
                logger=self.logger,
                log_prefix=self.log_prefix,
            )
            maintainers = await self.owners_file_handler.get_all_repository_maintainers()
            if reviewed_user not in assignees and reviewed_user not in maintainers:
                msg = (
                    f"@{reviewed_user} is not authorized to rebase this bot-owned PR.\n"
                    "Only the PR assignee (cherry-pick initiator) or maintainers can rebase."
                )
                self.logger.debug(f"{self.log_prefix} {msg}")
                await github_api_call(
                    pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                )
                return
        else:
            # For user-owned PRs, only the PR owner or maintainers can rebase
            if reviewed_user != pr_user_login:
                maintainers = await self.owners_file_handler.get_all_repository_maintainers()
                if reviewed_user not in maintainers:
                    msg = (
                        f"@{reviewed_user} is not authorized to rebase this PR.\n"
                        "Only the PR owner or maintainers can rebase."
                    )
                    self.logger.debug(f"{self.log_prefix} {msg}")
                    await github_api_call(
                        pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                    )
                    return

        base_ref = await github_api_call(lambda: pull_request.base.ref, logger=self.logger, log_prefix=self.log_prefix)
        head_ref = await github_api_call(lambda: pull_request.head.ref, logger=self.logger, log_prefix=self.log_prefix)
        github_token = self.github_webhook.token

        self.logger.info(f"{self.log_prefix} Rebasing {head_ref} onto {base_ref}")

        async with self._checkout_worktree(pull_request=pull_request, skip_merge=True) as (
            success,
            worktree_path,
            out,
            err,
        ):
            if not success:
                msg = "Failed to prepare worktree for rebase"
                self.logger.error(f"{self.log_prefix} {msg}: {out} --- {err}")
                await github_api_call(
                    pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                )
                return

            git_cmd = f"git --work-tree={shlex.quote(worktree_path)} --git-dir={shlex.quote(worktree_path + '/.git')}"

            # Checkout the PR head branch
            rc, out, err = await run_command(
                command=f"{git_cmd} checkout {shlex.quote(head_ref)}",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                redacted_err = _redact_secrets(err, [github_token], mask_sensitive=self.github_webhook.mask_sensitive)
                msg = f"Failed to checkout branch `{head_ref}`: {redacted_err}"
                self.logger.error(f"{self.log_prefix} {msg}")
                await github_api_call(
                    pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                )
                return

            # Fetch the base branch
            rc, out, err = await run_command(
                command=f"{git_cmd} fetch origin {shlex.quote(base_ref)}",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                redacted_err = _redact_secrets(err, [github_token], mask_sensitive=self.github_webhook.mask_sensitive)
                msg = f"Failed to fetch base branch `{base_ref}`: {redacted_err}"
                self.logger.error(f"{self.log_prefix} {msg}")
                await github_api_call(
                    pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                )
                return

            # Rebase onto base branch
            rc, out, err = await run_command(
                command=f"{git_cmd} rebase origin/{shlex.quote(base_ref)}",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                # Abort the rebase to clean up
                await run_command(
                    command=f"{git_cmd} rebase --abort",
                    log_prefix=self.log_prefix,
                    redact_secrets=[github_token],
                    mask_sensitive=self.github_webhook.mask_sensitive,
                )
                redacted_err = _redact_secrets(err, [github_token], mask_sensitive=self.github_webhook.mask_sensitive)
                redacted_out = _redact_secrets(out, [github_token], mask_sensitive=self.github_webhook.mask_sensitive)
                msg = (
                    f"**Rebase failed** for `{head_ref}` onto `{base_ref}`:\n"
                    f"```\n{redacted_out}\n{redacted_err}\n```\n"
                    "Please resolve conflicts manually."
                )
                self.logger.error(f"{self.log_prefix} Rebase failed: {redacted_out} --- {redacted_err}")
                await github_api_call(
                    pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                )
                return

            # Force push the rebased branch
            rc, out, err = await run_command(
                command=f"{git_cmd} push --force-with-lease origin {shlex.quote(head_ref)}",
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.github_webhook.mask_sensitive,
            )
            if not rc:
                redacted_err = _redact_secrets(err, [github_token], mask_sensitive=self.github_webhook.mask_sensitive)
                msg = f"Rebase succeeded but push failed for `{head_ref}`: {redacted_err}"
                self.logger.error(f"{self.log_prefix} {msg}")
                await github_api_call(
                    pull_request.create_issue_comment, msg, logger=self.logger, log_prefix=self.log_prefix
                )
                return

            self.logger.info(f"{self.log_prefix} Successfully rebased {head_ref} onto {base_ref}")
            await github_api_call(
                pull_request.create_issue_comment,
                f"Successfully rebased `{head_ref}` onto `{base_ref}` ✅",
                logger=self.logger,
                log_prefix=self.log_prefix,
            )

    async def run_retests(self, supported_retests: list[str], pull_request: PullRequest) -> None:
        """Run the specified retests for a pull request.

        Args:
            supported_retests: List of test names to run (e.g., ['tox', 'pre-commit'])
            pull_request: The PullRequest object to run tests for
        """
        if not supported_retests:
            self.logger.debug(f"{self.log_prefix} No retests to run")
            return

        # Map check names to runner functions
        _retests_to_func_map: dict[str, Callable[..., Coroutine[Any, Any, None]]] = {
            TOX_STR: self.run_tox,
            PRE_COMMIT_STR: self.run_pre_commit,
            BUILD_CONTAINER_STR: self.run_build_container,
            PYTHON_MODULE_INSTALL_STR: self.run_install_python_module,
            CONVENTIONAL_TITLE_STR: self.run_conventional_title_check,
        }

        # Add custom check runs to the retest map
        # Note: custom checks are validated in GithubWebhook._validate_custom_check_runs()
        # so name is guaranteed to exist
        for custom_check in self.github_webhook.custom_check_runs:
            check_key = custom_check["name"]
            _retests_to_func_map[check_key] = partial(self.run_custom_check, check_config=custom_check)

        # Add security checks to the retest map
        # Security methods don't take pull_request param, so wrap with lambda
        _retests_to_func_map[SECURITY_COMMITTER_IDENTITY_STR] = lambda **_kwargs: self.run_security_committer_identity()
        _retests_to_func_map[SECURITY_SUSPICIOUS_PATHS_STR] = lambda **_kwargs: self.run_security_suspicious_paths()

        tasks: list[Coroutine[Any, Any, Any] | Task[Any]] = []
        scheduled_tests: list[str] = []
        for _test in supported_retests:
            runner = _retests_to_func_map.get(_test)
            if runner is None:
                self.logger.error(f"{self.log_prefix} Unknown retest '{_test}' requested, skipping")
                continue
            self.logger.debug(f"{self.log_prefix} running retest {_test}")
            task = asyncio.create_task(runner(pull_request=pull_request))
            tasks.append(task)
            scheduled_tests.append(_test)

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, asyncio.CancelledError):
                self.logger.debug(f"{self.log_prefix} Retest task cancelled")
                raise result  # Re-raise CancelledError
            elif isinstance(result, BaseException):
                # Get the test name from scheduled_tests list for correct error attribution
                test_name = scheduled_tests[idx] if idx < len(scheduled_tests) else "unknown"
                self.logger.error(f"{self.log_prefix} Retest '{test_name}' failed: {result}")
