from flask import Flask
from flask.logging import default_handler
from simple_logger.logger import get_logger

FLASK_APP = Flask("webhook-server")
FLASK_APP.logger.removeHandler(default_handler)
FLASK_APP.logger.addHandler(get_logger(FLASK_APP.logger.name).handlers[0])

OTHER_MAIN_BRANCH = "master"

APP_ROOT_PATH = "/webhook_server"
TOX_STR = "tox"
PRE_COMMIT_STR = "pre-commit"
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
