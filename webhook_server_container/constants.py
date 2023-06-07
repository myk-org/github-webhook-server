ADD_STR = "add"
DELETE_STR = "delete"
CAN_BE_MERGED_STR = "can-be-merged"
BUILD_CONTAINER_STR = "build-container"
PYTHON_MODULE_INSTALL_STR = "python-module-install"
WIP_STR = "wip"

# Gitlab colors require a '#' prefix; e.g: #
USER_LABELS_DICT = {
    "hold": "B60205",
    "verified": "0E8A16",
    WIP_STR: "B60205",
    "lgtm": "0E8A16",
    "approve": "0E8A16",
    "target-branch-": "F09C74",
}
STATIC_LABELS_DICT = {
    **USER_LABELS_DICT,
    "auto-cherry-pick": "1D76DB",
    "size/l": "F5621C",
    "size/m": "F09C74",
    "size/s": "0E8A16",
    "size/xl": "D93F0B",
    "size/xs": "ededed",
    "size/xxl": "B60205",
    "needs-rebase": "B60205",
    CAN_BE_MERGED_STR: "0E8A17",
}

DYNAMIC_LABELS_DICT = {
    "approved-by-": "0E8A16",
    "changes_requested-by-": "D93F0B",
    "commented-by-": "BFD4F2",
    "branch-": "1D76DB",
    "base": "D4C5F9",
}

ALL_LABELS_DICT = {**STATIC_LABELS_DICT, **DYNAMIC_LABELS_DICT}
