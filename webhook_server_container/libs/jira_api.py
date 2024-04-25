from typing import Any, Dict
from jira import JIRA


class JiraApi:
    def __init__(
        self,
        jira_server: str,
        jira_project: str,
        jira_token: str,
        assignee: str,
    ):
        self.jira_server = jira_server
        self.jira_project = jira_project
        self.jira_token = jira_token

        self.conn = JIRA(
            server=self.jira_server,
            token_auth=self.jira_token,
        )
        self.conn.my_permissions()
        self.assignee = assignee
        self.fields: Dict[str, Any] = {"project": {"key": self.jira_project}, "assignee": {"name": self.assignee}}

    def create_story(self, title: str, body: str) -> str:
        self.fields.update({
            "summary": title,
            "description": body,
            "issuetype": {"name": "Story"},
        })
        _issue = self.conn.create_issue(fields=self.fields)
        return _issue.key

    def create_closed_subtask(self, title: str, body: str, parent_key: str) -> None:
        self.fields.update({
            "summary": title,
            "description": body,
            "parent": {"key": parent_key},
            "issuetype": {"name": "Sub-task"},
        })
        _issue = self.conn.create_issue(fields=self.fields)
        self.close_issue(key=_issue.key)

    def close_issue(self, key: str, comment: str = "") -> None:
        self.conn.transition_issue(
            issue=key,
            transition="closed",
            comment=comment,
        )
