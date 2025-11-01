"""Notification utilities."""

from __future__ import annotations

import json
from logging import Logger

import requests


def send_slack_message(message: str, webhook_url: str, logger: Logger, log_prefix: str = "") -> None:
    """
    Send message to Slack webhook.

    Args:
        message: Message text to send
        webhook_url: Slack webhook URL
        logger: Logger instance
        log_prefix: Prefix for log messages

    Raises:
        ValueError: If Slack webhook returns error status code
    """
    slack_data: dict[str, str] = {"text": message}
    logger.info(f"{log_prefix} Sending message to slack: {message}")
    response: requests.Response = requests.post(
        webhook_url,
        data=json.dumps(slack_data),
        headers={"Content-Type": "application/json"},
        timeout=10,
    )
    if response.status_code != 200:
        raise ValueError(
            f"Request to slack returned an error {response.status_code} with the following message: {response.text}"
        )
