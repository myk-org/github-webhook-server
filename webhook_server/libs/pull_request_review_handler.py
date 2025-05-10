from typing import Any

from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.utils.constants import ADD_STR, APPROVE_STR


class PullRequestReviewHandler:
    def __init__(self, github_webhook: Any):
        self.github_webhook = github_webhook
        self.hook_data = self.github_webhook.hook_data
        self.logger = self.github_webhook.logger
        self.log_prefix = self.github_webhook.log_prefix
        self.repository = self.github_webhook.repository
        self.pull_request = self.github_webhook.pull_request
        self.labels_handler = LabelsHandler(github_webhook=self.github_webhook)

    def process_pull_request_review_webhook_data(self) -> None:
        if self.hook_data["action"] == "submitted":
            """
            Available actions:
                commented
                approved
                changes_requested
            """
            reviewed_user = self.hook_data["review"]["user"]["login"]

            review_state = self.hook_data["review"]["state"]
            self.labels_handler.manage_reviewed_by_label(
                review_state=review_state,
                action=ADD_STR,
                reviewed_user=reviewed_user,
            )

            if body := self.hook_data["review"]["body"]:
                if f"/{APPROVE_STR}" in body:
                    self.labels_handler.label_by_user_comment(
                        user_requested_label=APPROVE_STR,
                        remove=False,
                        reviewed_user=reviewed_user,
                    )
