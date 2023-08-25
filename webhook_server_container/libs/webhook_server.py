from webhook_server_container.libs.repositories import Repositories


class WebhookServer(Repositories):
    def __init__(self, hook_data, repositories_app_api, missing_app_repositories):
        super().__init__(
            hook_data=hook_data,
            repositories_app_api=repositories_app_api,
            missing_app_repositories=missing_app_repositories,
        )

    def process_hook(self, data, event_log):
        self.logger.info(f"{self.log_prefix} {event_log}")
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
            self.process_unknown_webhook_data()
