from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contracts import AgentRunResult, ModelResponse, RunItem
from .guardrails import InputGuardrailResult, OutputGuardrailResult
from .run_context import RunContextWrapper

# 保存一次 agent run 的过程状态
@dataclass
class RunState:
    new_items: list[RunItem] = field(default_factory=list)
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

    # 运行时判断和递增方法
    def can_call_model(self) -> bool:
        return self.max_turns is None or self.current_turn < self.max_turns

    def record_model_turn(self) -> None:
        self.current_turn += 1

    def can_execute_tool(self) -> bool:
        return self.max_steps is None or self.steps_taken < self.max_steps

    def record_tool_step(self) -> None:
        self.steps_taken += 1

    def next_step_number(self) -> int:
        return self.steps_taken + 1

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

# 统一把 RunState 转成最终返回的 AgentRunResult
def build_run_result(run_state: RunState) -> AgentRunResult:
    new_items = tuple(run_state.new_items)
    return AgentRunResult(
        final_answer=run_state.final_answer,
        step_results=step_results_from_items(new_items),
        reached_final_answer=run_state.reached_final_answer,
        current_turn=run_state.current_turn,
        max_turns=run_state.max_turns,
        steps_taken=run_state.steps_taken,
        max_steps=run_state.max_steps,
        context_wrapper=run_state.context_wrapper,
        input_guardrail_results=tuple(run_state.input_guardrail_results),
        output_guardrail_results=tuple(run_state.output_guardrail_results),
        raw_responses=raw_responses_from_items(new_items),
        new_items=new_items,
    )
