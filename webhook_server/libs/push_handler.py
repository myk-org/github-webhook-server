import re
from typing import Any
from uuid import uuid4

from webhook_server.libs.check_run_handler import CheckRunHandler
from webhook_server.libs.runner_handler import RunnerHandler
from webhook_server.utils.helpers import run_command


class PushHandler:
    def __init__(self, github_webhook: Any):
        self.github_webhook = github_webhook
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.check_run_handler = CheckRunHandler(github_webhook=self.github_webhook)
        self.runner_handler = RunnerHandler(github_webhook=self.github_webhook)

    def process_push_webhook_data(self) -> None:
        tag = re.search(r"refs/tags/?(.*)", self.hook_data["ref"])
        if tag:
            tag_name = tag.group(1)
            self.logger.info(f"{self.log_prefix} Processing push for tag: {tag.group(1)}")
            if self.github_webhook.pypi:
                self.logger.info(f"{self.log_prefix} Processing upload to pypi for tag: {tag_name}")
                self.upload_to_pypi(tag_name=tag_name)

            if self.github_webhook.build_and_push_container and self.github_webhook.container_release:
                self.logger.info(f"{self.log_prefix} Processing build and push container for tag: {tag_name}")
                self.github_webhook._run_build_container(push=True, set_check=False, tag=tag_name)

    def upload_to_pypi(self, tag_name: str) -> None:
        def _issue_on_error(_error: str) -> None:
            self.repository.create_issue(
                title=_error,
                assignee=self.github_webhook.root_approvers[0] if self.github_webhook.root_approvers else "",
                body=f"""
Publish to PYPI failed: `{_error}`
""",
            )

        clone_repo_dir = f"{self.github_webhook.clone_repo_dir}-{uuid4()}"
        uv_cmd_dir = f"--directory {clone_repo_dir}"
        self.logger.info(f"{self.log_prefix} Start uploading to pypi")
        _dist_dir: str = f"{clone_repo_dir}/pypi-dist"

        with self.runner_handler._prepare_cloned_repo_dir(checkout=tag_name, clone_repo_dir=clone_repo_dir) as _res:
            if not _res[0]:
                _error = self.check_run_handler.get_check_run_text(out=_res[1], err=_res[2])
                return _issue_on_error(_error=_error)

            rc, out, err = run_command(
                command=f"uv {uv_cmd_dir} build --sdist --out-dir {_dist_dir}", log_prefix=self.log_prefix
            )
            if not rc:
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                return _issue_on_error(_error=_error)

            rc, tar_gz_file, err = run_command(command=f"ls {_dist_dir}", log_prefix=self.log_prefix)
            if not rc:
                _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                return _issue_on_error(_error=_error)

            tar_gz_file = tar_gz_file.strip()

            commands: list[str] = [
                f"uvx {uv_cmd_dir} twine check {_dist_dir}/{tar_gz_file}",
                f"uvx {uv_cmd_dir} twine upload --username __token__ --password {self.github_webhook.pypi['token']} {_dist_dir}/{tar_gz_file} --skip-existing",
            ]
            for cmd in commands:
                rc, out, err = run_command(command=cmd, log_prefix=self.log_prefix)
                if not rc:
                    _error = self.check_run_handler.get_check_run_text(out=out, err=err)
                    return _issue_on_error(_error=_error)

            self.logger.info(f"{self.log_prefix} Publish to pypi finished")
            if self.github_webhook.slack_webhook_url:
                message: str = f"""
```
{self.github_webhook.repository_name} Version {tag_name} published to PYPI.
```
"""
                self.github_webhook.send_slack_message(
                    message=message, webhook_url=self.github_webhook.slack_webhook_url
                )
