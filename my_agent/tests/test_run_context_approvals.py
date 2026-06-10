from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import RunContextWrapper, ToolApprovalRequest  # noqa: E402
from agents.run_state import ApprovalSnapshot  # noqa: E402


def test_tool_approval_status_starts_unknown_and_can_be_pending() -> None:
    context = RunContextWrapper(context={"request_id": "req_1"})

    assert context.context == {"request_id": "req_1"}
    assert context.approval_status_for("delete_file", "call_1") == "unknown"

    context.request_tool_call_approval("delete_file", "call_1")

    assert context.approval_status_for("delete_file", "call_1") == "pending"
    assert context.rejection_message_for("delete_file", "call_1") is None


def test_tool_approval_status_records_approved_and_rejected_calls() -> None:
    context = RunContextWrapper()

    context.request_tool_call_approval("delete_file", "call_1")
    context.approve_tool_call("delete_file", "call_1")
    context.request_tool_call_approval("delete_file", "call_2")
    context.reject_tool_call("delete_file", "call_2", "Path is outside workspace.")

    assert context.approval_status_for("delete_file", "call_1") == "approved"
    assert context.rejection_message_for("delete_file", "call_1") is None
    assert context.approval_status_for("delete_file", "call_2") == "rejected"
    assert context.rejection_message_for("delete_file", "call_2") == "Path is outside workspace."


def test_tool_approval_snapshots_round_trip_context_statuses() -> None:
    context = RunContextWrapper()
    approval_requests = (
        ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "pending.txt"},
            reason="Needs approval.",
        ),
        ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_2",
            arguments={"path": "approved.txt"},
            reason="Needs approval.",
        ),
        ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_3",
            arguments={"path": "rejected.txt"},
            reason="Needs approval.",
        ),
    )

    context.request_tool_call_approval("delete_file", "call_1")
    context.approve_tool_call("delete_file", "call_2")
    context.reject_tool_call("delete_file", "call_3", "Outside workspace.")

    snapshots = context.export_tool_approvals(approval_requests)

    assert snapshots == (
        ApprovalSnapshot(
            tool_name="delete_file",
            call_id="call_1",
            arguments={"path": "pending.txt"},
            status="pending",
            reason="Needs approval.",
        ),
        ApprovalSnapshot(
            tool_name="delete_file",
            call_id="call_2",
            arguments={"path": "approved.txt"},
            status="approved",
            reason="Needs approval.",
        ),
        ApprovalSnapshot(
            tool_name="delete_file",
            call_id="call_3",
            arguments={"path": "rejected.txt"},
            status="rejected",
            reason="Needs approval.",
            rejection_message="Outside workspace.",
        ),
    )

    restored = RunContextWrapper()
    restored.import_tool_approvals(snapshots)

    assert restored.approval_status_for("delete_file", "call_1") == "pending"
    assert restored.approval_status_for("delete_file", "call_2") == "approved"
    assert restored.approval_status_for("delete_file", "call_3") == "rejected"
    assert restored.rejection_message_for("delete_file", "call_3") == "Outside workspace."
