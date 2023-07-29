from flask import Flask


FLASK_APP = Flask("webhook-server")

ADD_STR = "add"
DELETE_STR = "delete"
CAN_BE_MERGED_STR = "can-be-merged"
BUILD_CONTAINER_STR = "build-container"
PYTHON_MODULE_INSTALL_STR = "python-module-install"
WIP_STR = "wip"
CHERRY_PICK_LABEL_PREFIX = "cherry-pick-"
CHERRY_PICKED_LABEL_PREFIX = "CherryPicked"
APPROVED_BY_LABEL_PREFIX = "ApprovedBy-"
CHANGED_REQUESTED_BY_LABEL_PREFIX = "ChangesRequestedBy-"
COMMENTED_BY_LABEL_PREFIX = "CommentedBy-"
VERIFIED_LABEL_STR = "verified"
LGTM_STR = "lgtm"
NEEDS_REBASE_LABEL_STR = "needs-rebase"
HOLD_LABEL_STR = "hold"

# Gitlab colors require a '#' prefix; e.g: #
USER_LABELS_DICT = {
    HOLD_LABEL_STR: "B60205",
    VERIFIED_LABEL_STR: "0E8A16",
    WIP_STR: "B60205",
    LGTM_STR: "0E8A16",
}

STATIC_LABELS_DICT = {
    **USER_LABELS_DICT,
    CHERRY_PICKED_LABEL_PREFIX: "1D76DB",
    "size/L": "F5621C",
    "size/M": "F09C74",
    "size/S": "0E8A16",
    "size/XL": "D93F0B",
    "size/XS": "ededed",
    "size/XXL": "B60205",
    NEEDS_REBASE_LABEL_STR: "B60205",
    CAN_BE_MERGED_STR: "0E8A17",
}

DYNAMIC_LABELS_DICT = {
    APPROVED_BY_LABEL_PREFIX: "0E8A16",
    COMMENTED_BY_LABEL_PREFIX: "D93F0B",
    CHANGED_REQUESTED_BY_LABEL_PREFIX: "F5621C",
    CHERRY_PICK_LABEL_PREFIX: "F09C74",
    "branch-": "1D76DB",
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
