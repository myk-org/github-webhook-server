from webhook_server_container.libs.github_api import GitHubApi
from webhook_server_container.utils.constants import CAN_BE_MERGED_STR


class WebhookServer(GitHubApi):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

    def process_hook(self, data, event_log):
        self.app.logger.info(f"{self.log_prefix} {event_log}")
        ignore_data = ["status", "branch_protection_rule"]
        if data == "issue_comment":
            self.process_comment_webhook_data()

        elif data == "pull_request":
            self.process_pull_request_webhook_data()

        elif data == "push":
            self.process_push_webhook_data()

        elif data == "pull_request_review":
            self.process_pull_request_review_webhook_data()

        elif data not in ignore_data:
            self.process_unknown_webhook_data(data=data)

    def process_unknown_webhook_data(self, data):
        if data == "check_run":
            _check_run = self.hook_data["check_run"]
            if _check_run["name"] == CAN_BE_MERGED_STR:
                return

            if self.hook_data["action"] == "completed":
                self.process_check_run_complete(check_run=_check_run)

        self.pull_request = self.pull_request or self._get_pull_request()
        if self.pull_request:
            self.last_commit = self._get_last_commit()
            self.check_if_can_be_merged()

    def process_check_run_complete(self, check_run):
        self.app.logger.info(
            f"{self.log_prefix} Got event check_run completed, getting pull request"
        )
        for _pull_request in self.repository.get_pulls(state="open"):
            _last_commit = list(_pull_request.get_commits())[-1]
            for _commit_check_run in _last_commit.get_check_runs():
                if _commit_check_run.id == int(check_run["id"]):
                    self.pull_request = _pull_request
                    break
