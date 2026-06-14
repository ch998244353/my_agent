from __future__ import annotations

from dataclasses import asdict, dataclass
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


@dataclass(frozen=True)
class PendingApprovalSummary:
    tool_name: str
    call_id: str
    arguments: dict[str, Any]
    reason: str | None = None


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


def _tool_call_to_state(tool_call: ToolCall) -> dict[str, Any]:
    return {
        "tool_name": tool_call.tool_name,
        "arguments": dict(tool_call.arguments),
        "call_id": tool_call.call_id,
    }


def _tool_approval_request_to_state(request: ToolApprovalRequest) -> dict[str, Any]:
    return {
        "tool_name": request.tool_name,
        "call_id": request.call_id,
        "arguments": dict(request.arguments),
        "reason": request.reason,
    }


def _model_response_to_state(response: ModelResponse) -> dict[str, Any]:
    return {
        "response_id": response.response_id,
        "output": response.output,
        "output_text": response.output_text,
        "tool_calls": [_tool_call_to_state(tool_call) for tool_call in response.tool_calls],
        "refusal": response.refusal,
        "usage": response.usage,
        "request_summary": response.request_summary,
        "request_id": response.request_id,
    }


def _run_item_payload_to_state(payload: Any) -> Any:
    if isinstance(payload, ToolCall):
        return _tool_call_to_state(payload)
    if isinstance(payload, ToolApprovalRequest):
        return _tool_approval_request_to_state(payload)
    if isinstance(payload, ModelResponse):
        return _model_response_to_state(payload)
    if isinstance(payload, VerificationSummary):
        return asdict(payload)
    return payload


def _run_item_to_state(item: RunItem) -> dict[str, Any]:
    return {
        "item_type": item.item_type,
        "step_number": item.step_number,
        "payload": _run_item_payload_to_state(item.payload),
        "metadata": dict(item.metadata),
    }


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
    # 把一次 agent run 的最终输出、工具结果、审批状态统一暴露给 CLI 和调用方
    def pending_approvals(self) -> tuple[ToolApprovalRequest, ...]:
        return tuple(
            item.payload
            for item in self.new_items
            if item.item_type == "tool_approval_required"
            and isinstance(item.payload, ToolApprovalRequest)
            and self._approval_request_is_pending(item.payload)
        )

    def _approval_request_is_pending(self, request: ToolApprovalRequest) -> bool:
        if self.context_wrapper is None:
            return True
        status = self.context_wrapper.approval_status_for(
            request.tool_name,
            request.call_id,
        )
        return status in ("unknown", "pending")

    @property
    def pending_approval_summaries(self) -> tuple[PendingApprovalSummary, ...]:
        return tuple(
            PendingApprovalSummary(
                tool_name=request.tool_name,
                call_id=request.call_id,
                arguments=dict(request.arguments),
                reason=request.reason,
            )
            for request in self.pending_approvals
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
        from .run_state import RunStateSnapshot

        approvals = (
            self.context_wrapper.export_tool_approvals(self.pending_approvals)
            if self.context_wrapper is not None
            else ()
        )
        snapshot = RunStateSnapshot(
            input=self.input,
            last_agent_name=getattr(self.last_agent, "name", None),
            last_response_id=self.last_response_id,
            current_turn=self.current_turn,
            steps_taken=self.steps_taken,
            max_turns=self.max_turns,
            max_steps=self.max_steps,
            tool_approvals=approvals,
            model_responses=tuple(
                _model_response_to_state(response) for response in self.raw_responses
            ),
            new_items=tuple(_run_item_to_state(item) for item in self.new_items),
        )
        return asdict(snapshot)


@dataclass(frozen=True)
class RunResult(RunResultBase):
    pass
