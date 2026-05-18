from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .guardrails import InputGuardrailResult, OutputGuardrailResult
    from .run_context import RunContextWrapper


MessageRole = Literal["system", "user", "assistant", "tool_call", "tool_response"]


@dataclass(frozen=True)
class ChatMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ToolArgument:
    name: str
    description: str
    type: str
    required: bool = True


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    arguments: list[ToolArgument]
    returns: str

    def argument_names(self) -> list[str]:
        return [argument.name for argument in self.arguments]


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    arguments: dict[str, Any]
    call_id: str


@dataclass(frozen=True)
class ModelResponse:
    response_id: str | None
    output: list[Any]
    output_text: str | None
    tool_calls: list[ToolCall]
    raw: Any | None = None


@dataclass(frozen=True)
class RunItem:
    item_type: Literal[
        "model_response",
        "model_error",
        "tool_call",
        "tool_result",
        "tool_error",
        "handoff",
        "input_guardrail",
        "output_guardrail",
        "final_output",
        "run_stopped",
    ]
    step_number: int
    payload: Any
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepRecord:
    step_number: int
    messages: list[ChatMessage] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    observation: str | None = None
    is_final_answer: bool = False
    error: str | None = None


@dataclass(frozen=True)
class AgentRunResult:
    final_answer: Any | None
    step_results: list[Any]
    reached_final_answer: bool
    steps_taken: int
    current_turn: int = 0
    max_turns: int | None = None
    max_steps: int | None = None
    context_wrapper: RunContextWrapper | None = None
    input_guardrail_results: tuple[InputGuardrailResult, ...] = ()
    output_guardrail_results: tuple[OutputGuardrailResult, ...] = ()
    raw_responses: tuple[ModelResponse, ...] = ()
    new_items: tuple[RunItem, ...] = ()


@dataclass(frozen=True)
class CodeExecutionResult:
    output: Any | None      # 结果
    logs: str               # print 结果
    is_final_answer: bool = False


def render_tool_signature(tool_spec: ToolSpec) -> str:
    arguments = ", ".join(
        f"{argument.name}: {argument.type}"
        for argument in tool_spec.arguments
    )
    return f"{tool_spec.name}({arguments}) -> {tool_spec.returns}"


def tool_to_prompt_text(tool_spec: ToolSpec) -> str:
    arguments_text = ", ".join(
        f"{argument.name}({argument.type})"
        for argument in tool_spec.arguments
    )
    return (
        f"{tool_spec.name}: {tool_spec.description}\n"
        f"inputs: {arguments_text}\n"
        f"returns: {tool_spec.returns}"
    )
