OTHER_MAIN_BRANCH: str = "master"
TOX_STR: str = "tox"
PRE_COMMIT_STR: str = "pre-commit"
BUILD_AND_PUSH_CONTAINER_STR: str = "build-and-push-container"
SUCCESS_STR: str = "success"
FAILURE_STR: str = "failure"
IN_PROGRESS_STR: str = "in_progress"
QUEUED_STR: str = "queued"
ADD_STR: str = "add"
DELETE_STR: str = "delete"
CAN_BE_MERGED_STR: str = "can-be-merged"
BUILD_CONTAINER_STR: str = "build-container"
PYTHON_MODULE_INSTALL_STR: str = "python-module-install"
CONVENTIONAL_TITLE_STR: str = "conventional-title"
WIP_STR: str = "wip"
LGTM_STR: str = "lgtm"
APPROVE_STR: str = "approve"
LABELS_SEPARATOR: str = "-"
CHERRY_PICK_LABEL_PREFIX: str = f"cherry-pick{LABELS_SEPARATOR}"
CHERRY_PICKED_LABEL_PREFIX: str = "CherryPicked"
APPROVED_BY_LABEL_PREFIX: str = f"approved{LABELS_SEPARATOR}"
LGTM_BY_LABEL_PREFIX: str = f"{LGTM_STR}{LABELS_SEPARATOR}"
CHANGED_REQUESTED_BY_LABEL_PREFIX: str = f"changes-requested{LABELS_SEPARATOR}"
COMMENTED_BY_LABEL_PREFIX: str = f"commented{LABELS_SEPARATOR}"
BRANCH_LABEL_PREFIX: str = f"branch{LABELS_SEPARATOR}"
VERIFIED_LABEL_STR: str = "verified"
NEEDS_REBASE_LABEL_STR: str = "needs-rebase"
HAS_CONFLICTS_LABEL_STR: str = "has-conflicts"
HOLD_LABEL_STR: str = "hold"
SIZE_LABEL_PREFIX: str = "size/"
COMMAND_RETEST_STR = "retest"
COMMAND_CHERRY_PICK_STR = "cherry-pick"
COMMAND_ASSIGN_REVIEWERS_STR = "assign-reviewers"
COMMAND_CHECK_CAN_MERGE_STR = "check-can-merge"
COMMAND_ASSIGN_REVIEWER_STR = "assign-reviewer"

# Gitlab colors require a '#' prefix; e.g: #
USER_LABELS_DICT: dict[str, str] = {
    HOLD_LABEL_STR: "B60205",
    VERIFIED_LABEL_STR: "0E8A16",
    WIP_STR: "B60205",
    LGTM_STR: "0E8A16",
    APPROVE_STR: "0E8A16",
}

STATIC_LABELS_DICT: dict[str, str] = {
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

DYNAMIC_LABELS_DICT: dict[str, str] = {
    APPROVED_BY_LABEL_PREFIX: "0E8A16",
    LGTM_BY_LABEL_PREFIX: "DCED6F",
    COMMENTED_BY_LABEL_PREFIX: "D93F0B",
    CHANGED_REQUESTED_BY_LABEL_PREFIX: "F5621C",
    CHERRY_PICK_LABEL_PREFIX: "F09C74",
    BRANCH_LABEL_PREFIX: "1D76DB",
}

ALL_LABELS_DICT: dict[str, str] = {**STATIC_LABELS_DICT, **DYNAMIC_LABELS_DICT}


class REACTIONS:
    ok: str = "+1"
    notok: str = "-1"
    laugh: str = "laugh"
    confused: str = "confused"
    heart: str = "heart"
    hooray: str = "hooray"
    rocket: str = "rocket"
    eyes: str = "eyes"
