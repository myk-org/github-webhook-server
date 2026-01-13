OTHER_MAIN_BRANCH: str = "master"
TOX_STR: str = "tox"
PRE_COMMIT_STR: str = "pre-commit"
PREK_STR: str = "prek"
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
COMMAND_RETEST_STR: str = "retest"
COMMAND_REPROCESS_STR: str = "reprocess"
COMMAND_CHERRY_PICK_STR: str = "cherry-pick"
COMMAND_ASSIGN_REVIEWERS_STR: str = "assign-reviewers"
COMMAND_CHECK_CAN_MERGE_STR: str = "check-can-merge"
COMMAND_ASSIGN_REVIEWER_STR: str = "assign-reviewer"
COMMAND_ADD_ALLOWED_USER_STR: str = "add-allowed-user"
COMMAND_AUTOMERGE_STR: str = "automerge"
COMMAND_REGENERATE_WELCOME_STR: str = "regenerate-welcome"
AUTOMERGE_LABEL_STR: str = "automerge"
ROOT_APPROVERS_KEY: str = "root-approvers"

# Gitlab colors require a '#' prefix; e.g: #
USER_LABELS_DICT: dict[str, str] = {
    HOLD_LABEL_STR: "B60205",
    VERIFIED_LABEL_STR: "0E8A16",
    WIP_STR: "B60205",
    LGTM_STR: "0E8A16",
    APPROVE_STR: "0E8A16",
    AUTOMERGE_LABEL_STR: "0E8A16",
}

# Mapping from label strings to their configuration category names
LABEL_CATEGORY_MAP: dict[str, str] = {
    HOLD_LABEL_STR: "hold",
    VERIFIED_LABEL_STR: "verified",
    WIP_STR: "wip",
    AUTOMERGE_LABEL_STR: "automerge",
    LGTM_STR: "lgtm",  # Always enabled
    APPROVE_STR: "approve",  # Always enabled
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

# Default label colors - uses ALL_LABELS_DICT as the source of truth
# These are used when no custom colors are configured via labels.colors
DEFAULT_LABEL_COLORS: dict[str, str] = ALL_LABELS_DICT

# All configurable label categories (for enabled-labels config)
# Note: reviewed-by is NOT in this list because it cannot be disabled
CONFIGURABLE_LABEL_CATEGORIES: set[str] = {
    "verified",
    "hold",
    "wip",
    "needs-rebase",
    "has-conflicts",
    "can-be-merged",
    "size",
    "branch",
    "cherry-pick",
    "automerge",
}


class REACTIONS:
    ok: str = "+1"
    notok: str = "-1"
    laugh: str = "laugh"
    confused: str = "confused"
    heart: str = "heart"
    hooray: str = "hooray"
    rocket: str = "rocket"
    eyes: str = "eyes"
