import asyncio
import contextlib
import re
import shlex
import shutil
from asyncio import Task
from collections.abc import AsyncGenerator, Callable, Coroutine
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

import shortuuid
from github.Branch import Branch
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.handlers.check_run_handler import CheckRunHandler, CheckRunOutput
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.utils import helpers as helpers_module
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CHERRY_PICKED_LABEL,
    CONVENTIONAL_TITLE_STR,
    PRE_COMMIT_STR,
    PREK_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
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
    ) -> AsyncGenerator[tuple[bool, str, str, str]]:
        """Create worktree from existing clone for handler operations.

        Uses centralized clone from github_webhook.clone_repo_dir and creates
        a worktree for isolated checkout operations. No cloning happens here.

        Args:
            pull_request: Pull request object
            is_merged: Whether PR is merged
            checkout: Specific branch/commit to checkout
            tag_name: Tag name to checkout

        Yields:
            tuple: (success: bool, worktree_path: str, stdout: str, stderr: str)
        """
        pr_number: int | None = None
        base_ref: str | None = None
        if pull_request:
            pr_number = await asyncio.to_thread(lambda: pull_request.number)
            base_ref = await asyncio.to_thread(lambda: pull_request.base.ref)

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
            if success and pull_request and not is_merged and not tag_name:
                merge_ref = base_ref
                if merge_ref is None:
                    merge_ref = await asyncio.to_thread(lambda: pull_request.base.ref)
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
        # Wrap PyGithub property access in asyncio.to_thread to avoid blocking
        base_ref = await asyncio.to_thread(lambda: pull_request.base.ref)
        _tox_tests = self.github_webhook.tox.get(base_ref, "")

        # Build tox command with {worktree_path} placeholder
        cmd = f"uvx {python_ver} {TOX_STR} --workdir {{worktree_path}} --root {{worktree_path}} -c {{worktree_path}}"
        if _tox_tests and _tox_tests != "all":
            tests = _tox_tests.replace(" ", "")
            cmd += f" -e {tests}"

        check_config = CheckConfig(name=TOX_STR, command=cmd, title="Tox")
        await self.run_check(pull_request=pull_request, check_config=check_config)

    async def run_pre_commit(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.pre_commit:
            self.logger.debug(f"{self.log_prefix} Pre-commit not configured for this repository")
            return

        cmd = f"uvx --directory {{worktree_path}} {PREK_STR} run --all-files"
        check_config = CheckConfig(name=PRE_COMMIT_STR, command=cmd, title="Pre-Commit")
        await self.run_check(pull_request=pull_request, check_config=check_config)

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
            # Build container build command with worktree path
            build_cmd: str = (
                f"--network=host {no_cache} -f "
                f"{worktree_path}/{self.github_webhook.dockerfile} "
                f"{worktree_path} -t {_container_repository_and_tag}"
            )

            if self.github_webhook.container_build_args:
                build_args = " ".join(f"--build-arg {arg}" for arg in self.github_webhook.container_build_args)
                build_cmd = f"{build_args} {build_cmd}"

            if self.github_webhook.container_command_args:
                build_cmd = f"{' '.join(self.github_webhook.container_command_args)} {build_cmd}"

            if command_args:
                build_cmd = f"{command_args} {build_cmd}"

            podman_build_cmd: str = f"podman build {build_cmd}"
            self.logger.debug(f"{self.log_prefix} Podman build command to run: {podman_build_cmd}")

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
                        await asyncio.to_thread(pull_request.create_issue_comment, push_msg)

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
                        await asyncio.to_thread(pull_request.create_issue_comment, err_msg)

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
        allowed_names = [name.strip() for name in self.github_webhook.conventional_title.split(",") if name.strip()]
        title = pull_request.title

        self.logger.debug(f"{self.log_prefix} Conventional title check for title: {title}, allowed: {allowed_names}")
        # Use generator expression instead of list comprehension inside any() for efficiency
        if any(re.match(rf"^{re.escape(_name)}(\([^)]+\))?!?: .+", title) for _name in allowed_names):
            await self.check_run_handler.set_check_success(name=CONVENTIONAL_TITLE_STR, output=output)
        else:
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
{", ".join(f"`{t}`" for t in allowed_names)}

**Valid examples:**
- `feat: add user authentication`
- `fix(parser): handle edge case in URL parsing`
- `feat!: breaking change in API response`
- `refactor(core)!: major architectural change`
- `docs: update installation guide`

**Format rules:**
- Type must be one of the configured types
- Optional scope in parentheses: `(scope)`
- Optional breaking change indicator: `!`
- **Mandatory**: colon followed by space `: `
- **Mandatory**: non-empty description after the space

**Note:** The Conventional Commits specification allows custom types beyond the standard recommendations.
Your team can configure additional types in the repository settings.

**Resources:**
- [Conventional Commits v1.0.0 Specification](https://www.conventionalcommits.org/en/v1.0.0/)
"""
            await self.check_run_handler.set_check_failure(name=CONVENTIONAL_TITLE_STR, output=output)

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

    async def is_branch_exists(self, branch: str) -> Branch:
        return await asyncio.to_thread(self.repository.get_branch, branch)

    async def cherry_pick(
        self,
        pull_request: PullRequest,
        target_branch: str,
        reviewed_user: str = "",
        *,
        assign_to_pr_owner: bool = True,
    ) -> None:
        """Cherry-pick a merged PR to a target branch.

        Args:
            pull_request: The merged pull request to cherry-pick.
            target_branch: Branch to cherry-pick into.
            reviewed_user: The user who triggered the cherry-pick. Used in PR description and logging.
            assign_to_pr_owner: Assign the cherry-pick PR to the original PR owner. Default True.
        """
        pr_author = await asyncio.to_thread(lambda: pull_request.user.login)
        self.logger.info(
            f"{self.log_prefix} Cherry-pick to {target_branch} requested by {reviewed_user or pr_author}, "
            f"PR owner: {pr_author}"
        )

        assignee_flag = f" -a {shlex.quote(pr_author)}" if assign_to_pr_owner else ""
        self.logger.debug(f"{self.log_prefix} Cherry-pick PR assignee: {pr_author if assign_to_pr_owner else 'none'}")

        new_branch_name = f"{CHERRY_PICKED_LABEL}-{pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not await self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.error(err_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, err_msg)

        else:
            await self.check_run_handler.set_check_in_progress(name=CHERRY_PICKED_LABEL)
            commit_hash = pull_request.merge_commit_sha
            commit_msg_striped = pull_request.title.replace("'", "")
            pull_request_url = pull_request.html_url
            github_token = self.github_webhook.token
            source_branch = pull_request.base.ref
            reviewed_user_or_author = reviewed_user or pr_author

            async with self._checkout_worktree(pull_request=pull_request) as (success, worktree_path, out, err):
                git_cmd = f"git --work-tree={worktree_path} --git-dir={worktree_path}/.git"
                hub_cmd = f"GITHUB_TOKEN={github_token} hub --work-tree={worktree_path} --git-dir={worktree_path}/.git"
                commands: list[str] = [
                    f"{git_cmd} checkout {target_branch}",
                    f"{git_cmd} pull origin {target_branch}",
                    f"{git_cmd} checkout -b {new_branch_name} origin/{target_branch}",
                    f"{git_cmd} cherry-pick {commit_hash}",
                    f"{git_cmd} push origin {new_branch_name}",
                    f'bash -c "{hub_cmd} pull-request -b {target_branch} '
                    f"-h {new_branch_name} -l {CHERRY_PICKED_LABEL}{assignee_flag} "
                    f"-m '{CHERRY_PICKED_LABEL}: [{target_branch}] "
                    f"{commit_msg_striped}' -m 'Cherry-pick from {source_branch} "
                    f"requested by {reviewed_user_or_author}, PR owner: {pr_author}' "
                    f"-m '{pull_request_url}'\"",
                ]

                output: CheckRunOutput = {
                    "title": "Cherry-pick details",
                    "summary": "",
                    "text": None,
                }
                if not success:
                    output["text"] = self.check_run_handler.get_check_run_text(out=out, err=err)
                    await self.check_run_handler.set_check_failure(name=CHERRY_PICKED_LABEL, output=output)

                for cmd in commands:
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
                        await asyncio.to_thread(
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
                            f"git push origin {local_branch_name}\n"
                            "```",
                        )
                        return

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            await self.check_run_handler.set_check_success(name=CHERRY_PICKED_LABEL, output=output)
            await asyncio.to_thread(
                pull_request.create_issue_comment, f"Cherry-picked PR {pull_request.title} into {target_branch}"
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
