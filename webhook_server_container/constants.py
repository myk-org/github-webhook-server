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


# Gitlab colors require a '#' prefix; e.g: #
USER_LABELS_DICT = {
    "hold": "B60205",
    VERIFIED_LABEL_STR: "0E8A16",
    WIP_STR: "B60205",
    LGTM_STR: "0E8A16",
    "approve": "0E8A16",
}
STATIC_LABELS_DICT = {
    **USER_LABELS_DICT,
    CHERRY_PICKED_LABEL_PREFIX: "1D76DB",
    "size/l": "F5621C",
    "size/m": "F09C74",
    "size/s": "0E8A16",
    "size/xl": "D93F0B",
    "size/xs": "ededed",
    "size/xxl": "B60205",
    NEEDS_REBASE_LABEL_STR: "B60205",
    CAN_BE_MERGED_STR: "0E8A17",
    CHERRY_PICK_LABEL_PREFIX: "F09C74",
}

DYNAMIC_LABELS_DICT = {
    APPROVED_BY_LABEL_PREFIX: "0E8A16",
    CHANGED_REQUESTED_BY_LABEL_PREFIX: "D93F0B",
    COMMENTED_BY_LABEL_PREFIX: "BFD4F2",
    "branch-": "1D76DB",
    "base": "D4C5F9",
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
