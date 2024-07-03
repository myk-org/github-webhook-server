import os
from typing import Any, Dict, List
from jira import Issue, JIRA
from pyhelper_utils.general import ignore_exceptions
from simple_logger.logger import get_logger


LOGGER = get_logger(name="JiraApi", filename=os.environ.get("WEBHOOK_SERVER_LOG_FILE"))


class JiraApi:
    def __init__(self, server: str, project: str, token: str):
        self.server = server
        self.project = project
        self.token = token

        self.conn: JIRA = JIRA(
            server=self.server,
            token_auth=self.token,
        )
        self.conn.my_permissions()
        self.fields: Dict[str, Any] = {"project": {"key": self.project}}

    @ignore_exceptions(logger=LOGGER)
    def create_story(self, title: str, body: str, epic_key: str, assignee: str) -> str:
        self.fields.update({
            "summary": title,
            "description": body,
            "issuetype": {"name": "Story"},
            "assignee": {"name": assignee},
        })
        if epic_key:
            if epic_custom_field := self.get_epic_custom_field():
                self.fields.update({epic_custom_field: epic_key})

        _issue: Issue = self.conn.create_issue(fields=self.fields)
        return _issue.key

    @ignore_exceptions(logger=LOGGER)
    def create_closed_subtask(self, title: str, body: str, parent_key: str, assignee: str) -> None:
        self.fields.update({
            "summary": title,
            "description": body,
            "parent": {"key": parent_key},
            "issuetype": {"name": "Sub-task"},
            "assignee": {"name": assignee},
        })
        _issue: Issue = self.conn.create_issue(fields=self.fields)
        self.close_issue(key=_issue.key)

    @ignore_exceptions(logger=LOGGER)
    def close_issue(self, key: str, comment: str = "") -> None:
        self.conn.transition_issue(
            issue=key,
            transition="closed",
            comment=comment,
        )

    def get_epic_custom_field(self) -> str:
        _epic_field_id: List[str] = [cf["id"] for cf in self.conn.fields() if "Epic Link" in cf["name"]]
        return _epic_field_id[0] if _epic_field_id else ""
