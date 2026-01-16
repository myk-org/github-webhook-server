import types
from collections.abc import Mapping

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
CHERRY_PICKED_LABEL: str = "CherryPicked"
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

# Mapping from exact label strings to their configuration category names.
#
# IMPORTANT: This map is for EXACT-MATCH labels only!
# - Keys must be complete label names (e.g., "hold", "verified", "wip")
# - These are looked up directly: `if label in LABEL_CATEGORY_MAP`
#
# PREFIX-BASED labels are NOT in this map and are handled separately
# by prefix-matching logic in LabelsHandler.is_label_enabled():
# - "size" category: labels like "size/XS", "size/M", "size/XXL" (prefix: SIZE_LABEL_PREFIX)
# - "branch" category: labels like "branch-main", "branch-feature" (prefix: BRANCH_LABEL_PREFIX)
# - "cherry-pick" category: labels like "cherry-pick-main" (prefix: CHERRY_PICK_LABEL_PREFIX)
#
# Do NOT add prefix-based label examples (e.g., "size/XL", "branch-main") to this map.
LABEL_CATEGORY_MAP: dict[str, str] = {
    HOLD_LABEL_STR: "hold",
    VERIFIED_LABEL_STR: "verified",
    WIP_STR: "wip",
    AUTOMERGE_LABEL_STR: "automerge",
    LGTM_STR: "lgtm",  # Always enabled
    APPROVE_STR: "approve",  # Always enabled
    NEEDS_REBASE_LABEL_STR: "needs-rebase",
    HAS_CONFLICTS_LABEL_STR: "has-conflicts",
    CAN_BE_MERGED_STR: "can-be-merged",
}

STATIC_LABELS_DICT: dict[str, str] = {
    **USER_LABELS_DICT,
    CHERRY_PICKED_LABEL: "1D76DB",
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

_ALL_LABELS_DICT: dict[str, str] = {**STATIC_LABELS_DICT, **DYNAMIC_LABELS_DICT}
ALL_LABELS_DICT: Mapping[str, str] = types.MappingProxyType(_ALL_LABELS_DICT)

# Default label colors - uses ALL_LABELS_DICT as the source of truth
# These are used when no custom colors are configured via labels.colors
# Using MappingProxyType to prevent accidental mutation of the shared dict
DEFAULT_LABEL_COLORS: Mapping[str, str] = ALL_LABELS_DICT

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


# Built-in check run names that cannot be overridden by custom checks
BUILTIN_CHECK_NAMES: frozenset[str] = frozenset({
    TOX_STR,
    PRE_COMMIT_STR,
    BUILD_CONTAINER_STR,
    PYTHON_MODULE_INSTALL_STR,
    CONVENTIONAL_TITLE_STR,
    CAN_BE_MERGED_STR,
})


class REACTIONS:
    ok: str = "+1"
    notok: str = "-1"
    laugh: str = "laugh"
    confused: str = "confused"
    heart: str = "heart"
    hooray: str = "hooray"
    rocket: str = "rocket"
    eyes: str = "eyes"
