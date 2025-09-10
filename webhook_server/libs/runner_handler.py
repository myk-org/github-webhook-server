import asyncio
import contextlib
import re
import shutil
from typing import TYPE_CHECKING, Any, AsyncGenerator
from uuid import uuid4

import shortuuid
from github.Branch import Branch
from github.PullRequest import PullRequest
from github.Repository import Repository

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CHERRY_PICKED_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    PRE_COMMIT_STR,
    PREK_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server.utils.helpers import run_command

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
            rc, out, err = await run_command(
                command=f"git clone {self.repository.clone_url.replace('https://', f'https://{self.github_webhook.token}@')} "
                f"{clone_repo_dir}",
                log_prefix=self.log_prefix,
            )
            if not rc:
                result = (rc, out, err)
                success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} config user.name '{self.repository.owner.login}'", log_prefix=self.log_prefix
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(
                    f"{git_cmd} config user.email '{self.repository.owner.email}'", log_prefix=self.log_prefix
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(
                    command=f"{git_cmd} config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*",
                    log_prefix=self.log_prefix,
                )
                if not rc:
                    result = (rc, out, err)
                    success = False

            if success:
                rc, out, err = await run_command(command=f"{git_cmd} remote update", log_prefix=self.log_prefix)
                if not rc:
                    result = (rc, out, err)
                    success = False

            # Checkout to requested branch/tag
            if checkout and success:
                rc, out, err = await run_command(f"{git_cmd} checkout {checkout}", log_prefix=self.log_prefix)
                if not rc:
                    result = (rc, out, err)
                    success = False

                if success and pull_request:
                    rc, out, err = await run_command(
                        f"{git_cmd} merge origin/{pull_request.base.ref} -m 'Merge {pull_request.base.ref}'",
                        log_prefix=self.log_prefix,
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
                        )
                        if not rc:
                            result = (rc, out, err)
                            success = False

                    elif tag_name:
                        rc, out, err = await run_command(
                            command=f"{git_cmd} checkout {tag_name}", log_prefix=self.log_prefix
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
                            )
                            if not rc:
                                result = (rc, out, err)
                                success = False

                            if pull_request and success:
                                rc, out, err = await run_command(
                                    f"{git_cmd} merge origin/{pull_request.base.ref} -m 'Merge {pull_request.base.ref}'",
                                    log_prefix=self.log_prefix,
                                )
                                if not rc:
                                    result = (rc, out, err)

        finally:
            yield result
            self.logger.debug(f"{self.log_prefix} Deleting {clone_repo_dir}")
            shutil.rmtree(clone_repo_dir)

    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        shutil.rmtree("/tmp/storage-run-1000/containers", ignore_errors=True)
        shutil.rmtree("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    async def run_podman_command(self, command: str) -> tuple[bool, str, str]:
        rc, out, err = await run_command(command=command, log_prefix=self.log_prefix)

        if rc:
            return rc, out, err

        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return await run_command(command=command, log_prefix=self.log_prefix)

        return rc, out, err

    async def run_tox(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.tox:
            self.logger.debug(f"{self.log_prefix} Tox not configured for this repository")
            return

        self.logger.step(f"{self.log_prefix} Starting tox tests execution")  # type: ignore

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

        self.logger.step(f"{self.log_prefix} Setting tox check status to in-progress")  # type: ignore
        await self.check_run_handler.set_run_tox_check_in_progress()
        self.logger.debug(f"{self.log_prefix} Tox command to run: {cmd}")

        self.logger.step(f"{self.log_prefix} Preparing repository clone for tox execution")  # type: ignore
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

            self.logger.step(f"{self.log_prefix} Executing tox command")  # type: ignore
            rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix)

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(f"{self.log_prefix} Tox tests completed successfully")  # type: ignore
                return await self.check_run_handler.set_run_tox_check_success(output=output)
            else:
                self.logger.step(f"{self.log_prefix} Tox tests failed")  # type: ignore
                return await self.check_run_handler.set_run_tox_check_failure(output=output)

    async def run_pre_commit(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.pre_commit:
            self.logger.debug(f"{self.log_prefix} Pre-commit not configured for this repository")
            return

        self.logger.step(f"{self.log_prefix} Starting pre-commit checks execution")  # type: ignore

        if await self.check_run_handler.is_check_run_in_progress(check_run=PRE_COMMIT_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {PRE_COMMIT_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        cmd = f" uvx --directory {clone_repo_dir} {PREK_STR} run --all-files"

        self.logger.step(f"{self.log_prefix} Setting pre-commit check status to in-progress")  # type: ignore
        await self.check_run_handler.set_run_pre_commit_check_in_progress()

        self.logger.step(f"{self.log_prefix} Preparing repository clone for pre-commit execution")  # type: ignore
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

            self.logger.step(f"{self.log_prefix} Executing pre-commit command")  # type: ignore
            rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix)

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(f"{self.log_prefix} Pre-commit checks completed successfully")  # type: ignore
                return await self.check_run_handler.set_run_pre_commit_check_success(output=output)
            else:
                self.logger.step(f"{self.log_prefix} Pre-commit checks failed")  # type: ignore
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

        self.logger.step(f"{self.log_prefix} Starting container build process")  # type: ignore

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

        self.logger.step(f"{self.log_prefix} Setting container build check status to in-progress")  # type: ignore
        await self.check_run_handler.set_container_build_in_progress()

        _container_repository_and_tag = self.github_webhook.container_repository_and_tag(
            pull_request=pull_request, is_merged=is_merged, tag=tag
        )
        no_cache: str = " --no-cache" if is_merged else ""
        build_cmd: str = f"--network=host {no_cache} -f {clone_repo_dir}/{self.github_webhook.dockerfile} {clone_repo_dir} -t {_container_repository_and_tag}"

        if self.github_webhook.container_build_args:
            build_args = " ".join(f"--build-arg {arg}" for arg in self.github_webhook.container_build_args)
            build_cmd = f"{build_args} {build_cmd}"

        if self.github_webhook.container_command_args:
            build_cmd = f"{' '.join(self.github_webhook.container_command_args)} {build_cmd}"

        if command_args:
            build_cmd = f"{command_args} {build_cmd}"

        podman_build_cmd: str = f"podman build {build_cmd}"
        self.logger.debug(f"{self.log_prefix} Podman build command to run: {podman_build_cmd}")
        self.logger.step(f"{self.log_prefix} Preparing repository clone for container build")  # type: ignore
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

            self.logger.step(f"{self.log_prefix} Executing container build command")  # type: ignore
            build_rc, build_out, build_err = await self.run_podman_command(command=podman_build_cmd)
            output["text"] = self.check_run_handler.get_check_run_text(err=build_err, out=build_out)

            if build_rc:
                self.logger.step(f"{self.log_prefix} Container build completed successfully")  # type: ignore
                self.logger.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_success(output=output)
            else:
                self.logger.step(f"{self.log_prefix} Container build failed")  # type: ignore
                self.logger.error(f"{self.log_prefix} Failed to build {_container_repository_and_tag}")
                if pull_request and set_check:
                    return await self.check_run_handler.set_container_build_failure(output=output)

            if push and build_rc:
                self.logger.step(f"{self.log_prefix} Starting container push to registry")  # type: ignore
                cmd = f"podman push --creds {self.github_webhook.container_repository_username}:{self.github_webhook.container_repository_password} {_container_repository_and_tag}"
                push_rc, _, _ = await self.run_podman_command(command=cmd)
                if push_rc:
                    self.logger.step(f"{self.log_prefix} Container push completed successfully")  # type: ignore
                    push_msg: str = f"New container for {_container_repository_and_tag} published"
                    if pull_request:
                        await asyncio.to_thread(pull_request.create_issue_comment, push_msg)

                    if self.github_webhook.slack_webhook_url:
                        message = f"""
```
{self.github_webhook.repository_full_name} {push_msg}.
```
"""
                        self.github_webhook.send_slack_message(
                            message=message, webhook_url=self.github_webhook.slack_webhook_url
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
                        self.github_webhook.send_slack_message(
                            message=message, webhook_url=self.github_webhook.slack_webhook_url
                        )

    async def run_install_python_module(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.pypi:
            return

        self.logger.step(f"{self.log_prefix} Starting Python module installation")  # type: ignore

        if await self.check_run_handler.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {PYTHON_MODULE_INSTALL_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        self.logger.info(f"{self.log_prefix} Installing python module")
        self.logger.step(f"{self.log_prefix} Setting Python module install check status to in-progress")  # type: ignore
        await self.check_run_handler.set_python_module_install_in_progress()
        self.logger.step(f"{self.log_prefix} Preparing repository clone for Python module installation")  # type: ignore
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

            self.logger.step(f"{self.log_prefix} Executing Python module installation command")  # type: ignore
            rc, out, err = await run_command(
                command=f"uvx pip wheel --no-cache-dir -w {clone_repo_dir}/dist {clone_repo_dir}",
                log_prefix=self.log_prefix,
            )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                self.logger.step(f"{self.log_prefix} Python module installation completed successfully")  # type: ignore
                return await self.check_run_handler.set_python_module_install_success(output=output)

            self.logger.step(f"{self.log_prefix} Python module installation failed")  # type: ignore
            return await self.check_run_handler.set_python_module_install_failure(output=output)

    async def run_conventional_title_check(self, pull_request: PullRequest) -> None:
        if not self.github_webhook.conventional_title:
            return

        self.logger.step(f"{self.log_prefix} Starting conventional title check")  # type: ignore

        output: dict[str, str] = {
            "title": "Conventional Title",
            "summary": "",
            "text": "",
        }

        if await self.check_run_handler.is_check_run_in_progress(check_run=CONVENTIONAL_TITLE_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {CONVENTIONAL_TITLE_STR}.")

        self.logger.step(f"{self.log_prefix} Setting conventional title check status to in-progress")  # type: ignore
        await self.check_run_handler.set_conventional_title_in_progress()
        allowed_names = self.github_webhook.conventional_title.split(",")
        title = pull_request.title

        self.logger.debug(f"{self.log_prefix} Conventional title check for title: {title}, allowed: {allowed_names}")
        if any([re.search(rf"{_name}(.*):", title) for _name in allowed_names]):
            self.logger.step(f"{self.log_prefix} Conventional title check completed successfully")  # type: ignore
            await self.check_run_handler.set_conventional_title_success(output=output)
        else:
            self.logger.step(f"{self.log_prefix} Conventional title check failed")  # type: ignore
            output["summary"] = "Failed"
            output["text"] = f"Pull request title must starts with allowed title: {': ,'.join(allowed_names)}"
            await self.check_run_handler.set_conventional_title_failure(output=output)

    async def is_branch_exists(self, branch: str) -> Branch:
        return await asyncio.to_thread(self.repository.get_branch, branch)

    async def cherry_pick(self, pull_request: PullRequest, target_branch: str, reviewed_user: str = "") -> None:
        requested_by = reviewed_user or "by target-branch label"
        self.logger.step(f"{self.log_prefix} Starting cherry-pick process to {target_branch}")  # type: ignore
        self.logger.info(f"{self.log_prefix} Cherry-pick requested by user: {requested_by}")

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not await self.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.step(f"{self.log_prefix} Cherry-pick failed: target branch does not exist")  # type: ignore
            self.logger.error(err_msg)
            await asyncio.to_thread(pull_request.create_issue_comment, err_msg)

        else:
            self.logger.step(f"{self.log_prefix} Setting cherry-pick check status to in-progress")  # type: ignore
            await self.check_run_handler.set_cherry_pick_in_progress()
            commit_hash = pull_request.merge_commit_sha
            commit_msg_striped = pull_request.title.replace("'", "")
            pull_request_url = pull_request.html_url
            clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
            git_cmd = f"git --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
            hub_cmd = f"GITHUB_TOKEN={self.github_webhook.token} hub --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"
            commands: list[str] = [
                f"{git_cmd} checkout {target_branch}",
                f"{git_cmd} pull origin {target_branch}",
                f"{git_cmd} checkout -b {new_branch_name} origin/{target_branch}",
                f"{git_cmd} cherry-pick {commit_hash}",
                f"{git_cmd} push origin {new_branch_name}",
                f"bash -c \"{hub_cmd} pull-request -b {target_branch} -h {new_branch_name} -l {CHERRY_PICKED_LABEL_PREFIX} -m '{CHERRY_PICKED_LABEL_PREFIX}: [{target_branch}] {commit_msg_striped}' -m 'cherry-pick {pull_request_url} into {target_branch}' -m 'requested-by {requested_by}'\"",
            ]
            self.logger.debug(f"{self.log_prefix} Cherry pick commands to run: {commands}")

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

                self.logger.step(f"{self.log_prefix} Executing cherry-pick commands")  # type: ignore
                for cmd in commands:
                    rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix)
                    if not rc:
                        self.logger.step(f"{self.log_prefix} Cherry-pick command failed")  # type: ignore
                        output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                        await self.check_run_handler.set_cherry_pick_failure(output=output)
                        self.logger.error(f"{self.log_prefix} Cherry pick failed: {out} --- {err}")
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

            self.logger.step(f"{self.log_prefix} Cherry-pick completed successfully")  # type: ignore
            await self.check_run_handler.set_cherry_pick_success(output=output)
            await asyncio.to_thread(
                pull_request.create_issue_comment, f"Cherry-picked PR {pull_request.title} into {target_branch}"
            )
