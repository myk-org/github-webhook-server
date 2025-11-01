import asyncio
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

from webhook_server.libs.handlers.check_run_handler import CheckRunHandler
from webhook_server.libs.handlers.runner_handler import RunnerHandler
from webhook_server.utils.helpers import format_task_fields, run_command
from webhook_server.utils.notification_utils import send_slack_message

if TYPE_CHECKING:
    from github.Repository import Repository

    from webhook_server.libs.github_api import GithubWebhook


class PushHandler:
    def __init__(self, github_webhook: "GithubWebhook"):
        self.github_webhook = github_webhook

        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix: str = self.github_webhook.log_prefix
        self.repository: Repository = self.github_webhook.repository
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)
        self.runner_handler = RunnerHandler(github_webhook=self.github_webhook)

    async def process_push_webhook_data(self) -> None:
        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
            f"Starting push webhook processing",  # pragma: allowlist secret
        )
        tag = re.search(r"refs/tags/?(.+)", self.hook_data["ref"])
        if tag:
            tag_name = tag.group(1)
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'processing')} "
                f"Processing tag push: {tag_name}",
            )
            self.logger.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
            self.logger.debug(f"{self.log_prefix} Tag: {tag_name}")
            if self.github_webhook.pypi:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
                    f"Starting PyPI upload for tag: {tag_name}",
                )
                self.logger.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
                await self.upload_to_pypi(tag_name=tag_name)

            if self.github_webhook.build_and_push_container and self.github_webhook.container_release:
                self.logger.step(  # type: ignore[attr-defined]
                    f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
                    f"Starting container build and push for tag: {tag_name}",
                )
                self.logger.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
                await self.runner_handler.run_build_container(push=True, set_check=False, tag=tag_name)
        else:
            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'processing')} "
                f"Non-tag push detected, skipping processing",
            )

    async def upload_to_pypi(self, tag_name: str) -> None:
        async def _issue_on_error(*, _error: str) -> None:
            """Create an issue for PyPI upload errors using GraphQL API."""
            owner, repo_name = self.github_webhook.owner_and_repo
            await self.github_webhook.unified_api.create_issue_on_repository(
                owner=owner,
                name=repo_name,
                title=_error,
                body=f"""
Publish to PYPI failed: `{_error}`
""",
            )

        self.logger.step(  # type: ignore[attr-defined]
            f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'started')} "
            f"Starting PyPI upload process for tag: {tag_name}",
        )
        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        uv_cmd_dir = f"--directory {clone_repo_dir}"
        self.logger.info(f"{self.log_prefix} Start uploading to pypi")
        self.logger.debug(f"{self.log_prefix} Clone repo dir: {clone_repo_dir}")
        _dist_dir: str = f"{clone_repo_dir}/pypi-dist"

        async with self.runner_handler._prepare_cloned_repo_dir(
            checkout=tag_name, clone_repo_dir=clone_repo_dir
        ) as _res:
            if not _res[0]:
                _error = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return await _issue_on_error(_error=_error)

            rc, out, err = await run_command(
                command=f"uv {uv_cmd_dir} build --sdist --out-dir {_dist_dir}",
                log_prefix=self.log_prefix,
                redact_secrets=[],
            )
            if not rc:
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                return await _issue_on_error(_error=_error)

            # Get the sdist file (*.tar.gz) deterministically using Python (no shell pipes required)
            matches = sorted(Path(_dist_dir).glob("*.tar.gz"))
            if not matches:
                _error = f"No .tar.gz file found in {_dist_dir}"
                return await _issue_on_error(_error=_error)

            tar_gz_file = matches[0].name

            # Securely handle PyPI token - use pypirc file instead of CLI args
            token = (self.github_webhook.pypi or {}).get("token")
            if not token:
                return await _issue_on_error(_error="PyPI token is not configured")

            # Write temporary pypirc (removed when clone dir is cleaned up)
            # Create file atomically with secure permissions (0o600)
            pypirc_path = f"{clone_repo_dir}/.pypirc"
            pypirc_content = (
                "[distutils]\n"
                "index-servers = pypi\n\n"
                "[pypi]\n"
                "repository = https://upload.pypi.org/legacy/\n"
                "username = __token__\n"
                f"password = {token}\n"
            )
            # Atomically create with restrictive permissions and symlink protection
            try:
                # O_NOFOLLOW prevents symlink traversal attacks
                flags = os.O_CREAT | os.O_WRONLY | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                fd = os.open(pypirc_path, flags, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(pypirc_content)
            except FileExistsError:
                _error = f".pypirc file already exists at {pypirc_path}"
                self.logger.exception(f"{self.log_prefix} {_error}")
                return await _issue_on_error(_error=_error)
            except OSError as ex:
                _error = f"Failed to create .pypirc file: {ex}"
                self.logger.exception(f"{self.log_prefix} {_error}")
                return await _issue_on_error(_error=_error)

            # Ensure .pypirc is always removed, even on errors
            try:
                commands: list[str] = [
                    f"uv {uv_cmd_dir} run --with twine twine check --strict '{_dist_dir}/{tar_gz_file}'",
                    f"uv {uv_cmd_dir} run --with twine twine upload --non-interactive --config-file '{pypirc_path}' "
                    f"'{_dist_dir}/{tar_gz_file}' --skip-existing",
                ]
                # Avoid logging secrets; keep high-level trace only
                self.logger.debug("Prepared Twine commands (details redacted for security)")

                for cmd in commands:
                    rc, out, err = await run_command(command=cmd, log_prefix=self.log_prefix, redact_secrets=[token])
                    if not rc:
                        _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                        return await _issue_on_error(_error=_error)
            finally:
                # Clean up .pypirc to reduce credential exposure
                try:
                    os.remove(pypirc_path)
                    self.logger.debug(f"{self.log_prefix} Removed .pypirc after upload attempt")
                except OSError as ex:
                    self.logger.warning(f"{self.log_prefix} Failed to remove .pypirc: {ex}")

            self.logger.step(  # type: ignore[attr-defined]
                f"{self.log_prefix} {format_task_fields('push_processing', 'webhook_event', 'completed')} "
                f"PyPI upload completed successfully for tag: {tag_name}",
            )
            self.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.github_webhook.slack_webhook_url:
                message: str = f"""
```
{self.github_webhook.repository_name} Version {tag_name} published to PYPI.
```
"""
                await asyncio.to_thread(
                    send_slack_message,
                    message,
                    self.github_webhook.slack_webhook_url,
                    self.github_webhook.logger,
                )
