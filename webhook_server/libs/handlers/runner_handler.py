import asyncio
import contextlib
import os
import re
import shlex
import shutil
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import shortuuid
from github.GithubException import GithubException
from github.Repository import Repository

from webhook_server.libs.graphql.graphql_client import GraphQLError
from webhook_server.libs.graphql.webhook_data import PullRequestWrapper
from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CHERRY_PICKED_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    PRE_COMMIT_STR,
    PREK_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server.utils.helpers import format_task_fields, run_command
from webhook_server.utils.notification_utils import send_slack_message

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


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

    async def _get_pr_node_id(self, pull_request: PullRequestWrapper) -> str:
        """Get PR node ID for GraphQL operations.

        Args:
            pull_request: Pull request wrapper object

        Returns:
            GraphQL node ID for the pull request
        """
        return pull_request.id

    @contextlib.asynccontextmanager
    async def _prepare_cloned_repo_dir(
        self,
        clone_repo_dir: str,
        pull_request: PullRequestWrapper | None = None,
        is_merged: bool = False,
        checkout: str = "",
        tag_name: str = "",
    ) -> AsyncGenerator[tuple[bool, Any, Any], None]:
        # Quote paths to handle spaces in directory names
        git_cmd = f'git --work-tree="{clone_repo_dir}" --git-dir="{clone_repo_dir}/.git"'
        self.logger.debug(f"{self.log_prefix} Preparing cloned repo dir {clone_repo_dir} with git cmd: {git_cmd}")
        result: tuple[bool, str, str] = (True, "", "")
        success = True

        try:
            # Clone with token embedded in URL for thread-safety (each clone gets its own URL)
            # Format: https://x-access-token:TOKEN@github.com/owner/repo.git  # pragma: allowlist secret
            # This is thread-safe unlike environment variables which can be overridden by concurrent clones
            clone_url_with_token = self.repository.clone_url.replace(
                "https://", f"https://x-access-token:{self.github_webhook.token}@"
            )

            rc, out, err = await run_command(
                command=f"git clone {clone_url_with_token} {clone_repo_dir}",
                log_prefix=self.log_prefix,
                redact_secrets=[self.github_webhook.token],
            )
            if not rc:
                result = (rc, out, err)
                success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} config user.name '{self.repository.owner.login}'",
                    log_prefix=self.log_prefix,
                    redact_secrets=[self.github_webhook.token],
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                # Guard against missing owner email (may be None for some organizations)
                owner_email = self.repository.owner.email or "noreply@github.com"
                rc, out, err = await run_command(
                    command=f"{git_cmd} config user.email '{owner_email}'",
                    log_prefix=self.log_prefix,
                    redact_secrets=[self.github_webhook.token],
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(
                    command=(
                        f"{git_cmd} config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*"
                    ),
                    log_prefix=self.log_prefix,
                    redact_secrets=[self.github_webhook.token],
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} remote update",
                    log_prefix=self.log_prefix,
                    redact_secrets=[self.github_webhook.token],
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if checkout and success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} checkout {checkout}",
                    log_prefix=self.log_prefix,
                    redact_secrets=[self.github_webhook.token],
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

                if success and pull_request:
                    rc, out, err = await run_command(
                        command=f"{git_cmd} merge origin/{pull_request.base.ref} -m 'Merge {pull_request.base.ref}'",
                        log_prefix=self.log_prefix,
                        redact_secrets=[self.github_webhook.token],
                    )
                    if not rc:
                        result = (rc, out, err)
                        success = False

            else:
                if success:
                    if is_merged and pull_request:
                        rc, out, err = await run_command(
                            command=f"{git_cmd} checkout {pull_request.base.ref}",
                            log_prefix=self.log_prefix,
                            redact_secrets=[self.github_webhook.token],
                        )
                        if not rc:
                            result = (rc, out, err)
                            success = False

                    elif tag_name:
                        rc, out, err = await run_command(
                            command=f"{git_cmd} checkout {tag_name}",
                            log_prefix=self.log_prefix,
                            redact_secrets=[self.github_webhook.token],
                        )
                        if not rc:
                            result = (rc, out, err)
                            success = False

                    elif not is_merged and not tag_name:
                        try:
                            if pull_request:
                                rc, out, err = await run_command(
                                    command=f"{git_cmd} checkout origin/pr/{pull_request.number}",
                                    log_prefix=self.log_prefix,
                                    redact_secrets=[self.github_webhook.token],
                                )
                                if not rc:
                                    result = (rc, out, err)
                                    success = False

                                if pull_request and success:
                                    rc, out, err = await run_command(
                                        command=(
                                            f"{git_cmd} merge origin/{pull_request.base.ref} "
                                            f"-m 'Merge {pull_request.base.ref}'"
                                        ),
                                        log_prefix=self.log_prefix,
                                        redact_secrets=[self.github_webhook.token],
                                    )
                                    if not rc:
                                        result = (rc, out, err)
                                        success = False
                        except Exception:
                            pr_number = pull_request.number if pull_request else "unknown"
                            self.logger.exception(f"{self.log_prefix} Failed to checkout pull request {pr_number}")

        finally:
            yield result
            self.logger.debug(f"{self.log_prefix} Deleting {clone_repo_dir}")
            shutil.rmtree(clone_repo_dir, ignore_errors=True)

    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        # Derive UID dynamically for portability and security
        uid = os.getuid()
        containers_path = f"/tmp/storage-run-{uid}/containers"
        libpod_tmp_path = f"/tmp/storage-run-{uid}/libpod/tmp"

        # Guard against symlinks to prevent security vulnerabilities
        for path in [containers_path, libpod_tmp_path]:
            if os.path.exists(path):
                # Verify path is not a symlink before removal
                if os.path.islink(path):
                    self.logger.warning(f"{self.log_prefix} Skipping symlink removal: {path}")
                    continue
                # Additional security: Verify path is under /tmp
                if not os.path.realpath(path).startswith("/tmp/"):
                    self.logger.warning(f"{self.log_prefix} Skipping unsafe path removal: {path}")
                    continue
                shutil.rmtree(path, ignore_errors=True)

    async def run_podman_command(
        self, command: str, redact_secrets: list[str] | None = None, timeout: int | None = None
    ) -> tuple[bool, str, str]:
        rc, out, err = await run_command(
            command=command, log_prefix=self.log_prefix, redact_secrets=redact_secrets, timeout=timeout
        )

        if rc:
            return rc, out, err

        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return await run_command(
                command=command, log_prefix=self.log_prefix, redact_secrets=redact_secrets, timeout=timeout
            )

        return rc, out, err

    async def _push_container(
        self,
        container_repository_and_tag: str | None,
        pull_request: PullRequestWrapper | None = None,
    ) -> None:
        """Push container to registry.

        Extracted from run_build_container for better separation of concerns.

        Args:
            container_repository_and_tag: Full container image tag (registry/repo:tag)
            pull_request: Pull request object for commenting on push status
        """
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting container push to registry",
        )
        # Extract registry from image tag (format: registry/repo:tag)
        if not container_repository_and_tag:
            self.logger.error(f"{self.log_prefix} No container repository and tag specified for push")
            return

        registry = container_repository_and_tag.split("/")[0] if "/" in container_repository_and_tag else "docker.io"

        # Login securely via stdin to avoid exposing credentials in process args
        # Pass password via stdin (with newline) for maximum security
        # Username is provided via --username flag, password comes from stdin
        # Shell-quote username to handle special characters safely
        quoted_username = shlex.quote(self.github_webhook.container_repository_username)
        login_cmd = f"podman login --username {quoted_username} --password-stdin {registry}"
        login_password = f"{self.github_webhook.container_repository_password}\n"
        login_rc, _, _ = await run_command(
            command=login_cmd,
            log_prefix=self.log_prefix,
            stdin_input=login_password,
            redact_secrets=[
                self.github_webhook.container_repository_username,
                self.github_webhook.container_repository_password,
            ],
        )

        if not login_rc:
            self.logger.error(f"{self.log_prefix} Failed to login to container registry {registry}")
            return

        push_cmd = f"podman push {container_repository_and_tag}"
        push_secrets = [
            self.github_webhook.token,
            self.github_webhook.container_repository_username,
            self.github_webhook.container_repository_password,
        ]
        push_rc, push_out, push_err = await self.run_podman_command(command=push_cmd, redact_secrets=push_secrets)
        if push_out:
            self.logger.debug(f"{self.log_prefix} Podman push stdout: {push_out}")
        if push_err:
            self.logger.debug(f"{self.log_prefix} Podman push stderr: {push_err}")
        if push_rc:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                f"Container push completed successfully",
            )
            push_msg: str = f"New container for {container_repository_and_tag} published"
            if pull_request:
                await self.github_webhook.unified_api.add_pr_comment(pull_request=pull_request, body=push_msg)

            if self.github_webhook.slack_webhook_url:
                message = f"""
```
{self.github_webhook.repository_full_name} {push_msg}.
```
"""
                await asyncio.to_thread(
                    send_slack_message,
                    message,
                    self.github_webhook.slack_webhook_url,
                    self.github_webhook.logger,
                )

            self.logger.info(f"{self.log_prefix} Done push {container_repository_and_tag}")
        else:
            err_msg: str = f"Failed to build and push {container_repository_and_tag}"
            self.logger.error(f"{self.log_prefix} {err_msg} - stdout: {push_out}, stderr: {push_err}")
            if pull_request:
                await self.github_webhook.unified_api.add_pr_comment(pull_request=pull_request, body=err_msg)

            if self.github_webhook.slack_webhook_url:
                message = f"""
```
{self.github_webhook.repository_full_name} {err_msg}.
```
                """
                await asyncio.to_thread(
                    send_slack_message,
                    message,
                    self.github_webhook.slack_webhook_url,
                    self.github_webhook.logger,
                )

    async def run_tox(self, pull_request: PullRequestWrapper) -> None:
        if not self.github_webhook.tox:
            self.logger.debug(f"{self.log_prefix} Tox not configured for this repository")
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} Starting tox tests execution"
        )

        if await self.check_run_handler.is_check_run_in_progress(check_run=TOX_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {TOX_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        python_ver = (
            f"--python={self.github_webhook.tox_python_version}" if self.github_webhook.tox_python_version else ""
        )
        # Quote directory paths to handle spaces
        cmd = f'uvx {python_ver} {TOX_STR} --workdir "{clone_repo_dir}" --root "{clone_repo_dir}" -c "{clone_repo_dir}"'
        _tox_tests = self.github_webhook.tox.get(pull_request.base.ref, "")

        if _tox_tests and _tox_tests != "all":
            tests = _tox_tests.replace(" ", "")
            cmd += f" -e {tests}"

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Setting tox check status to in-progress",
        )
        await self.check_run_handler.set_run_tox_check_in_progress()
        self.logger.debug(f"{self.log_prefix} Tox command to run: {cmd}")

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Preparing repository clone for tox execution",
        )
        async with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir, pull_request=pull_request) as _res:
            output: dict[str, Any] = {
                "title": "Tox",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                self.logger.error(f"{self.log_prefix} Repository preparation failed for tox")
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await self.check_run_handler.set_run_tox_check_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} Executing tox command"
            )
            rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix, redact_secrets=[])

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Tox tests completed successfully",
                )
                return await self.check_run_handler.set_run_tox_check_success(output=output)
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} Tox tests failed"
                )
                return await self.check_run_handler.set_run_tox_check_failure(output=output)

    async def run_pre_commit(self, pull_request: PullRequestWrapper) -> None:
        if not self.github_webhook.pre_commit:
            self.logger.debug(f"{self.log_prefix} Pre-commit not configured for this repository")
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting pre-commit checks execution",
        )

        if await self.check_run_handler.is_check_run_in_progress(check_run=PRE_COMMIT_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {PRE_COMMIT_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        # Quote directory path to handle spaces
        cmd = f'uv run --directory "{clone_repo_dir}" {PREK_STR} run --all-files'

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Setting pre-commit check status to in-progress",
        )
        await self.check_run_handler.set_run_pre_commit_check_in_progress()

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Preparing repository clone for pre-commit execution",
        )
        async with self._prepare_cloned_repo_dir(pull_request=pull_request, clone_repo_dir=clone_repo_dir) as _res:
            output: dict[str, Any] = {
                "title": "Pre-Commit",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                self.logger.error(f"{self.log_prefix} Repository preparation failed for pre-commit")
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await self.check_run_handler.set_run_pre_commit_check_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Executing pre-commit command",
            )
            rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix, redact_secrets=[])

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Pre-commit checks completed successfully",
                )
                return await self.check_run_handler.set_run_pre_commit_check_success(output=output)
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                    f"Pre-commit checks failed",
                )
                return await self.check_run_handler.set_run_pre_commit_check_failure(output=output)

    async def run_build_container(
        self,
        pull_request: PullRequestWrapper | None = None,
        set_check: bool = True,
        push: bool = False,
        is_merged: bool = False,
        tag: str = "",
        command_args: str = "",
        reviewed_user: str | None = None,
    ) -> None:
        if not self.github_webhook.build_and_push_container:
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} Starting container build process",
        )

        if (
            self.owners_file_handler
            and reviewed_user
            and pull_request
            and not await self.owners_file_handler.is_user_valid_to_run_commands(
                reviewed_user=reviewed_user, pull_request=pull_request
            )
        ):
            return

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"

        if pull_request and set_check:
            if await self.check_run_handler.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR) and not is_merged:
                self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {BUILD_CONTAINER_STR}.")

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Setting container build check status to in-progress",
            )
            await self.check_run_handler.set_container_build_in_progress()

        _container_repository_and_tag = self.github_webhook.container_repository_and_tag(
            is_merged=is_merged,
            tag=tag,
            pull_request=pull_request,
        )
        no_cache: str = " --no-cache" if is_merged else ""
        build_cmd: str = (
            f'--network=host {no_cache} -f "{clone_repo_dir}/{self.github_webhook.dockerfile}" "{clone_repo_dir}"'
        )
        if _container_repository_and_tag:
            build_cmd += f" -t {_container_repository_and_tag}"

        if self.github_webhook.container_build_args:
            build_args = " ".join(f"--build-arg {arg}" for arg in self.github_webhook.container_build_args)
            build_cmd = f"{build_args} {build_cmd}"

        if self.github_webhook.container_command_args:
            build_cmd = f"{' '.join(self.github_webhook.container_command_args)} {build_cmd}"

        if command_args:
            build_cmd = f"{command_args} {build_cmd}"

        podman_build_cmd: str = f"podman build {build_cmd}"
        self.logger.debug(f"{self.log_prefix} Podman build command to run: {podman_build_cmd}")
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Preparing repository clone for container build",
        )
        async with self._prepare_cloned_repo_dir(
            pull_request=pull_request,
            is_merged=is_merged,
            tag_name=tag,
            clone_repo_dir=clone_repo_dir,
        ) as _res:
            output: dict[str, Any] = {
                "title": "Build container",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Executing container build command",
            )
            # Collect all potential secrets from build args and container credentials
            build_secrets = [self.github_webhook.token]
            if self.github_webhook.container_build_args:
                build_secrets.extend(self.github_webhook.container_build_args)
            build_rc, build_out, build_err = await self.run_podman_command(
                command=podman_build_cmd, redact_secrets=build_secrets
            )
            output["text"] = self.check_run_handler.get_check_run_text(err=build_err, out=build_out)

            if build_rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Container build completed successfully",
                )
                self.logger.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
                # Set check success if requested, but don't return yet if push is needed
                if pull_request and set_check and not push:
                    return await self.check_run_handler.set_container_build_success(output=output)
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                    f"Container build failed",
                )
                self.logger.error(f"{self.log_prefix} Failed to build {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_failure(output=output)

            if push and build_rc:
                await self._push_container(
                    container_repository_and_tag=_container_repository_and_tag,
                    pull_request=pull_request,
                )

    async def run_install_python_module(self, pull_request: PullRequestWrapper) -> None:
        if not self.github_webhook.pypi:
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting Python module installation",
        )

        if await self.check_run_handler.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {PYTHON_MODULE_INSTALL_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        self.logger.info(f"{self.log_prefix} Installing python module")
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Setting Python module install check status to in-progress",
        )
        await self.check_run_handler.set_python_module_install_in_progress()
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Preparing repository clone for Python module installation",
        )
        async with self._prepare_cloned_repo_dir(
            pull_request=pull_request,
            clone_repo_dir=clone_repo_dir,
        ) as _res:
            output: dict[str, Any] = {
                "title": "Python module installation",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await self.check_run_handler.set_python_module_install_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Executing Python module installation command",
            )
            rc, out, err = await run_command(
                command=f'uv build --wheel --out-dir "{clone_repo_dir}/dist" --no-cache "{clone_repo_dir}"',
                log_prefix=self.log_prefix,
                redact_secrets=[],
            )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Python module installation completed successfully",
                )
                return await self.check_run_handler.set_python_module_install_success(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Python module installation failed",
            )
            return await self.check_run_handler.set_python_module_install_failure(output=output)

    async def run_conventional_title_check(self, pull_request: PullRequestWrapper) -> None:
        if not self.github_webhook.conventional_title:
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting conventional title check",
        )

        output: dict[str, str] = {
            "title": "Conventional Title",
            "summary": "",
            "text": "",
        }

        if await self.check_run_handler.is_check_run_in_progress(check_run=CONVENTIONAL_TITLE_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {CONVENTIONAL_TITLE_STR}.")

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
            f"Setting conventional title check status to in-progress",
        )
        await self.check_run_handler.set_conventional_title_in_progress()
        # Strip whitespace from each allowed name to tolerate config whitespace
        # Filter out empty strings to prevent regex matching any title
        allowed_names = [name.strip() for name in self.github_webhook.conventional_title.split(",") if name.strip()]
        # Strip leading/trailing whitespace from title to be more forgiving
        title = pull_request.title.strip()

        self.logger.debug(f"{self.log_prefix} Conventional title check for title: {title}, allowed: {allowed_names}")
        # Match conventional commit format: type(optional-scope): description
        # Examples: "feat: title", "feat(scope): title", "fix!: breaking change"
        if any([re.search(rf"^{re.escape(_name)}(\([^)]*\))?!?:", title) for _name in allowed_names]):
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                f"Conventional title check completed successfully",
            )
            await self.check_run_handler.set_conventional_title_success(output=output)
        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Conventional title check failed",
            )
            output["summary"] = "Failed"
            output["text"] = f"Pull request title must start with allowed title: {', '.join(allowed_names)}"
            await self.check_run_handler.set_conventional_title_failure(output=output)

    async def is_branch_exists(self, branch: str) -> bool:
        owner, repo_name = self.github_webhook.owner_and_repo
        return await self.github_webhook.unified_api.get_branch(owner, repo_name, branch)

    async def cherry_pick(self, pull_request: PullRequestWrapper, target_branch: str, reviewed_user: str = "") -> None:
        requested_by = reviewed_user or "by target-branch label"
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting cherry-pick process to {target_branch}",
        )
        self.logger.info(f"{self.log_prefix} Cherry-pick requested by user: {requested_by}")

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not await self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exist"
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Cherry-pick failed: target branch does not exist",
            )
            self.logger.error(err_msg)
            await self.github_webhook.unified_api.add_pr_comment(pull_request=pull_request, body=err_msg)

        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Setting cherry-pick check status to in-progress",
            )
            await self.check_run_handler.set_cherry_pick_in_progress()
            commit_hash = pull_request.merge_commit_sha
            # Validate that PR has been merged before attempting cherry-pick
            if not commit_hash:
                # Fallback: Try to get last commit OID via GraphQL (or REST)
                owner, repo = self.github_webhook.owner_and_repo
                try:
                    # Use get_pull_request_data to get raw dict data with commits
                    pr_data = await self.github_webhook.unified_api.get_pull_request_data(
                        owner, repo, pull_request.number, include_commits=True
                    )
                    # Extract last commit OID from GraphQL response
                    commits_nodes = pr_data.get("commits", {}).get("nodes", [])
                    if commits_nodes:
                        commit_hash = commits_nodes[-1].get("commit", {}).get("oid")
                        self.logger.info(
                            f"{self.log_prefix} merge_commit_sha was None, using last commit OID: {commit_hash}"
                        )
                except (GraphQLError, GithubException, KeyError, IndexError) as fallback_ex:
                    self.logger.warning(
                        f"{self.log_prefix} Failed to get last commit OID via GraphQL/REST: {fallback_ex}"
                    )

                if not commit_hash:
                    err_msg = "cherry-pick failed: pull request has not been merged yet (merge_commit_sha is None)"
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                        f"Cherry-pick failed: PR not merged",
                    )
                    self.logger.error(f"{self.log_prefix} {err_msg}")
                    await self.github_webhook.unified_api.add_pr_comment(pull_request=pull_request, body=err_msg)
                    return
            # Note: shlex.quote() is used inline in hub command for safe shell escaping
            pull_request_url = pull_request.html_url
            clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
            # Quote paths to handle spaces
            git_cmd = f'git --work-tree="{clone_repo_dir}" --git-dir="{clone_repo_dir}/.git"'
            hub_cmd = f'hub --work-tree="{clone_repo_dir}" --git-dir="{clone_repo_dir}/.git"'
            # Build environment dict for passing token securely via subprocess env parameter
            hub_env = os.environ.copy()
            hub_env["GITHUB_TOKEN"] = self.github_webhook.token

            commands: list[tuple[str, dict[str, str] | None]] = [
                (f"{git_cmd} checkout {target_branch}", None),
                (f"{git_cmd} pull origin {target_branch}", None),
                (f"{git_cmd} checkout -b {new_branch_name} origin/{target_branch}", None),
                (f"{git_cmd} cherry-pick {commit_hash}", None),
                (f"{git_cmd} push origin {new_branch_name}", None),
                # Hub command with explicit env binding (env passed via env parameter)
                # Note: shlex.quote() already adds quotes, so we don't wrap in additional quotes
                (
                    f"{hub_cmd} pull-request -b {target_branch} -h {new_branch_name} "
                    f"-l {CHERRY_PICKED_LABEL_PREFIX} "
                    f"-m {shlex.quote(f'{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] {pull_request.title}')} "
                    f"-m {shlex.quote(f'cherry-pick {pull_request_url} into {target_branch}')} "
                    f"-m {shlex.quote(f'requested-by {requested_by}')}",
                    hub_env,
                ),
            ]

            rc, out, err = None, "", ""
            async with self._prepare_cloned_repo_dir(pull_request=pull_request, clone_repo_dir=clone_repo_dir) as _res:
                output = {
                    "title": "Cherry-pick details",
                    "summary": "",
                    "text": None,
                }
                if not _res[0]:
                    output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                    await self.check_run_handler.set_cherry_pick_failure(output=output)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                    f"Executing cherry-pick commands",
                )
                for cmd, env in commands:
                    # Explicit env binding via tuple - no heuristic needed
                    rc, out, err = await run_command(
                        command=cmd,
                        log_prefix=self.log_prefix,
                        redact_secrets=[self.github_webhook.token],
                        env=env,
                    )
                    if not rc:
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                            f"Cherry-pick command failed",
                        )
                        output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                        await self.check_run_handler.set_cherry_pick_failure(output=output)
                        self.logger.error(f"{self.log_prefix} Cherry pick failed: {out} --- {err}")
                        local_branch_name = f"{pull_request.head.ref}-{target_branch}"
                        await self.github_webhook.unified_api.add_pr_comment(
                            pull_request=pull_request,
                            body=f"**Manual cherry-pick is needed**\nCherry pick failed for "
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

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                f"Cherry-pick completed successfully",
            )
            await self.check_run_handler.set_cherry_pick_success(output=output)
            await self.github_webhook.unified_api.add_pr_comment(
                pull_request=pull_request, body=f"Cherry-picked PR {pull_request.title} into {target_branch}"
            )
