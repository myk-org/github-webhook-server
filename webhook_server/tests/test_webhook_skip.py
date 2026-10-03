"""Tests for payload-only webhook skipping.

Two of these exist to enforce an invariant rather than a behaviour: skipping may only ever
read *delivery-local* facts, never mutable repository state. A payload saying "this PR has
label verified" can be wrong a second later; a payload saying "this check_run was created"
cannot change, and the terminal delivery arrives separately.
"""

import pytest

from webhook_server.utils.webhook_skip import (
    DELIVERY_LOCAL_CHECK_RUN_FIELDS,
    DELIVERY_LOCAL_FIELDS,
    payload_skip_reason,
)


class TestPayloadSkipReasons:
    def test_ping_skipped(self) -> None:
        assert payload_skip_reason("ping", {}) == "ping"

    def test_status_pending_skipped_but_terminal_processed(self) -> None:
        assert payload_skip_reason("status", {"state": "pending"}) is not None
        for terminal in ("success", "failure", "error"):
            assert payload_skip_reason("status", {"state": terminal}) is None

    def test_check_run_created_skipped_completed_processed(self) -> None:
        assert payload_skip_reason("check_run", {"action": "created", "check_run": {}}) is not None
        assert payload_skip_reason("check_run", {"action": "rerequested", "check_run": {}}) is not None
        assert (
            payload_skip_reason(
                "check_run", {"action": "completed", "check_run": {"name": "tox", "conclusion": "success"}}
            )
            is None
        )

    def test_check_run_can_be_merged_non_success_skipped(self) -> None:
        payload = {"action": "completed", "check_run": {"name": "can-be-merged", "conclusion": "failure"}}
        assert payload_skip_reason("check_run", payload) is not None
        # A successful can-be-merged is a real decision and must be processed
        payload["check_run"]["conclusion"] = "success"
        assert payload_skip_reason("check_run", payload) is None

    def test_push_deletion_and_branch_skipped_tag_processed(self) -> None:
        assert payload_skip_reason("push", {"deleted": True}) is not None
        assert payload_skip_reason("push", {"deleted": False, "ref": "refs/heads/main"}) is not None
        assert payload_skip_reason("push", {"deleted": False, "ref": "refs/tags/v1.0.0"}) is None

    def test_review_thread_non_actionable_skipped(self) -> None:
        assert payload_skip_reason("pull_request_review_thread", {"action": "created"}) is not None
        # resolved/unresolved stay here - the conversation-resolution config half needs
        # repo-local config, which is only knowable after an API call
        for action in ("resolved", "unresolved"):
            assert payload_skip_reason("pull_request_review_thread", {"action": action}) is None

    def test_actionable_events_are_never_skipped(self) -> None:
        assert payload_skip_reason("pull_request", {"action": "opened"}) is None
        assert payload_skip_reason("issue_comment", {"action": "created"}) is None
        assert payload_skip_reason("check_run_suite", {}) is None


class TestDeliveryLocalOnly:
    """Guards against skipping on state that a user can change a second later."""

    def test_payload_skip_reason_ignores_repository_state(self) -> None:
        """A payload stripped to delivery-local fields must decide the same thing.

        If a rule ever starts reading labels, draft, mergeable, head.sha or reviewers, the
        stripped payload loses those keys and the answers diverge - failing here.
        """
        full = {
            "action": "completed",
            "state": "success",
            "ref": "refs/tags/v9",
            "deleted": False,
            "check_run": {"name": "tox", "conclusion": "success"},
            # mutable repository state that must never influence a decision here
            "labels": ["verified", "can-be-merged"],
            "draft": False,
            "mergeable": True,
            "merged": False,
            "requested_reviewers": ["rnetser"],
            "head": {"sha": "deadbeef", "ref": "feature"},
            "base": {"ref": "main"},
            "repository": {"name": "r", "full_name": "o/r", "private": False},
            "pull_request": {"number": 1},
        }
        stripped = {k: v for k, v in full.items() if k in DELIVERY_LOCAL_FIELDS}
        assert "labels" not in stripped, "labels must not be a delivery-local field"

        for event in (
            "ping",
            "status",
            "check_run",
            "push",
            "pull_request",
            "issue_comment",
            "pull_request_review_thread",
        ):
            assert payload_skip_reason(event, full) == payload_skip_reason(event, stripped)

    def test_mutating_repository_state_does_not_change_the_decision(self) -> None:
        """Flipping live state must never change whether we skip."""
        base = {"action": "completed", "check_run": {"name": "tox", "conclusion": "success"}}
        assert payload_skip_reason("check_run", base) is None

        mutated = dict(base)
        mutated["labels"] = ["can-be-merged"]  # someone added a label
        assert payload_skip_reason("check_run", mutated) is None

        mutated["labels"] = []  # ...and removed it again
        assert payload_skip_reason("check_run", mutated) is None

        mutated["draft"] = True
        mutated["mergeable"] = False
        mutated["head"] = {"sha": "0" * 40}
        assert payload_skip_reason("check_run", mutated) is None

    def test_delivery_local_field_list_covers_what_is_read(self) -> None:
        """Every key the rules touch must be declared, so the stripped-payload test is meaningful."""
        # action, state, ref, deleted and check_run (+ name/conclusion within it)
        assert {"action", "state", "ref", "deleted", "check_run"} <= DELIVERY_LOCAL_FIELDS
        assert DELIVERY_LOCAL_CHECK_RUN_FIELDS == {"name", "conclusion"}


@pytest.mark.parametrize(
    ("event", "payload"),
    [
        ("ping", {}),
        ("status", {"state": "pending"}),
        ("check_run", {"action": "created", "check_run": {}}),
        ("push", {"deleted": True}),
    ],
)
def test_skips_always_return_a_reason(event: str, payload: dict) -> None:
    """A skip must always be explainable - an empty reason means an unattributable drop."""
    reason = payload_skip_reason(event, payload)
    assert reason, f"{event} skipped with no reason"
    assert event in reason or "deletion" in reason or "branch push" in reason
