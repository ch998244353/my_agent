from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, cast

from .contracts import ChatMessage, ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from .verification import VerificationResult

T = TypeVar("T")

# 把 ToolCall 转成稳定文本
def _render_tool_call(tool_call: ToolCall) -> str:
    arguments = ", ".join(
        f"{name}={value!r}"
        for name, value in tool_call.arguments.items()
    )
    return f"{tool_call.call_id}: {tool_call.tool_name}({arguments})"


@dataclass(frozen=True)
class VerificationSummary:
    attempts: int
    passed: bool
    skipped: int = 0
    last_observation: str | None = None


def _run_item_observation(item: RunItem) -> str | None:
    observation = item.metadata.get("observation")
    if isinstance(observation, str):
        return observation
    if isinstance(item.payload, str):
        return item.payload
    return None


def _verification_payload_passed(payload: Any) -> bool | None:
    if not isinstance(payload, tuple):
        return None
    if not all(isinstance(result, VerificationResult) for result in payload):
        return None
    return all(result.passed for result in payload)


def _verification_summary_from_items(
    items: tuple[RunItem, ...],
) -> VerificationSummary | None:
    attempts = 0
    skipped = 0
    passed: bool | None = None
    last_observation: str | None = None
    for item in items:
        if item.item_type == "verification_result":
            attempts += 1
            metadata_passed = item.metadata.get("passed")
            passed = (
                metadata_passed
                if isinstance(metadata_passed, bool)
                else _verification_payload_passed(item.payload)
            )
            last_observation = _run_item_observation(item) or last_observation
        elif item.item_type == "verification_skipped":
            skipped += 1
            last_observation = _run_item_observation(item) or last_observation

    if attempts == 0 and skipped == 0:
        return None
    return VerificationSummary(
        attempts=attempts,
        passed=bool(passed),
        skipped=skipped,
        last_observation=last_observation,
    )


if TYPE_CHECKING:
    from .agent import Agent
    from .guardrails import InputGuardrailResult, OutputGuardrailResult
    from .run_context import RunContextWrapper
    from .tool_guardrails import ToolInputGuardrailResult, ToolOutputGuardrailResult


@dataclass(frozen=True)
class RunResultBase:
    final_answer: Any | None
    step_results: list[Any]
    reached_final_answer: bool
    steps_taken: int
    input: Any | None = None
    last_agent: Agent | None = None
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

    @property
    # 把函数伪装成属性 : result.final_output = result.final_output()
    def final_output(self) -> Any | None:
        return self.final_answer

    @property
    def last_response_id(self) -> str | None:
        if not self.raw_responses:
            return None
        return self.raw_responses[-1].response_id

    @property
    def pending_approvals(self) -> tuple[ToolApprovalRequest, ...]:
        return tuple(
            item.payload
            for item in self.new_items
            if item.item_type == "tool_approval_required"
            and isinstance(item.payload, ToolApprovalRequest)
        )

    @property
    def has_pending_approvals(self) -> bool:
        return bool(self.pending_approvals)

    @property
    def verification_summary(self) -> VerificationSummary | None:
        return _verification_summary_from_items(self.new_items)


    # 可以选择是否严格检查 final answer 是否是要求的 type
    def final_output_as(
        self,
        cls: type[T], # type[T] 的作用是把参数 cls 和返回值 T 绑定起来, cls：你希望 final_output 是什么类型。
        raise_if_incorrect_type: bool = False,
    ) -> T:
        if raise_if_incorrect_type and not isinstance(self.final_output, cls):
            raise TypeError(f"Final output is not of type {cls.__name__}")
        return cast(T, self.final_output) # 把 final_output 作为 T 类型返回
    


    #  input、tool_call、tool_result、final_output 转成 ChatMessage             列表
    def to_input_list(self, mode: str = "preserve_all") -> list[ChatMessage]:
        messages: list[ChatMessage] = []
        if self.input is not None:
            messages.append(ChatMessage(role="user", content=str(self.input)))

        for item in self.new_items:
            if item.item_type == "tool_call" and isinstance(item.payload, ToolCall):
                messages.append(
                    ChatMessage(
                        role="tool_call",
                        content=_render_tool_call(item.payload),
                    )
                )
            elif item.item_type == "tool_result":
                messages.append(
                    ChatMessage(role="tool_response", content=str(item.payload))
                )
            elif item.item_type == "final_output":
                messages.append(
                    ChatMessage(role="assistant", content=str(item.payload))
                )

        return messages

    def to_state(self) -> dict[str, Any]:
        return {
            "input": self.input,
            "last_agent": self.last_agent,
            "last_response_id": self.last_response_id,
            "final_output": self.final_output,
            "reached_final_answer": self.reached_final_answer,
            "verification_summary": self.verification_summary,
            "new_items": self.new_items,
        }


@dataclass(frozen=True)
class RunResult(RunResultBase):
    pass
