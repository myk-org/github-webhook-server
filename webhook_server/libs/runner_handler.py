import contextlib
import functools
import shutil
from typing import Any, Generator
from uuid import uuid4

import shortuuid
from github.NamedUser import NamedUser
from github.PaginatedList import PaginatedList

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.exceptions import NoPullRequestError
from webhook_server.utils.constants import (
    BUILD_CONTAINER_STR,
    CHERRY_PICKED_LABEL_PREFIX,
    CONVENTIONAL_TITLE_STR,
    PRE_COMMIT_STR,
    PYTHON_MODULE_INSTALL_STR,
    TOX_STR,
)
from webhook_server.utils.helpers import run_command


class RunnerHandler:
    def __init__(self, github_webhook: Any):
        self.github_webhook = github_webhook
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.pull_request = self.github_webhook.pull_request
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)

    @contextlib.contextmanager
    def _prepare_cloned_repo_dir(
        self,
        clone_repo_dir: str,
        is_merged: bool = False,
        checkout: str = "",
        tag_name: str = "",
    ) -> Generator[tuple[bool, Any, Any], None, None]:
        git_cmd = f"git --work-tree={clone_repo_dir} --git-dir={clone_repo_dir}/.git"

        # Clone the repository
        rc, out, err = run_command(
            command=f"git clone {self.repository.clone_url.replace('https://', f'https://{self.github_webhook.token}@')} "
            f"{clone_repo_dir}",
            log_prefix=self.log_prefix,
        )
        try:
            rc, out, err = run_command(
                command=f"{git_cmd} config user.name '{self.repository.owner.login}'", log_prefix=self.log_prefix
            )
            if not rc:
                yield rc, out, err

            rc, out, err = run_command(
                f"{git_cmd} config user.email '{self.repository.owner.email}'", log_prefix=self.log_prefix
            )
            if not rc:
                yield rc, out, err

            rc, out, err = run_command(
                command=f"{git_cmd} config --local --add remote.origin.fetch +refs/pull/*/head:refs/remotes/origin/pr/*",
                log_prefix=self.log_prefix,
            )
            if not rc:
                yield rc, out, err

            rc, out, err = run_command(command=f"{git_cmd} remote update", log_prefix=self.log_prefix)
            if not rc:
                yield rc, out, err

            # Checkout to requested branch/tag
            if checkout:
                rc, out, err = run_command(f"{git_cmd} checkout {checkout}", log_prefix=self.log_prefix)
                if not rc:
                    yield rc, out, err

                if getattr(self, "pull_request", None):
                    rc, out, err = run_command(
                        f"{git_cmd} merge origin/{self.github_webhook.pull_request_branch} -m 'Merge {self.github_webhook.pull_request_branch}'",
                        log_prefix=self.log_prefix,
                    )
                    if not rc:
                        yield rc, out, err

            # Checkout the branch if pull request is merged or for release
            else:
                if is_merged:
                    rc, out, err = run_command(
                        command=f"{git_cmd} checkout {self.github_webhook.pull_request_branch}",
                        log_prefix=self.log_prefix,
                    )
                    if not rc:
                        yield rc, out, err

                elif tag_name:
                    rc, out, err = run_command(command=f"{git_cmd} checkout {tag_name}", log_prefix=self.log_prefix)
                    if not rc:
                        yield rc, out, err

                # Checkout the pull request
                else:
                    try:
                        pull_request = self.github_webhook._get_pull_request()
                        rc, out, err = run_command(
                            command=f"{git_cmd} checkout origin/pr/{pull_request.number}", log_prefix=self.log_prefix
                        )
                        if not rc:
                            yield rc, out, err

                        if getattr(self, "pull_request", None):
                            rc, out, err = run_command(
                                f"{git_cmd} merge origin/{self.github_webhook.pull_request_branch} -m 'Merge {self.github_webhook.pull_request_branch}'",
                                log_prefix=self.log_prefix,
                            )
                            if not rc:
                                yield rc, out, err
                    except NoPullRequestError:
                        self.logger.error(f"{self.log_prefix} [func:_run_in_container] No pull request found")
                        yield False, "", "[func:_run_in_container] No pull request found"

            yield rc, out, err

        finally:
            self.logger.debug(f"{self.log_prefix} Deleting {clone_repo_dir}")
            shutil.rmtree(clone_repo_dir)

    def is_podman_bug(self, err: str) -> bool:
        _err = "Error: current system boot ID differs from cached boot ID; an unhandled reboot has occurred"
        return _err in err.strip()

    def fix_podman_bug(self) -> None:
        self.logger.debug(f"{self.log_prefix} Fixing podman bug")
        shutil.rmtree("/tmp/storage-run-1000/containers", ignore_errors=True)
        shutil.rmtree("/tmp/storage-run-1000/libpod/tmp", ignore_errors=True)

    def run_podman_command(self, command: str, pipe: bool = False) -> tuple[bool, str, str]:
        rc, out, err = run_command(command=command, log_prefix=self.log_prefix, pipe=pipe)

        if rc:
            return rc, out, err

        if self.is_podman_bug(err=err):
            self.fix_podman_bug()
            return run_command(command=command, log_prefix=self.log_prefix, pipe=pipe)

        return rc, out, err

    def _run_tox(self) -> None:
        if not self.github_webhook.tox:
            return

        if self.check_run_handler.is_check_run_in_progress(check_run=TOX_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {TOX_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        python_ver = (
            f"--python={self.github_webhook.tox_python_version}" if self.github_webhook.tox_python_version else ""
        )
        cmd = f"uvx {python_ver} {TOX_STR} --workdir {clone_repo_dir} --root {clone_repo_dir} -c {clone_repo_dir}"
        _tox_tests = self.github_webhook.tox.get(self.github_webhook.pull_request_branch, "")
        if _tox_tests and _tox_tests != "all":
            tests = _tox_tests.replace(" ", "")
            cmd += f" -e {tests}"

        self.check_run_handler.set_run_tox_check_in_progress()
        with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir) as _res:
            output: dict[str, Any] = {
                "title": "Tox",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return self.check_run_handler.set_run_tox_check_failure(output=output)

            rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                return self.check_run_handler.set_run_tox_check_success(output=output)
            else:
                return self.check_run_handler.set_run_tox_check_failure(output=output)

    def _run_pre_commit(self) -> None:
        if not self.github_webhook.pre_commit:
            return

        if self.check_run_handler.is_check_run_in_progress(check_run=PRE_COMMIT_STR):
            self.logger.debug(f"{self.log_prefix} Check run is in progress, re-running {PRE_COMMIT_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        cmd = f" uvx --directory {clone_repo_dir} {PRE_COMMIT_STR} run --all-files"
        self.check_run_handler.set_run_pre_commit_check_in_progress()
        with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir) as _res:
            output: dict[str, Any] = {
                "title": "Pre-Commit",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return self.check_run_handler.set_run_pre_commit_check_failure(output=output)

            rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                return self.check_run_handler.set_run_pre_commit_check_success(output=output)
            else:
                return self.check_run_handler.set_run_pre_commit_check_failure(output=output)

    def _run_build_container(
        self,
        set_check: bool = True,
        push: bool = False,
        is_merged: bool = False,
        tag: str = "",
        command_args: str = "",
        reviewed_user: str | None = None,
    ) -> None:
        if not self.github_webhook.build_and_push_container:
            return

        if reviewed_user and not self._is_user_valid_to_run_commands(reviewed_user=reviewed_user):
            return

        if self.check_run_handler.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {BUILD_CONTAINER_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        pull_request = hasattr(self, "pull_request")

        if pull_request and set_check:
            if self.check_run_handler.is_check_run_in_progress(check_run=BUILD_CONTAINER_STR) and not is_merged:
                self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {BUILD_CONTAINER_STR}.")

            self.check_run_handler.set_container_build_in_progress()

        _container_repository_and_tag = self.github_webhook._container_repository_and_tag(is_merged=is_merged, tag=tag)
        no_cache: str = " --no-cache" if is_merged else ""
        build_cmd: str = f"--network=host {no_cache} -f {clone_repo_dir}/{self.github_webhook.dockerfile} {clone_repo_dir} -t {_container_repository_and_tag}"

        if self.github_webhook.container_build_args:
            build_args: str = [f"--build-arg {b_arg}" for b_arg in self.github_webhook.container_build_args][0]
            build_cmd = f"{build_args} {build_cmd}"

        if self.github_webhook.container_command_args:
            build_cmd = f"{' '.join(self.github_webhook.container_command_args)} {build_cmd}"

        if command_args:
            build_cmd = f"{command_args} {build_cmd}"

        podman_build_cmd: str = f"podman build {build_cmd}"
        with self._prepare_cloned_repo_dir(
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
                if self.pull_request and set_check:
                    return self.check_run_handler.set_container_build_failure(output=output)

            build_rc, build_out, build_err = self.run_podman_command(command=podman_build_cmd, pipe=True)
            output["text"] = self.check_run_handler.get_check_run_text(err=build_err, out=build_out)

            if build_rc:
                self.logger.info(f"{self.log_prefix} Done building {_container_repository_and_tag}")
                if pull_request and set_check:
                    return self.check_run_handler.set_container_build_success(output=output)
            else:
                self.logger.error(f"{self.log_prefix} Failed to build {_container_repository_and_tag}")
                if self.pull_request and set_check:
                    return self.check_run_handler.set_container_build_failure(output=output)

            if push and build_rc:
                cmd = f"podman push --creds {self.github_webhook.container_repository_username}:{self.github_webhook.container_repository_password} {_container_repository_and_tag}"
                push_rc, _, _ = self.run_podman_command(command=cmd)
                if push_rc:
                    push_msg: str = f"New container for {_container_repository_and_tag} published"
                    if pull_request:
                        self.pull_request.create_issue_comment(push_msg)

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
                    if self.pull_request:
                        self.pull_request.create_issue_comment(err_msg)

                    if self.github_webhook.slack_webhook_url:
                        message = f"""
```
{self.github_webhook.repository_full_name} {err_msg}.
```
                        """
                        self.github_webhook.send_slack_message(
                            message=message, webhook_url=self.github_webhook.slack_webhook_url
                        )

    def _run_install_python_module(self) -> None:
        if not self.github_webhook.pypi:
            return

        if self.check_run_handler.is_check_run_in_progress(check_run=PYTHON_MODULE_INSTALL_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {PYTHON_MODULE_INSTALL_STR}.")

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        self.logger.info(f"{self.log_prefix} Installing python module")
        self.check_run_handler.set_python_module_install_in_progress()
        with self._prepare_cloned_repo_dir(
            clone_repo_dir=clone_repo_dir,
        ) as _res:
            output: dict[str, Any] = {
                "title": "Python module installation",
                "summary": "",
                "text": None,
            }
            if not _res[0]:
                output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return self.check_run_handler.set_python_module_install_failure(output=output)

            rc, out, err = run_command(
                command=f"uvx pip wheel --no-cache-dir -w {clone_repo_dir}/dist {clone_repo_dir}",
                log_prefix=self.log_prefix,
            )

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            if rc:
                return self.check_run_handler.set_python_module_install_success(output=output)

            return self.check_run_handler.set_python_module_install_failure(output=output)

    def _run_conventional_title_check(self) -> None:
        output: dict[str, str] = {
            "title": "Conventional Title",
            "summary": "",
            "text": "",
        }

        if self.check_run_handler.is_check_run_in_progress(check_run=CONVENTIONAL_TITLE_STR):
            self.logger.info(f"{self.log_prefix} Check run is in progress, re-running {CONVENTIONAL_TITLE_STR}.")

        self.check_run_handler.set_conventional_title_in_progress()
        allowed_names = self.github_webhook.conventional_title.split(",")
        title = self.pull_request.title
        if any([title.startswith(f"{_name}:") for _name in allowed_names]):
            self.check_run_handler.set_conventional_title_success(output=output)
        else:
            output["summary"] = "Failed"
            output["text"] = f"Pull request title must starts with allowed title: {': ,'.join(allowed_names)}"

            self.check_run_handler.set_conventional_title_failure(output=output)

    def _is_user_valid_to_run_commands(self, reviewed_user: str) -> bool:
        allowed_user_to_approve = self.get_all_repository_maintainers() + self.github_webhook.all_repository_approvers
        allow_user_comment = f"/add-allowed-user @{reviewed_user}"

        comment_msg = f"""
{reviewed_user} is not allowed to run retest commands.
maintainers can allow it by comment `{allow_user_comment}`
Maintainers:
 - {"\n - @".join(allowed_user_to_approve)}
"""

        if reviewed_user not in self.valid_users_to_run_commands:
            comments_from_approvers = [
                comment.body
                for comment in self.pull_request.get_issue_comments()
                if comment.user.login in allowed_user_to_approve
            ]
            for comment in comments_from_approvers:
                if allow_user_comment in comment:
                    return True

            self.logger.debug(f"{self.log_prefix} {reviewed_user} is not in {self.valid_users_to_run_commands}")
            self.pull_request.create_issue_comment(comment_msg)
            return False

        return True

    @functools.cached_property
    def valid_users_to_run_commands(self) -> set[str]:
        return set((
            *self.get_all_repository_contributors(),
            *self.get_all_repository_collaborators(),
            *self.github_webhook.all_repository_approvers,
            *self.github_webhook.all_pull_request_reviewers,
        ))

    def get_all_repository_maintainers(self) -> list[str]:
        maintainers: list[str] = []

        for user in self.repository_collaborators:
            permmissions = user.permissions

            if permmissions.admin or permmissions.maintain:
                maintainers.append(user.login)

        return maintainers

    @functools.cached_property
    def repository_collaborators(self) -> PaginatedList[NamedUser]:
        return self.repository.get_collaborators()

    @functools.cached_property
    def repository_contributors(self) -> PaginatedList[NamedUser]:
        return self.repository.get_contributors()

    def get_all_repository_contributors(self) -> list[str]:
        return [val.login for val in self.repository_contributors]

    def get_all_repository_collaborators(self) -> list[str]:
        return [val.login for val in self.repository_collaborators]

    def cherry_pick(self, target_branch: str, reviewed_user: str = "") -> None:
        requested_by = reviewed_user or "by target-branch label"
        self.logger.info(f"{self.log_prefix} Cherry-pick requested by user: {requested_by}")

        new_branch_name = f"{CHERRY_PICKED_LABEL_PREFIX}-{self.pull_request.head.ref}-{shortuuid.uuid()[:5]}"
        if not self.github_webhook.is_branch_exists(branch=target_branch):
            err_msg = f"cherry-pick failed: {target_branch} does not exists"
            self.logger.error(err_msg)
            self.pull_request.create_issue_comment(err_msg)

        else:
            self.check_run_handler.set_cherry_pick_in_progress()
            commit_hash = self.pull_request.merge_commit_sha
            commit_msg_striped = self.pull_request.title.replace("'", "")
            pull_request_url = self.pull_request.html_url
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

            rc, out, err = None, "", ""
            with self._prepare_cloned_repo_dir(clone_repo_dir=clone_repo_dir) as _res:
                output = {
                    "title": "Cherry-pick details",
                    "summary": "",
                    "text": None,
                }
                if not _res[0]:
                    output["text"] = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                    self.check_run_handler.set_cherry_pick_failure(output=output)

                for cmd in commands:
                    rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)
                    if not rc:
                        output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)
                        self.check_run_handler.set_cherry_pick_failure(output=output)
                        self.logger.error(f"{self.log_prefix} Cherry pick failed: {out} --- {err}")
                        local_branch_name = f"{self.pull_request.head.ref}-{target_branch}"
                        self.pull_request.create_issue_comment(
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
                            "```"
                        )
                        return

            output["text"] = self.check_run_handler.get_check_run_text(err=err, out=out)

            self.check_run_handler.set_cherry_pick_success(output=output)
            self.pull_request.create_issue_comment(f"Cherry-picked PR {self.pull_request.title} into {target_branch}")
