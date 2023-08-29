from webhook_server_container.libs.logs import Logs
from webhook_server_container.libs.repositories import Repositories
from webhook_server_container.utils.helpers import check_rate_limit


class WebhookServer(Repositories):
    def __init__(
        self, hook_data, github_event, repositories_app_api, missing_app_repositories
    ):
        super().__init__(
            hook_data=hook_data,
            github_event=github_event,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )

        log = Logs(
            repository_name=self.repository_name,
            pull_request=self.pull_request,
            token=self.token,
        )
        self.logger = log.logger
        self.log_prefix = log.log_prefix
        self.logger.info(f"{self.log_prefix} Check rate limit")
        check_rate_limit()

    def process_hook(self, event_log):
        self.logger.info(f"{self.log_prefix} {event_log}")
        ignore_data = ["status", "branch_protection_rule"]
        if self.github_event == "issue_comment":
            self.process_comment_webhook_data()

        elif self.github_event == "pull_request":
            self.process_pull_request_webhook_data()

        elif self.github_event == "push":
            self.process_push_webhook_data()

        elif self.github_event == "pull_request_review":
            self.process_pull_request_review_webhook_data()

        elif self.github_event not in ignore_data:
            self.process_unknown_webhook_data()
