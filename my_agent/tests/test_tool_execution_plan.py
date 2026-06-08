from __future__ import annotations

import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents.run_loop as run_loop  # noqa: E402
from agents import Agent, AgentMemory, ModelResponse, ToolCall  # noqa: E402
from agents.run_context import RunContextWrapper  # noqa: E402
from agents.run_state import RunState  # noqa: E402
from agents.run_steps import (  # noqa: E402
    ModelTurnResult,
    ProcessedResponse,
    ToolExecutionPlan,
    build_tool_execution_plan,
)
from agents.tools import function_tool  # noqa: E402


class ScriptedResponseModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.tool_outputs = []

    def get_response(self, messages, tool_specs):
        if not self.responses:
            return ModelResponse(
                response_id=None,
                output=[],
                output_text=None,
                tool_calls=[],
            )
        return self.responses.pop(0)

    def record_tool_output(self, action, output) -> None:
        self.tool_outputs.append((action, output))


def test_tool_execution_plan_query_properties_describe_empty_plan() -> None:
    plan = ToolExecutionPlan(actions=(), tool_calls=(), handoff_calls=())

    assert plan.has_actions is False
    assert plan.has_tool_calls is False
    assert plan.has_handoff_calls is False
    assert plan.has_pending_approval is False
    assert plan.should_pause is False


def test_tool_execution_plan_query_properties_describe_tool_plan() -> None:
    action = ToolCall("lookup", {"query": "weather"}, "call_tool")
    plan = ToolExecutionPlan(
        actions=(action,),
        tool_calls=(action,),
        handoff_calls=(),
    )

    assert plan.has_actions is True
    assert plan.has_tool_calls is True
    assert plan.has_handoff_calls is False
    assert plan.has_pending_approval is False
    assert plan.should_pause is False


def test_tool_execution_plan_query_properties_describe_final_plan() -> None:
    plan = ToolExecutionPlan(actions=(), tool_calls=(), handoff_calls=())

    assert plan.has_actions is False
    assert plan.should_pause is False


def test_tool_execution_plan_query_properties_describe_pending_plan() -> None:
    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_pending")
    plan = ToolExecutionPlan(
        actions=(action,),
        tool_calls=(action,),
        handoff_calls=(),
        pending_approval_calls=(action,),
    )

    assert plan.has_actions is True
    assert plan.has_tool_calls is True
    assert plan.has_pending_approval is True
    assert plan.should_pause is True


def test_build_tool_execution_plan_keeps_model_action_order_and_classifies_calls() -> None:
    target_agent = Agent(memory=AgentMemory(), model=ScriptedResponseModel([]), name="Helper")
    agent = Agent(
        memory=AgentMemory(),
        model=ScriptedResponseModel([]),
        handoffs=[target_agent],
    )
    tool_call = ToolCall("lookup", {"query": "weather"}, "call_tool")
    handoff_call = ToolCall("transfer_to_helper", {"task": "help"}, "call_handoff")
    model_turn = ModelTurnResult(
        response=ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[tool_call, handoff_call],
        ),
        tool_calls=[tool_call, handoff_call],
    )
    processed = ProcessedResponse(
        model_turn=model_turn,
        model_response=model_turn.response,
        tool_calls=[tool_call],
        handoff_calls=[handoff_call],
    )

    plan = build_tool_execution_plan(agent, processed, RunState())

    assert isinstance(plan, ToolExecutionPlan)
    assert plan.actions == (tool_call, handoff_call)
    assert plan.tool_calls == (tool_call,)
    assert plan.handoff_calls == (handoff_call,)
    assert plan.pending_approval_calls == ()


def test_build_tool_execution_plan_separates_pending_approval_calls() -> None:
    agent = Agent(memory=AgentMemory(), model=ScriptedResponseModel([]))
    action = ToolCall("delete_file", {"path": "notes.txt"}, "call_pending")
    context = RunContextWrapper()
    context.request_tool_call_approval("delete_file", "call_pending")
    run_state = RunState(context_wrapper=context)
    model_turn = ModelTurnResult(
        response=ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[action],
        ),
        tool_calls=[action],
    )
    processed = ProcessedResponse(
        model_turn=model_turn,
        model_response=model_turn.response,
        tool_calls=[action],
        handoff_calls=[],
    )

    plan = build_tool_execution_plan(agent, processed, run_state)

    assert plan.actions == (action,)
    assert plan.tool_calls == (action,)
    assert plan.pending_approval_calls == (action,)


def test_run_loop_executes_only_actions_selected_by_tool_execution_plan(monkeypatch) -> None:
    executed: list[str] = []

    def run_tool(text: str) -> str:
        executed.append(text)
        return text

    skipped_call = ToolCall("missing_tool", {}, "call_skip")
    planned_call = ToolCall("run_tool", {"text": "planned"}, "call_run")
    model = ScriptedResponseModel(
        [
            ModelResponse(
                response_id="resp_tools",
                output=[],
                output_text=None,
                tool_calls=[skipped_call, planned_call],
            ),
            ModelResponse(
                response_id="resp_done",
                output=[],
                output_text="done",
                tool_calls=[],
            ),
        ]
    )
    agent = Agent(memory=AgentMemory(), model=model)
    agent.tool_registry.register(function_tool(run_tool))

    def fake_plan(agent, processed_response, run_state):
        return ToolExecutionPlan(
            actions=(planned_call,),
            tool_calls=(planned_call,),
            handoff_calls=(),
        )

    monkeypatch.setattr(run_loop, "build_tool_execution_plan", fake_plan)

    result = agent.run("Use the planned tool.")

    assert executed == ["planned"]
    assert result.final_answer == "done"


def test_run_loop_pauses_pending_approval_plan_without_executing_action(monkeypatch) -> None:
    executed: list[str] = []

    def run_tool(text: str) -> str:
        executed.append(text)
        return text

    pending_call = ToolCall("run_tool", {"text": "pending"}, "call_pending")
    model = ScriptedResponseModel(
        [
            ModelResponse(
                response_id="resp_pending",
                output=[],
                output_text=None,
                tool_calls=[pending_call],
            )
        ]
    )
    agent = Agent(memory=AgentMemory(), model=model)
    agent.tool_registry.register(function_tool(run_tool))

    def fake_plan(agent, processed_response, run_state):
        return ToolExecutionPlan(
            actions=(pending_call,),
            tool_calls=(pending_call,),
            handoff_calls=(),
            pending_approval_calls=(pending_call,),
        )

    monkeypatch.setattr(run_loop, "build_tool_execution_plan", fake_plan)

    result = agent.run("Use a tool that requires approval.")

    assert executed == []
    assert result.reached_final_answer is False
    assert result.final_answer is None
    assert result.new_items[-1].item_type == "run_stopped"
    assert result.new_items[-1].payload == "tool_approval_required"


def test_run_loop_reads_limit_reason_from_run_state_contract(monkeypatch) -> None:
    class ContractOnlyRunState(RunState):
        def next_limit_reason(self) -> str | None:
            return "max_steps_reached"

        def can_execute_tool(self) -> bool:
            raise AssertionError("run loop should use next_limit_reason")

        def can_call_model(self) -> bool:
            raise AssertionError("run loop should use next_limit_reason")

    monkeypatch.setattr(run_loop, "RunState", ContractOnlyRunState)
    model = ScriptedResponseModel(
        [
            ModelResponse(
                response_id="resp_should_not_run",
                output=[],
                output_text="should not run",
                tool_calls=[],
            )
        ]
    )
    agent = Agent(memory=AgentMemory(), model=model)

    result = agent.run("Stop before model call.")

    assert result.reached_final_answer is False
    assert result.final_answer is None
    assert result.new_items[-1].item_type == "run_stopped"
    assert result.new_items[-1].payload == "max_steps_reached"
