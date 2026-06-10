from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .tool_schema import schema_type_label

if TYPE_CHECKING:
    from .guardrails import InputGuardrailResult, OutputGuardrailResult
    from .run_context import RunContextWrapper
    from .tool_guardrails import ToolInputGuardrailResult, ToolOutputGuardrailResult


MessageRole = Literal["system", "user", "assistant", "tool_call", "tool_response"]
TOOL_APPROVAL_REQUIRED_METADATA_KEY = "approval_required"


@dataclass(frozen=True)
class ChatMessage:
    role: MessageRole
    content: str


@dataclass(frozen=True)
class ToolArgument:
    name: str
    description: str
    schema: dict[str, Any] = field(default_factory=dict)
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
class ToolApprovalRequest:
    tool_name: str
    call_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None


@dataclass(frozen=True)
class ModelResponse:
    response_id: str | None
    output: list[Any]
    output_text: str | None
    tool_calls: list[ToolCall]
    refusal: str | None = None
    raw: Any | None = None
    usage: dict[str, Any] | None = None
    request_summary: dict[str, Any] | None = None
    request_id: str | None = None


# 存“某个工具输入 Guardrail 已运行”的事件 
@dataclass(frozen=True)
class RunItem:
    item_type: Literal[
        "model_response",
        "model_error",
        "tool_call",
        "tool_result",
        "tool_error",
        "tool_input_guardrail",
        "tool_output_guardrail",
        "handoff",
        "input_guardrail",
        "output_guardrail",
        "final_output",
        "run_stopped",
        "tool_approval_required",
        "verification_result",
        "verification_skipped",
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
    tool_input_guardrail_results: tuple[ToolInputGuardrailResult, ...] = ()
    tool_output_guardrail_results: tuple[ToolOutputGuardrailResult, ...] = ()
    raw_responses: tuple[ModelResponse, ...] = ()
    new_items: tuple[RunItem, ...] = ()


@dataclass(frozen=True)
class CodeExecutionResult:
    output: Any | None      # 结果
    logs: str               # print 结果
    is_final_answer: bool = False


def _argument_type_label(argument: ToolArgument) -> str:
    if argument.schema:
        return schema_type_label(argument.schema)
    return "any"


def render_tool_signature(tool_spec: ToolSpec) -> str:
    arguments = ", ".join(
        f"{argument.name}: {_argument_type_label(argument)}"
        for argument in tool_spec.arguments
    )
    return f"{tool_spec.name}({arguments}) -> {tool_spec.returns}"


def tool_to_prompt_text(tool_spec: ToolSpec) -> str:
    arguments_text = ", ".join(
        f"{argument.name}({_argument_type_label(argument)})"
        for argument in tool_spec.arguments
    )
    return (
        f"{tool_spec.name}: {tool_spec.description}\n"
        f"inputs: {arguments_text}\n"
        f"returns: {tool_spec.returns}"
    )
