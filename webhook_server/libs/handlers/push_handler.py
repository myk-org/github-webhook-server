import asyncio
import re
from typing import TYPE_CHECKING

from github.Repository import Repository

from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.helpers import run_command
from webhook_server.utils.notification_utils import send_slack_message

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook
    from webhook_server.utils.context import WebhookContext


class PushHandler:
    def __init__(self, github_webhook: "GithubWebhook"):
        self.github_webhook = github_webhook

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.ctx: WebhookContext | None = github_webhook.ctx
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)
        self.runner_handler = RunnerHandler(github_webhook=self.github_webhook)

    async def process_push_webhook_data(self) -> None:
        if self.ctx:
            self.ctx.start_step("push_handler")

        tag = re.search(r"^refs/tags/(.+)$", self.hook_data["ref"])
        if tag:
            tag_name = tag.group(1)
            self.logger.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
            self.logger.debug(f"{self.log_prefix} Tag: {tag_name}")
            if self.github_webhook.pypi:
                self.logger.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
                try:
                    await self.upload_to_pypi(tag_name=tag_name)
                except Exception:
                    self.logger.exception(f"{self.log_prefix} PyPI upload failed")

            if self.github_webhook.build_and_push_container and self.github_webhook.container_release:
                self.logger.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
                try:
                    await self.runner_handler.run_build_container(push=True, set_check=False, tag=tag_name)
                    # Note: run_build_container logs completion/failure internally
                except Exception as ex:
                    self.logger.exception(f"{self.log_prefix} Container build and push failed: {ex}")

        if self.ctx:
            self.ctx.complete_step("push_handler")

    async def upload_to_pypi(self, tag_name: str) -> None:
        async def _issue_on_error(_error: str) -> None:
            # Sanitize title: replace newlines, remove backticks, strip whitespace, truncate
            sanitized_title = _error.replace("\n", " ").replace("`", "").replace("\r", "").strip()
            # Truncate to safe length (GitHub issue title limit is ~256 chars, use 250 for safety)
            if len(sanitized_title) > 250:
                sanitized_title = sanitized_title[:247] + "..."
            await asyncio.to_thread(
                self.repository.create_issue,
                title=sanitized_title,
                body=f"""
Publish to PYPI failed: `{_error}`
""",
            )

        self.logger.info(f"{self.log_prefix} Start uploading to pypi")

        async with self.runner_handler._checkout_worktree(checkout=tag_name) as (success, worktree_path, out, err):
            uv_cmd_dir = f"--directory {worktree_path}"
            _dist_dir: str = f"{worktree_path}/pypi-dist"
            self.logger.debug(f"{self.log_prefix} Worktree path: {worktree_path}")

            if not success:
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                await _issue_on_error(_error=_error)
                return

            rc, out, err = await run_command(
                command=f"uv {uv_cmd_dir} build --sdist --out-dir {_dist_dir}", log_prefix=self.log_prefix
            )
            if not rc:
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                await _issue_on_error(_error=_error)
                return

            rc, tar_gz_file, err = await run_command(command=f"ls {_dist_dir}", log_prefix=self.log_prefix)
            if not rc:
                _error = self.check_run_handler.get_check_run_text(out=tar_gz_file, err=err)
                await _issue_on_error(_error=_error)
                return

            tar_gz_file = tar_gz_file.strip()

            pypi_token = self.github_webhook.pypi["token"]
            commands: list[str] = [
                f"uvx {uv_cmd_dir} twine check {_dist_dir}/{tar_gz_file}",
                f"uvx {uv_cmd_dir} twine upload --username __token__ "
                f"--password {pypi_token} "
                f"{_dist_dir}/{tar_gz_file} --skip-existing",
            ]

            for cmd in commands:
                rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix, redact_secrets=[pypi_token])
                if not rc:
                    _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                    await _issue_on_error(_error=_error)
                    return

            self.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.github_webhook.slack_webhook_url:
                message: str = f"""
```
{self.github_webhook.repository_name} Version {tag_name} published to PYPI.
```
"""
                send_slack_message(
                    message=message,
                    webhook_url=self.github_webhook.slack_webhook_url,
                    logger=self.logger,
                    log_prefix=self.log_prefix,
                )
