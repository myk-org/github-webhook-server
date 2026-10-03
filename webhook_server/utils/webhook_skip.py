"""Decide whether a webhook delivery can be dropped before spending any API call.

Every rule here is a fact about the *delivery* - the event type and fields that were fixed
the moment GitHub sent it. None of them describes mutable repository state.

That distinction is the whole point. A payload that says "this PR currently has label
verified" can be wrong a second later; skipping on it would act on stale state. A payload
that says "this check_run was created" cannot change - and the terminal ``completed``
delivery, which carries the decision we actually act on, arrives separately.

So the rule this module enforces is:

    Only delivery-local facts may be read. Never repository state.

Skipping is safe because every skipped delivery is a link in a sequence whose terminal
element arrives on its own webhook - a ``status: pending`` is followed by a terminal
status, a ``check_run: created`` by its ``completed``. Nothing is lost by not
constructing a client for them.

Mutating-state rules belong in the handlers, behind
:func:`~webhook_server.utils.staleness.is_stale_for_pr`, which re-reads live state and
compares it to the payload.

This module is the single source of truth for those rules: ``process_webhook`` calls it
before constructing anything, and ``process()`` calls the same function, so the two
cannot drift apart.
"""

from typing import Any, Final

from webhook_server.utils.constants import CAN_BE_MERGED_STR, SUCCESS_STR

# Delivery-local field allowlist. Nothing outside this set may influence a decision here.
# Enforced by test_payload_skip_reason_ignores_repository_state: a payload stripped to
# these keys must produce the same answer as the full payload.
DELIVERY_LOCAL_FIELDS: Final[frozenset[str]] = frozenset({
    "action",
    "state",
    "ref",
    "deleted",
    "check_run",
})

# check_run sub-fields that are delivery-local
DELIVERY_LOCAL_CHECK_RUN_FIELDS: Final[frozenset[str]] = frozenset({"name", "conclusion"})

_NON_ACTIONABLE_REVIEW_THREAD_ACTIONS: Final[frozenset[str]] = frozenset({"resolved", "unresolved"})


def _check_run_skip_reason(hook_data: dict[str, Any]) -> str | None:
    """check_run rules. Both are facts about this delivery."""
    action = hook_data.get("action", "")
    if action != "completed":
        # A terminal delivery for this check run follows separately.
        return f"check_run (action={action}, skipped)"

    check_run = hook_data.get("check_run") or {}
    # This is the server's own check; its conclusion here is final for this delivery and
    # the merge verdict is recomputed on the next event that can change it.
    if check_run.get("name") == CAN_BE_MERGED_STR and check_run.get("conclusion", "") != SUCCESS_STR:
        return f"check_run (can-be-merged, conclusion={check_run.get('conclusion', '')}, skipped)"

    return None


def payload_skip_reason(event_type: str, hook_data: dict[str, Any]) -> str | None:
    """Return why this delivery needs no processing, or None if it must be handled.

    Pure function of the payload. Performs no I/O and issues no API call, so it is safe to
    call before a GitHub client exists.

    Deliberately excludes the ``pull_request_review_thread`` "conversation resolution
    disabled" rule: that setting can be overridden by repo-local config
    (``.github-webhook-server.yaml``), which lives in the repository and is therefore only
    knowable after an API call. The non-actionable-``action`` half of that rule *is* here;
    the config half stays in ``process()``.

    Args:
        event_type: X-GitHub-Event value
        hook_data: parsed webhook payload

    Returns:
        A human-readable skip reason, or None when the delivery must be processed.
    """
    if event_type == "ping":
        return "ping"

    # Only terminal states (success, failure, error) carry a decision; pending is followed
    # by its own terminal delivery.
    if event_type == "status" and hook_data.get("state") == "pending":
        return "status (state=pending, skipped)"

    if event_type == "push":
        if hook_data.get("deleted"):
            # The old path logged two lines; both substrings are preserved so existing
            # operator greps and log assertions keep matching.
            return "Branch/tag deletion detected, skipping processing - deletion event (skipped)"
        # PushHandler only processes tags; a branch push has nothing to do.
        if not str(hook_data.get("ref", "")).startswith("refs/tags/"):
            return f"branch push (skipped) - Skipping clone for branch push: {hook_data.get('ref', '')}"

    if event_type == "check_run":
        return _check_run_skip_reason(hook_data)

    if event_type == "pull_request_review_thread":
        action = hook_data.get("action", "")
        if action not in _NON_ACTIONABLE_REVIEW_THREAD_ACTIONS:
            return f"pull_request_review_thread (action={action}, skipped)"

    return None
