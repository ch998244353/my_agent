from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import Agent, AgentMemory, ToolCall  # noqa: E402
from agents.contracts import ToolApprovalRequest  # noqa: E402
from agents.run_context import RunContextWrapper  # noqa: E402
from agents.run_state import RunState  # noqa: E402
from agents.run_steps import (  # noqa: E402
    ModelTurnResult,
    NextStepStopped,
    execute_tool_call,
    resolve_tool_final_output_step,
)
from agents.tools import function_tool  # noqa: E402


class RecordingModel:
    def __init__(self) -> None:
        self.tool_outputs: list[tuple[ToolCall, str]] = []

    def record_tool_output(self, action: ToolCall, output: str) -> None:
        self.tool_outputs.append((action, output))


def test_tool_approval_required_pauses_before_handler() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return "deleted"

    model = RecordingModel()
    agent = Agent(memory=AgentMemory(), model=model)
    tool = function_tool(delete_file, needs_approval=True)
    agent.tool_registry.register(tool)
    context = RunContextWrapper()
    run_state = RunState(context_wrapper=context)
    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_approval")

    outcome = execute_tool_call(
        agent,
        action,
        run_state,
        step_number=1,
        tool_use_behavior="run_llm_again",
    )

    assert calls == []
    assert outcome.should_stop is True
    assert outcome.is_final_answer is False
    assert outcome.stop_reason == "tool_approval_required"
    assert context.approval_status_for("delete_file", "call_approval") == "pending"
    assert run_state.steps_taken == 0
    assert model.tool_outputs == []

    approval_items = [
        item for item in run_state.new_items if item.item_type == "tool_approval_required"
    ]
    assert len(approval_items) == 1
    approval_request = approval_items[0].payload
    assert isinstance(approval_request, ToolApprovalRequest)
    assert approval_request.tool_name == "delete_file"
    assert approval_request.call_id == "call_approval"
    assert approval_request.arguments == {"path": "notes.txt"}

    stopped_items = [item for item in run_state.new_items if item.item_type == "run_stopped"]
    assert [item.payload for item in stopped_items] == ["tool_approval_required"]

    next_step = resolve_tool_final_output_step(
        ModelTurnResult(response=None, tool_calls=[action]),
        outcome,
    )
    assert next_step is not None
    assert isinstance(next_step.next_step, NextStepStopped)
    assert next_step.next_step.reason == "tool_approval_required"


def test_rejected_tool_approval_returns_observation_without_handler() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return "deleted"

    model = RecordingModel()
    agent = Agent(memory=AgentMemory(), model=model)
    tool = function_tool(delete_file, needs_approval=True)
    agent.tool_registry.register(tool)
    context = RunContextWrapper()
    context.reject_tool_call(
        "delete_file",
        "call_rejected",
        "User refused deleting notes.txt.",
    )
    run_state = RunState(context_wrapper=context)
    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_rejected")

    outcome = execute_tool_call(
        agent,
        action,
        run_state,
        step_number=2,
        tool_use_behavior="run_llm_again",
    )

    observation = (
        "Tool 'delete_file' observation\n"
        "status: error\n"
        "reason: tool_approval_rejected\n"
        "detail: User refused deleting notes.txt."
    )
    assert calls == []
    assert outcome.should_stop is False
    assert outcome.result_value == "User refused deleting notes.txt."
    assert outcome.observation == observation
    assert context.approval_status_for("delete_file", "call_rejected") == "rejected"
    assert [item.item_type for item in run_state.new_items] == ["tool_result"]
    assert run_state.new_items[0].metadata["tool_execution"]["reason"] == (
        "tool_approval_rejected"
    )
    assert model.tool_outputs == [(action, observation)]
