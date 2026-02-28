from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from github.PullRequest import PullRequest

from webhook_server.libs.handlers.labels_handler import LabelsHandler
from webhook_server.libs.handlers.owners_files_handler import OwnersFileHandler
from webhook_server.libs.test_oracle import call_test_oracle
from webhook_server.utils.constants import ADD_STR, APPROVE_STR

if TYPE_CHECKING:
    from webhook_server.libs.github_api import GithubWebhook
    from webhook_server.utils.context import WebhookContext

_background_tasks: set[asyncio.Task[None]] = set()


class PullRequestReviewHandler:
    def __init__(self, github_webhook: GithubWebhook, owners_file_handler: OwnersFileHandler) -> None:
        self.github_webhook = github_webhook
        self.ctx: WebhookContext | None = github_webhook.ctx
        self.owners_file_handler = owners_file_handler

        self.hook_data = self.github_webhook.hook_data
        self.labels_handler = LabelsHandler(
            github_webhook=self.github_webhook, owners_file_handler=self.owners_file_handler
        )
        self.github_webhook.logger.debug(f"{self.github_webhook.log_prefix} Initialized PullRequestReviewHandler")

    async def process_pull_request_review_webhook_data(self, pull_request: PullRequest) -> None:
        if self.ctx:
            self.ctx.start_step("pr_review_handler")

        try:
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
                    # In this project, "approved" means a maintainer uses the /approve command
                    # (which adds an approved-<user> label), NOT GitHub's review approval state.
                    # The oracle trigger fires only when /approve is found in the review body.
                    if f"/{APPROVE_STR}" in body:
                        await self.labels_handler.label_by_user_comment(
                            pull_request=pull_request,
                            user_requested_label=APPROVE_STR,
                            remove=False,
                            reviewed_user=reviewed_user,
                        )
                        task = asyncio.create_task(
                            call_test_oracle(
                                github_webhook=self.github_webhook,
                                pull_request=pull_request,
                                trigger="approved",
                            )
                        )
                        _background_tasks.add(task)
                        task.add_done_callback(_background_tasks.discard)
        finally:
            if self.ctx:
                self.ctx.complete_step("pr_review_handler")
