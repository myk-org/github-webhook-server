import asyncio
import contextlib
import re
import shutil
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import shortuuid
from github.Branch import Branch
from github.PullRequest import PullRequest
from github.Repository import Repository

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
from webhook_server.utils.helpers import _redact_secrets, format_task_fields, run_command
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

    @property
    def mask_sensitive(self) -> bool:
        """Get mask_sensitive configuration value."""
        return self.github_webhook.config.get_value("mask-sensitive-data", return_on_none=True)

    @contextlib.asynccontextmanager
    async def _prepare_cloned_repo_dir(
        self,
        clone_repo_dir: str,
        pull_request: PullRequest | None = None,
        is_merged: bool = False,
        checkout: str = "",
        tag_name: str = "",
    ) -> AsyncGenerator[tuple[bool, Any, Any], None]:
        git_cmd = f"git --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
        self.logger.debug(f"{self.log_prefix} Preparing cloned repo dir {clone_repo_dir} with git cmd: {git_cmd}")
        result: tuple[bool, str, str] = (True, "", "")
        success = True

        try:
            # Clone the repository
            github_token = self.github_webhook.token
            clone_url_with_token = self.repository.clone_url.replace("https://", f"https://{github_token}@")
            rc, out, err = await run_command(
                command=(f"git clone {clone_url_with_token} {clone_repo_dir}"),
                log_prefix=self.log_prefix,
                redact_secrets=[github_token],
                mask_sensitive=self.mask_sensitive,
            )
            if not rc:
                result = (rc, out, err)
                success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} config user.name '{self.repository.owner.login}'",
                    log_prefix=self.log_prefix,
                    mask_sensitive=self.mask_sensitive,
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} config user.email '{self.repository.owner.email}'",
                    log_prefix=self.log_prefix,
                    mask_sensitive=self.mask_sensitive,
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
                    mask_sensitive=self.mask_sensitive,
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} remote update",
                    log_prefix=self.log_prefix,
                    mask_sensitive=self.mask_sensitive,
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            # Checkout to requested branch/tag
            if checkout and success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} checkout {checkout}",
                    log_prefix=self.log_prefix,
                    mask_sensitive=self.mask_sensitive,
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

                if success and pull_request:
                    rc, out, err = await run_command(
                        command=f"{git_cmd} merge origin/{pull_request.base.ref} -m 'Merge {pull_request.base.ref}'",
                        log_prefix=self.log_prefix,
                        mask_sensitive=self.mask_sensitive,
                    )
                    if not rc:
                        result = (rc, out, err)
                        success = False

            # Checkout the branch if pull request is merged or for release
            else:
                if success:
                    if is_merged and pull_request:
                        rc, out, err = await run_command(
                            command=f"{git_cmd} checkout {pull_request.base.ref}",
                            log_prefix=self.log_prefix,
                            mask_sensitive=self.mask_sensitive,
                        )
                        if not rc:
                            result = (rc, out, err)
                            success = False

                    elif tag_name:
                        rc, out, err = await run_command(
                            command=f"{git_cmd} checkout {tag_name}",
                            log_prefix=self.log_prefix,
                            mask_sensitive=self.mask_sensitive,
                        )
                        if not rc:
                            result = (rc, out, err)
                            success = False

                    # Checkout the pull request
                    else:
                        if _pull_request := await self.github_webhook.get_pull_request():
                            rc, out, err = await run_command(
                                command=f"{git_cmd} checkout origin/pr/{_pull_request.number}",
                                log_prefix=self.log_prefix,
                                mask_sensitive=self.mask_sensitive,
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
                                    mask_sensitive=self.mask_sensitive,
                                )
                                if not rc:
                                    result = (rc, out, err)

        finally:
            yield result
            self.logger.debug(f"{self.log_prefix} Deleting {clone_repo_dir}")
            shutil.rmtree(clone_repo_dir, ignore_errors=True)

    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        shutil.rmtree("/tmp/storage-run-1000/containers", ignore_errors=True)
        shutil.rmtree("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    async def run_podman_command(
        self, command: str, redact_secrets: list[str] | None = None, mask_sensitive: bool = True
    ) -> tuple[bool, str, str]:
        rc, out, err = await run_command(
            command=command, log_prefix=self.log_prefix, redact_secrets=redact_secrets, mask_sensitive=mask_sensitive
        )

        if rc:
            return rc, out, err

        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return await run_command(
                command=command,
                log_prefix=self.log_prefix,
                redact_secrets=redact_secrets,
                mask_sensitive=mask_sensitive,
            )

        return rc, out, err

    async def run_tox(self, pull_request: PullRequest) -> None:
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
        cmd = f"uvx {python_ver} {TOX_STR} --workdir {clone_repo_dir} --root {clone_repo_dir} -c {clone_repo_dir}"
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
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} "
                    f"Repository preparation failed for tox",
                )
                self.logger.error(f"{self.log_prefix} Repository preparation failed for tox")
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await self.check_run_handler.set_run_tox_check_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} Executing tox command"
            )
            rc, out, err = await run_command(
                command=cmd, log_prefix=self.log_prefix, mask_sensitive=self.mask_sensitive
            )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Tox tests completed successfully",
                )
                return await self.check_run_handler.set_run_tox_check_success(output=output)
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} Tox tests failed"
                )
                return await self.check_run_handler.set_run_tox_check_failure(output=output)

    async def run_pre_commit(self, pull_request: PullRequest) -> None:
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
        cmd = f" uvx --directory {clone_repo_dir} {PREK_STR} run --all-files"

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
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} "
                    f"Repository preparation failed for pre-commit",
                )
                self.logger.error(f"{self.log_prefix} Repository preparation failed for pre-commit")
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await self.check_run_handler.set_run_pre_commit_check_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Executing pre-commit command",
            )
            rc, out, err = await run_command(
                command=cmd, log_prefix=self.log_prefix, mask_sensitive=self.mask_sensitive
            )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Pre-commit checks completed successfully",
                )
                return await self.check_run_handler.set_run_pre_commit_check_success(output=output)
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} Pre-commit checks failed"
                )
                return await self.check_run_handler.set_run_pre_commit_check_failure(output=output)

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
            pull_request=pull_request, is_merged=is_merged, tag=tag
        )
        no_cache: str = " --no-cache" if is_merged else ""
        build_cmd: str = (
            f"--network=host {no_cache} -f "
            f"{clone_repo_dir}/{self.github_webhook.dockerfile} "
            f"{clone_repo_dir} -t {_container_repository_and_tag}"
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
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} "
                    f"Repository preparation failed for container build",
                )
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Executing container build command",
            )
            build_rc, build_out, build_err = await self.run_podman_command(
                command=podman_build_cmd, mask_sensitive=self.mask_sensitive
            )
            output["text"] = self.check_run_handler.get_check_run_text(err=build_err, out=build_out)

            if build_rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Container build completed successfully",
                )
                self.logger.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_success(output=output)
            else:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} Container build failed"
                )
                self.logger.error(f"{self.log_prefix} Failed to build {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_failure(output=output)

            if push and build_rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} "
                    f"Starting container push to registry",
                )
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
                    mask_sensitive=self.mask_sensitive,
                )
                if push_rc:
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                        f"Container push completed successfully",
                    )
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
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} Container push failed",
                    )
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

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} "
            f"{format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting Python module installation"
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
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} "
                    f"Repository preparation failed for Python module installation",
                )
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await self.check_run_handler.set_python_module_install_failure(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'processing')} "
                f"Executing Python module installation command",
            )
            rc, out, err = await run_command(
                command=f"uvx pip wheel --no-cache-dir -w {clone_repo_dir}/dist {clone_repo_dir}",
                log_prefix=self.log_prefix,
                mask_sensitive=self.mask_sensitive,
            )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                    f"Python module installation completed successfully",
                )
                return await self.check_run_handler.set_python_module_install_success(output=output)

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} "
                f"{format_task_fields('runner', 'ci_check', 'failed')} "
                f"Python module installation failed"
            )
            return await self.check_run_handler.set_python_module_install_failure(output=output)

    async def run_conventional_title_check(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.conventional_title:
            return

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'started')} Starting conventional title check"
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
        allowed_names = self.github_webhook.conventional_title.split(",")
        title = pull_request.title

        self.logger.debug(f"{self.log_prefix} Conventional title check for title: {title}, allowed: {allowed_names}")
        if any([re.search(rf"{_name}(.*):", title) for _name in allowed_names]):
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                f"Conventional title check completed successfully",
            )
            await self.check_run_handler.set_conventional_title_success(output=output)
        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} "
                f"{format_task_fields('runner', 'ci_check', 'failed')} "
                f"Conventional title check failed"
            )
            output["summary"] = "Failed"
            output["text"] = f"Pull request title must starts with allowed title: {': ,'.join(allowed_names)}"
            await self.check_run_handler.set_conventional_title_failure(output=output)

    async def is_branch_exists(self, branch: str) -> Branch:
        return await asyncio.to_thread(self.repository.get_branch, branch)

    async def cherry_pick(self, pull_request: PullRequest, target_branch: str, reviewed_user: str = "") -> None:
        requested_by = reviewed_user or "by target-branch label"
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} "
            f"{format_task_fields('runner', 'ci_check', 'started')} "
            f"Starting cherry-pick process to {target_branch}"
        )
        self.logger.info(f"{self.log_prefix} Cherry-pick requested by user: {requested_by}")

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not await self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} "
                f"{format_task_fields('runner', 'ci_check', 'failed')} "
                f"Cherry-pick failed: target branch does not exist"
            )
            self.logger.error(err_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, err_msg)

        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} "
                f"{format_task_fields('runner', 'ci_check', 'processing')} "
                f"Setting cherry-pick check status to in-progress"
            )
            await self.check_run_handler.set_cherry_pick_in_progress()
            commit_hash = pull_request.merge_commit_sha
            commit_msg_striped = pull_request.title.replace("'", "")
            pull_request_url = pull_request.html_url
            clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
            git_cmd = f"git --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
            github_token = self.github_webhook.token
            hub_cmd = f"GITHUB_TOKEN={github_token} hub --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
            commands: list[str] = [
                f"{git_cmd} checkout {target_branch}",
                f"{git_cmd} pull origin {target_branch}",
                f"{git_cmd} checkout -b {new_branch_name} origin/{target_branch}",
                f"{git_cmd} cherry-pick {commit_hash}",
                f"{git_cmd} push origin {new_branch_name}",
                f'bash -c "{hub_cmd} pull-request -b {target_branch} '
                f"-h {new_branch_name} -l {CHERRY_PICKED_LABEL_PREFIX} "
                f"-m '{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] "
                f"{commit_msg_striped}' -m 'cherry-pick {pull_request_url} "
                f"into {target_branch}' -m 'requested-by {requested_by}'\"",
            ]

            rc, out, err = None, "", ""
            async with self._prepare_cloned_repo_dir(pull_request=pull_request, clone_repo_dir=clone_repo_dir) as _res:
                output = {
                    "title": "Cherry-pick details",
                    "summary": "",
                    "text": None,
                }
                if not _res[0]:
                    self.logger.step(  # type: ignore[attr-defined]
                        f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'failed')} "
                        f"Repository preparation failed for cherry-pick",
                    )
                    output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                    await self.check_run_handler.set_cherry_pick_failure(output=output)

                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} "
                    f"{format_task_fields('runner', 'ci_check', 'processing')} "
                    f"Executing cherry-pick commands"
                )
                for cmd in commands:
                    rc, out, err = await run_command(
                        command=cmd,
                        log_prefix=self.log_prefix,
                        redact_secrets=[github_token],
                        mask_sensitive=self.mask_sensitive,
                    )
                    if not rc:
                        self.logger.step(  # type: ignore[attr-defined]
                            f"{self.log_prefix} "
                            f"{format_task_fields('runner', 'ci_check', 'failed')} "
                            f"Cherry-pick command failed"
                        )
                        output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                        await self.check_run_handler.set_cherry_pick_failure(output=output)
                        redacted_out = _redact_secrets(out, [github_token], mask_sensitive=self.mask_sensitive)
                        redacted_err = _redact_secrets(err, [github_token], mask_sensitive=self.mask_sensitive)
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

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('runner', 'ci_check', 'completed')} "
                f"Cherry-pick completed successfully",
            )
            await self.check_run_handler.set_cherry_pick_success(output=output)
            await asyncio.to_thread(
                pull_request.create_issue_comment, f"Cherry-picked PR {pull_request.title} into {target_branch}"
            )
