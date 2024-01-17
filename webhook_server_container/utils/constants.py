import logging
import os
from datetime import datetime
from logging.handlers import RotatingFileHandler

from colorlog import ColoredFormatter
from flask import Flask


class WrapperLogFormatter(ColoredFormatter):
    def formatTime(self, record, datefmt=None):  # noqa: N802
        return datetime.fromtimestamp(record.created).isoformat()


FLASK_APP = Flask("webhook-server")


def setup_logger():
    log_format = "%(asctime)s %(levelname)s \033[1;36m%(filename)s:%(lineno)d\033[1;0m %(name)s: %(message)s"
    logging.basicConfig(level=logging.INFO, format=log_format)

    # Add color to log level names
    logging.addLevelName(logging.DEBUG, "\033[1;34mDEBUG\033[1;0m")
    logging.addLevelName(logging.INFO, "\033[1;32mINFO\033[1;0m")
    logging.addLevelName(logging.WARNING, "\033[1;33mWARNING\033[1;0m")
    logging.addLevelName(logging.ERROR, "\033[1;31mERROR\033[1;0m")
    logging.addLevelName(logging.CRITICAL, "\033[1;41mCRITICAL\033[1;0m")

    log_file = os.environ.get("WEBHOOK_SERVER_LOG_FILE")
    if log_file:
        log_handler = RotatingFileHandler(filename=log_file, maxBytes=104857600, backupCount=20)
        file_log_formatter = WrapperLogFormatter(
            fmt="%(asctime)s %(levelname)s \033[1;36m%(filename)s:%(lineno)d\033[1;0m %(name)s: %(message)s",
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
            secondary_log_colors={},
        )
        log_handler.setFormatter(fmt=file_log_formatter)
        FLASK_APP.logger.addHandler(hdlr=log_handler)


setup_logger()


APP_ROOT_PATH = "/webhook_server"
TOX_STR = "tox"
BUILD_AND_PUSH_CONTAINER_STR = "build-and-push-container"
SUCCESS_STR = "success"
FAILURE_STR = "failure"
IN_PROGRESS_STR = "in_progress"
QUEUED_STR = "queued"
ADD_STR = "add"
DELETE_STR = "delete"
CAN_BE_MERGED_STR = "can-be-merged"
BUILD_CONTAINER_STR = "build-container"
PYTHON_MODULE_INSTALL_STR = "python-module-install"
WIP_STR = "wip"
CHERRY_PICK_LABEL_PREFIX = "cherry-pick-"
CHERRY_PICKED_LABEL_PREFIX = "CherryPicked"
APPROVED_BY_LABEL_PREFIX = "approved-"
CHANGED_REQUESTED_BY_LABEL_PREFIX = "changes-requested-"
COMMENTED_BY_LABEL_PREFIX = "commented-"
BRANCH_LABEL_PREFIX = "branch-"
VERIFIED_LABEL_STR = "verified"
LGTM_STR = "lgtm"
NEEDS_REBASE_LABEL_STR = "needs-rebase"
HAS_CONFLICTS_LABEL_STR = "has-conflicts"
HOLD_LABEL_STR = "hold"
SIZE_LABEL_PREFIX = "size/"

# Gitlab colors require a '#' prefix; e.g: #
USER_LABELS_DICT = {HOLD_LABEL_STR: "B60205", VERIFIED_LABEL_STR: "0E8A16", WIP_STR: "B60205", LGTM_STR: "0E8A16"}

STATIC_LABELS_DICT = {
    **USER_LABELS_DICT,
    CHERRY_PICKED_LABEL_PREFIX: "1D76DB",
    f"{SIZE_LABEL_PREFIX}L": "F5621C",
    f"{SIZE_LABEL_PREFIX}M": "F09C74",
    f"{SIZE_LABEL_PREFIX}S": "0E8A16",
    f"{SIZE_LABEL_PREFIX}XL": "D93F0B",
    f"{SIZE_LABEL_PREFIX}XS": "ededed",
    f"{SIZE_LABEL_PREFIX}XXL": "B60205",
    NEEDS_REBASE_LABEL_STR: "B60205",
    CAN_BE_MERGED_STR: "0E8A17",
    HAS_CONFLICTS_LABEL_STR: "B60205",
}

DYNAMIC_LABELS_DICT = {
    APPROVED_BY_LABEL_PREFIX: "0E8A16",
    COMMENTED_BY_LABEL_PREFIX: "D93F0B",
    CHANGED_REQUESTED_BY_LABEL_PREFIX: "F5621C",
    CHERRY_PICK_LABEL_PREFIX: "F09C74",
    BRANCH_LABEL_PREFIX: "1D76DB",
}

ALL_LABELS_DICT = {**STATIC_LABELS_DICT, **DYNAMIC_LABELS_DICT}


class REACTIONS:
    ok = "+1"
    notok = "-1"
    laugh = "laugh"
    confused = "confused"
    heart = "heart"
    hooray = "hooray"
    rocket = "rocket"
    eyes = "eyes"
