from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .contracts import ModelResponse, RunItem
from .guardrails import InputGuardrailResult, OutputGuardrailResult
from .result import RunResult
from .run_context import RunContextWrapper
from .tool_guardrails import ToolInputGuardrailResult, ToolOutputGuardrailResult

if TYPE_CHECKING:
    from .agent import Agent


MAX_TURNS_REACHED = "max_turns_reached"
MAX_STEPS_REACHED = "max_steps_reached"


# 保存一次 agent run 的过程状态
@dataclass
class RunState:
    new_items: list[RunItem] = field(default_factory=list)
    input: Any | None = None
    last_agent: Agent | None = None
    final_answer: Any | None = None
    reached_final_answer: bool = False
    current_turn: int = 0
    max_turns: int | None = None
    steps_taken: int = 0
    max_steps: int | None = None
    handoff_depth: int = 0
    context_wrapper: RunContextWrapper = field(default_factory=RunContextWrapper)
    input_guardrail_results: list[InputGuardrailResult] = field(default_factory=list)
    output_guardrail_results: list[OutputGuardrailResult] = field(default_factory=list)
    tool_input_guardrail_results: list[ToolInputGuardrailResult] = field(default_factory=list)
    tool_output_guardrail_results: list[ToolOutputGuardrailResult] = field(default_factory=list)

    # 运行时判断 和 递增方法
    def can_call_model(self) -> bool:
        return self.model_limit_reason() is None

    def record_model_turn(self) -> None:
        self.current_turn += 1

    def can_execute_tool(self) -> bool:
        return self.tool_limit_reason() is None

    def record_tool_step(self) -> None:
        self.steps_taken += 1

    def next_step_number(self) -> int:
        return self.steps_taken + 1

    def model_limit_reason(self) -> str | None:
        if self.max_turns is not None and self.current_turn >= self.max_turns:
            return MAX_TURNS_REACHED
        return None

    def tool_limit_reason(self) -> str | None:
        if self.max_steps is not None and self.steps_taken >= self.max_steps:
            return MAX_STEPS_REACHED
        return None

    def next_limit_reason(self) -> str | None:
        return self.tool_limit_reason() or self.model_limit_reason()

# 从 RunItem 里派生旧 API 的 step_results
def step_results_from_items(items: tuple[RunItem, ...]) -> list[Any]:
    return [item.payload for item in items if item.item_type == "tool_result"]


def raw_responses_from_items(items: tuple[RunItem, ...]) -> tuple[ModelResponse, ...]:
    return tuple(
        item.payload
        for item in items
        if item.item_type == "model_response"
        and isinstance(item.payload, ModelResponse)
    )

# 统一把 RunState 转成最终返回的 RunResult
def build_run_result(run_state: RunState) -> RunResult:
    new_items = tuple(run_state.new_items)
    return RunResult(
        final_answer=run_state.final_answer,
        step_results=step_results_from_items(new_items),
        reached_final_answer=run_state.reached_final_answer,
        current_turn=run_state.current_turn,
        max_turns=run_state.max_turns,
        steps_taken=run_state.steps_taken,
        max_steps=run_state.max_steps,
        input=run_state.input,
        last_agent=run_state.last_agent,
        context_wrapper=run_state.context_wrapper,
        input_guardrail_results=tuple(run_state.input_guardrail_results),
        output_guardrail_results=tuple(run_state.output_guardrail_results),
        tool_input_guardrail_results=tuple(run_state.tool_input_guardrail_results),
        tool_output_guardrail_results=tuple(run_state.tool_output_guardrail_results),
        raw_responses=raw_responses_from_items(new_items),
        new_items=new_items,
    )
