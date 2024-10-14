from typing import Any, Dict, List
from jira import Issue, JIRA

from webhook_server_container.utils.helpers import get_logger_with_params


class JiraApi:
    def __init__(self, server: str, project: str, token: str):
        self.logger = get_logger_with_params(name="JiraApi")
        self.server = server
        self.project = project
        self.token = token

        self.conn: JIRA = JIRA(
            server=self.server,
            token_auth=self.token,
        )
        self.conn.my_permissions()
        self.fields: Dict[str, Any] = {"project": {"key": self.project}}

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

    def close_issue(self, key: str, comment: str = "") -> None:
        self.conn.transition_issue(
            issue=key,
            transition="closed",
            comment=comment,
        )

    def get_epic_custom_field(self) -> str:
        _epic_field_id: List[str] = [cf["id"] for cf in self.conn.fields() if "Epic Link" in cf["name"]]
        return _epic_field_id[0] if _epic_field_id else ""
