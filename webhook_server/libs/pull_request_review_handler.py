from typing import TYPE_CHECKING

from github.PullRequest import PullRequest

from webhook_server.libs.labels_handler import LabelsHandler
from webhook_server.libs.owners_files_handler import OwnersFileHandler
from webhook_server.utils.constants import ADD_STR, APPROVE_STR

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook


class PullRequestReviewHandler:
    def __init__(self, github_webhook: "GithubWebhook", owners_file_handler: OwnersFileHandler):
        self.github_webhook = github_webhook
        self.owners_file_handler = owners_file_handler

        self.hook_data = self.github_webhook.hook_data
        self.labels_handler = LabelsHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.github_webhook.logger.debug(f"{self.github_webhook.log_prefix} Initialized PullRequestReviewHandler")

    async def process_pull_request_review_webhook_data(self, pull_request: PullRequest) -> None:
        if self.hook_data["action"] == "submitted":
            """
            Available actions:
                commented
                approved
                changes_requested
            """
            reviewed_user = self.hook_data["review"]["user"]["login"]
            review_state = self.hook_data["review"]["state"]
            self.github_webhook.logger.debug(
                f"{self.github_webhook.log_prefix} "
                f"Processing pull request review for user {reviewed_user} with state {review_state}"
            )

            await self.labels_handler.manage_reviewed_by_label(
                pull_request=pull_request,
                review_state=review_state,
                action=ADD_STR,
                reviewed_user=reviewed_user,
            )

            if body := self.hook_data["review"]["body"]:
                self.github_webhook.logger.debug(f"{self.github_webhook.log_prefix} Found review body: {body}")
                if f"/{APPROVE_STR}" in body:
                    await self.labels_handler.label_by_user_comment(
                        pull_request=pull_request,
                        user_requested_label=APPROVE_STR,
                        remove=False,
                        reviewed_user=reviewed_user,
                    )
