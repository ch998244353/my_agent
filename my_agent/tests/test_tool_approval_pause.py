from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import Agent, AgentMemory, ToolCall  # noqa: E402
from agents.coding_agent import CodingAgentProfile, build_coding_agent  # noqa: E402
from agents.contracts import ModelResponse, ToolApprovalRequest  # noqa: E402
from agents.environment import CommandResult  # noqa: E402
from agents.run_context import RunContextWrapper  # noqa: E402
from agents.run_state import ApprovalSnapshot, RunState, RunStateSnapshot  # noqa: E402
from agents.run_steps import (  # noqa: E402
    ModelTurnResult,
    NextStepStopped,
    execute_tool_call,
    resolve_tool_final_output_step,
)
from agents.tools import function_tool  # noqa: E402
from agents.trajectory import trajectory_events_from_result  # noqa: E402
from agents.workspace_manifest import WorkspaceManifest  # noqa: E402


class RecordingModel:
    def __init__(self) -> None:
        self.tool_outputs: list[tuple[ToolCall, str]] = []

    def record_tool_output(self, action: ToolCall, output: str) -> None:
        self.tool_outputs.append((action, output))


def _resume_pending_tool_approvals():
    try:
        from agents.run_resume import resume_pending_tool_approvals
    except ModuleNotFoundError:
        pytest.fail("agents.run_resume.resume_pending_tool_approvals is missing")
    return resume_pending_tool_approvals


def _resume_agent_loop():
    try:
        from agents.run_loop import resume_agent_loop
    except ImportError:
        pytest.fail("agents.run_loop.resume_agent_loop is missing")
    return resume_agent_loop


def test_resume_api_is_exported_from_agents_package() -> None:
    try:
        from agents import (  # noqa: PLC0415
            ApprovalSnapshot as PublicApprovalSnapshot,
            ResumeToolApprovalResult as PublicResumeToolApprovalResult,
            RunStateSnapshot as PublicRunStateSnapshot,
            resume_agent_loop as public_resume_agent_loop,
            resume_pending_tool_approvals as public_resume_pending_tool_approvals,
        )
    except ImportError:
        pytest.fail("resume state API is not exported from agents package")

    assert PublicApprovalSnapshot is ApprovalSnapshot
    assert PublicRunStateSnapshot is RunStateSnapshot
    assert public_resume_agent_loop is _resume_agent_loop()
    assert public_resume_pending_tool_approvals is _resume_pending_tool_approvals()
    assert PublicResumeToolApprovalResult.__name__ == "ResumeToolApprovalResult"


class ApprovalFlowModel:
    def __init__(self, action: ToolCall) -> None:
        self.action = action
        self.calls = 0
        self.tool_outputs: list[tuple[ToolCall, str]] = []

    def get_response(self, messages, tool_specs):
        self.calls += 1
        if self.tool_outputs:
            return ModelResponse(
                response_id=f"resp_final_{self.calls}",
                output=[],
                output_text=f"model saw: {self.tool_outputs[-1][1]}",
                tool_calls=[],
            )
        if self.calls == 1:
            return ModelResponse(
                response_id="resp_request_approval",
                output=[],
                output_text=None,
                tool_calls=[self.action],
            )
        return ModelResponse(
            response_id="resp_unexpected",
            output=[],
            output_text="model was called before approval finished",
            tool_calls=[],
        )

    def record_tool_output(self, action: ToolCall, output: str) -> None:
        self.tool_outputs.append((action, output))


class RecordingShellEnvironment:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(
        self,
        command,
        cwd=None,
        *,
        timeout_seconds=None,
        env=None,
    ) -> CommandResult:
        self.commands.append(command)
        return CommandResult(
            command=command,
            cwd=cwd or ".",
            returncode=0,
            stdout=f"ran {command}",
        )


CODING_SMOKE_PATCH = """*** Begin Patch
*** Update File: calc.py
@@
-def add(a, b):
-    return a - b
+def add(a, b):
+    return a + b
*** End Patch"""


class CodingSmokeModel:
    def __init__(self) -> None:
        self.calls = 0
        self.tool_outputs: list[tuple[ToolCall, str]] = []

    def get_response(self, messages, tool_specs):
        _ = messages, tool_specs
        self.calls += 1
        sequence = [
            ToolCall("list_workspace_files", {"path": "."}, "call_list"),
            ToolCall("read_workspace_file", {"path": "calc.py"}, "call_read"),
            ToolCall("run_test_command", {}, "call_test_before"),
            ToolCall(
                "apply_patch",
                {"patch": CODING_SMOKE_PATCH, "dry_run": True},
                "call_patch_dry",
            ),
            ToolCall(
                "apply_patch",
                {"patch": CODING_SMOKE_PATCH, "dry_run": False},
                "call_patch_apply",
            ),
            ToolCall("run_test_command", {}, "call_test_after"),
            ToolCall("final_answer", {"answer": "smoke complete"}, "call_final"),
        ]
        if self.calls <= len(sequence):
            return ModelResponse(
                response_id=f"resp_{self.calls}",
                output=[],
                output_text=None,
                tool_calls=[sequence[self.calls - 1]],
            )
        return ModelResponse(
            response_id=f"resp_{self.calls}",
            output=[],
            output_text=None,
            tool_calls=[],
        )

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


def test_approved_tool_approval_executes_without_second_approval() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return "deleted"

    model = RecordingModel()
    agent = Agent(memory=AgentMemory(), model=model)
    tool = function_tool(delete_file, needs_approval=True)
    agent.tool_registry.register(tool)
    context = RunContextWrapper()
    context.approve_tool_call("delete_file", "call_approved")
    run_state = RunState(context_wrapper=context)
    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_approved")

    outcome = execute_tool_call(
        agent,
        action,
        run_state,
        step_number=1,
        tool_use_behavior="run_llm_again",
    )

    assert calls == ["notes.txt"]
    assert outcome.result_value == "deleted"
    assert outcome.should_stop is False
    assert run_state.steps_taken == 1
    assert model.tool_outputs == [(action, "deleted")]


def test_resume_approved_tool_approval_executes_pending_tool_once() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return "deleted"

    model = RecordingModel()
    agent = Agent(memory=AgentMemory(), model=model)
    tool = function_tool(delete_file, needs_approval=True)
    agent.tool_registry.register(tool)
    run_state = RunState.from_snapshot(
        RunStateSnapshot(
            input="delete notes.txt",
            last_agent_name=agent.name,
            last_response_id="resp_1",
            current_turn=1,
            steps_taken=0,
            max_turns=None,
            max_steps=None,
            tool_approvals=(
                ApprovalSnapshot(
                    tool_name="delete_file",
                    call_id="call_approved",
                    arguments={"path": "notes.txt"},
                    status="approved",
                ),
            ),
            model_responses=(),
            new_items=(),
        ),
        agent=agent,
    )

    result = _resume_pending_tool_approvals()(agent, run_state)

    assert calls == ["notes.txt"]
    assert result.has_pending_approvals is False
    assert run_state.pending_tool_calls == ()
    assert len(result.outcomes) == 1
    assert result.outcomes[0].result_value == "deleted"
    assert result.outcomes[0].should_stop is False
    assert run_state.steps_taken == 1
    assert model.tool_outputs == [
        (ToolCall("delete_file", {"path": "notes.txt"}, "call_approved"), "deleted")
    ]


def test_resume_rejected_tool_approval_records_rejection_observation() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return "deleted"

    model = RecordingModel()
    agent = Agent(memory=AgentMemory(), model=model)
    tool = function_tool(delete_file, needs_approval=True)
    agent.tool_registry.register(tool)
    run_state = RunState.from_snapshot(
        {
            "input": "delete notes.txt",
            "last_agent_name": agent.name,
            "last_response_id": "resp_1",
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [
                {
                    "tool_name": "delete_file",
                    "call_id": "call_rejected",
                    "arguments": {"path": "notes.txt"},
                    "status": "rejected",
                    "rejection_message": "User refused deleting notes.txt.",
                }
            ],
            "model_responses": [],
            "new_items": [],
        },
        agent=agent,
    )

    result = _resume_pending_tool_approvals()(agent, run_state)

    assert calls == []
    assert result.has_pending_approvals is False
    assert run_state.pending_tool_calls == ()
    assert len(result.outcomes) == 1
    assert result.outcomes[0].result_value == "User refused deleting notes.txt."
    assert result.outcomes[0].observation == (
        "Tool 'delete_file' observation\n"
        "status: error\n"
        "reason: tool_approval_rejected\n"
        "detail: User refused deleting notes.txt."
    )
    assert run_state.steps_taken == 1
    assert run_state.new_items[0].metadata["approval_status"] == "rejected"


def test_resume_stops_at_first_unresolved_approval_to_preserve_order() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return f"deleted {path}"

    model = RecordingModel()
    agent = Agent(memory=AgentMemory(), model=model)
    tool = function_tool(delete_file, needs_approval=True)
    agent.tool_registry.register(tool)
    run_state = RunState.from_snapshot(
        {
            "input": "delete files",
            "last_agent_name": agent.name,
            "last_response_id": "resp_1",
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [
                {
                    "tool_name": "delete_file",
                    "call_id": "call_a",
                    "arguments": {"path": "a.txt"},
                    "status": "approved",
                },
                {
                    "tool_name": "delete_file",
                    "call_id": "call_b",
                    "arguments": {"path": "b.txt"},
                    "status": "pending",
                },
                {
                    "tool_name": "delete_file",
                    "call_id": "call_c",
                    "arguments": {"path": "c.txt"},
                    "status": "approved",
                },
            ],
            "model_responses": [],
            "new_items": [],
        },
        agent=agent,
    )

    result = _resume_pending_tool_approvals()(agent, run_state)

    assert calls == ["a.txt"]
    assert result.has_pending_approvals is True
    assert run_state.pending_tool_calls == (
        ToolCall("delete_file", {"path": "b.txt"}, "call_b"),
        ToolCall("delete_file", {"path": "c.txt"}, "call_c"),
    )
    assert [outcome.action.call_id for outcome in result.outcomes] == ["call_a"]


def test_approval_resume_round_trip_approved_continues_run_loop() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return f"deleted {path}"

    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_round_trip")
    model = ApprovalFlowModel(action)
    agent = Agent(memory=AgentMemory(), model=model)
    agent.tool_registry.register(function_tool(delete_file, needs_approval=True))

    first_result = agent.run("delete notes.txt")

    assert calls == []
    assert model.calls == 1
    assert first_result.has_pending_approvals is True
    assert first_result.reached_final_answer is False

    snapshot = first_result.to_state()
    json.dumps(snapshot)
    snapshot["tool_approvals"][0]["status"] = "approved"
    restored_state = RunState.from_snapshot(snapshot, agent=agent)

    resumed_result = _resume_agent_loop()(agent, restored_state)

    assert calls == ["notes.txt"]
    assert resumed_result.has_pending_approvals is False
    assert resumed_result.reached_final_answer is True
    assert resumed_result.final_answer == "model saw: deleted notes.txt"
    assert model.calls == 2


def test_unapproved_snapshot_resume_still_reports_pending_approval() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return f"deleted {path}"

    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_round_trip_pending")
    model = ApprovalFlowModel(action)
    agent = Agent(memory=AgentMemory(), model=model)
    agent.tool_registry.register(function_tool(delete_file, needs_approval=True))

    first_result = agent.run("delete notes.txt")
    snapshot = first_result.to_state()
    restored_state = RunState.from_snapshot(snapshot, agent=agent)

    resumed_result = _resume_agent_loop()(agent, restored_state)

    assert calls == []
    assert resumed_result.has_pending_approvals is True
    assert len(resumed_result.pending_approval_summaries) == 1
    assert resumed_result.pending_approval_summaries[0].tool_name == "delete_file"
    assert resumed_result.pending_approval_summaries[0].call_id == "call_round_trip_pending"
    assert model.calls == 1


def test_coding_agent_risky_shell_approval_round_trip_executes_after_context_approval(
    tmp_path,
) -> None:
    action = ToolCall(
        "run_shell_command",
        {"command": "pip install requests"},
        "call_shell_approval",
    )
    model = ApprovalFlowModel(action)
    environment = RecordingShellEnvironment()
    setup = build_coding_agent(
        model=model,
        workspace=tmp_path,
        profile=CodingAgentProfile.shell_test(),
        environment=environment,
    )

    first_result = setup.agent.run("install a dependency", config=setup.run_config)

    assert environment.commands == []
    assert first_result.has_pending_approvals is True
    approval = first_result.pending_approval_summaries[0]
    assert approval.tool_name == "run_shell_command"
    assert approval.call_id == "call_shell_approval"
    snapshot = first_result.to_state()
    context = RunContextWrapper(context=setup.run_config.context)
    context.approve_tool_call(approval.tool_name, approval.call_id)
    restored_state = RunState.from_snapshot(
        snapshot,
        agent=setup.agent,
        context_wrapper=context,
    )

    resumed_result = _resume_agent_loop()(setup.agent, restored_state, setup.run_config)

    assert environment.commands == ["pip install requests"]
    assert resumed_result.has_pending_approvals is False
    assert resumed_result.reached_final_answer is True
    assert "ran pip install requests" in resumed_result.final_answer
    assert model.calls == 2


def test_edit_local_smoke_pauses_actual_patch_then_resumes_with_trajectory(
    tmp_path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    calc_path = repo / "calc.py"
    calc_path.write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import add\n\n"
        "def test_add():\n"
        "    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    model = CodingSmokeModel()
    manifest = WorkspaceManifest(
        root=repo,
        default_test_command="python -m pytest -q",
    )
    setup = build_coding_agent(
        model=model,
        manifest=manifest,
        profile=CodingAgentProfile.edit_local(max_turns=20, max_steps=20),
    )

    first_result = setup.agent.run(
        "Run the end-to-end coding smoke.",
        config=setup.run_config,
    )

    assert first_result.has_pending_approvals is True
    approval = first_result.pending_approval_summaries[0]
    assert approval.tool_name == "apply_patch"
    assert approval.call_id == "call_patch_apply"
    assert "return a - b" in calc_path.read_text(encoding="utf-8")
    assert [call.call_id for call, _ in model.tool_outputs] == [
        "call_list",
        "call_read",
        "call_test_before",
        "call_patch_dry",
    ]
    first_event_types = [
        event.event_type
        for event in trajectory_events_from_result(
            first_result,
            run_id="smoke_first",
            task="Run the end-to-end coding smoke.",
            workspace_root=str(repo),
        )
    ]
    assert "approval_required" in first_event_types
    assert first_event_types[-1] == "run_stopped"

    snapshot = first_result.to_state()
    context = RunContextWrapper(context=setup.run_config.context)
    context.approve_tool_call(approval.tool_name, approval.call_id)
    restored_state = RunState.from_snapshot(
        snapshot,
        agent=setup.agent,
        context_wrapper=context,
    )

    resumed_result = _resume_agent_loop()(
        setup.agent,
        restored_state,
        setup.run_config,
    )

    assert resumed_result.has_pending_approvals is False
    assert resumed_result.reached_final_answer is True
    assert resumed_result.final_answer == "smoke complete"
    assert "return a + b" in calc_path.read_text(encoding="utf-8")
    assert [call.call_id for call, _ in model.tool_outputs] == [
        "call_list",
        "call_read",
        "call_test_before",
        "call_patch_dry",
        "call_patch_apply",
        "call_test_after",
        "call_final",
    ]
    resumed_event_types = [
        event.event_type
        for event in trajectory_events_from_result(
            resumed_result,
            run_id="smoke_resumed",
            task="Run the end-to-end coding smoke.",
            workspace_root=str(repo),
        )
    ]
    assert "approval_required" in resumed_event_types
    assert resumed_event_types[-1] == "final_output"


def test_approval_resume_round_trip_rejected_continues_run_loop() -> None:
    calls: list[str] = []

    def delete_file(path: str) -> str:
        calls.append(path)
        return f"deleted {path}"

    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_round_trip_reject")
    model = ApprovalFlowModel(action)
    agent = Agent(memory=AgentMemory(), model=model)
    agent.tool_registry.register(function_tool(delete_file, needs_approval=True))

    first_result = agent.run("delete notes.txt")
    snapshot = first_result.to_state()
    json.dumps(snapshot)
    snapshot["tool_approvals"][0]["status"] = "rejected"
    snapshot["tool_approvals"][0][
        "rejection_message"
    ] = "User refused deleting notes.txt."
    restored_state = RunState.from_snapshot(snapshot, agent=agent)

    resumed_result = _resume_agent_loop()(agent, restored_state)

    assert calls == []
    assert resumed_result.reached_final_answer is True
    assert "tool_approval_rejected" in resumed_result.final_answer
    assert "User refused deleting notes.txt." in resumed_result.final_answer
    assert model.calls == 2
