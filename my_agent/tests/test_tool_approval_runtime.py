from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.contracts import ToolCall  # noqa: E402
from agents.run_context import RunContextWrapper  # noqa: E402
from agents.tool_runtime import ToolApprovalDecision, requires_tool_approval  # noqa: E402
from agents.tools import function_tool  # noqa: E402


def test_bool_tool_approval_returns_decision_with_call_id() -> None:
    def echo(text: str) -> str:
        return text

    tool = function_tool(echo, needs_approval=True)
    decision = tool.requires_approval_for(
        RunContextWrapper(),
        object(),
        ToolCall(tool.name, {"text": "hello"}, "call_bool"),
    )

    assert isinstance(decision, ToolApprovalDecision)
    assert decision.requires_approval is True
    assert decision.call_id == "call_bool"
    assert decision.error_message is None


def test_callable_tool_approval_receives_arguments_and_call_id() -> None:
    seen: dict[str, Any] = {}

    def approval(context: RunContextWrapper, arguments: dict[str, Any], call_id: str) -> bool:
        seen["context"] = context
        seen["arguments"] = arguments
        seen["call_id"] = call_id
        return arguments["path"].endswith(".txt") and call_id == "call_args"

    def read_file(path: str) -> str:
        return path

    context = RunContextWrapper()
    tool = function_tool(read_file, needs_approval=approval)
    decision = tool.requires_approval_for(
        context,
        object(),
        ToolCall(tool.name, {"path": "notes.txt"}, "call_args"),
    )

    assert decision.requires_approval is True
    assert decision.call_id == "call_args"
    assert seen == {
        "context": context,
        "arguments": {"path": "notes.txt"},
        "call_id": "call_args",
    }


def test_callable_tool_approval_exception_becomes_decision_error() -> None:
    def approval(context: RunContextWrapper, arguments: dict[str, Any], call_id: str) -> bool:
        raise RuntimeError(f"policy unavailable for {call_id}")

    decision = requires_tool_approval(
        approval,
        RunContextWrapper(),
        object(),
        ToolCall("read_file", {"path": "notes.txt"}, "call_error"),
    )

    assert decision.requires_approval is True
    assert decision.call_id == "call_error"
    assert decision.error_type == "RuntimeError"
    assert decision.error_message == "policy unavailable for call_error"
