from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from .guardrails import InputGuardrailResult, OutputGuardrailResult
from .result import RunResult
from .run_context import RunContextWrapper
from .tool_guardrails import ToolInputGuardrailResult, ToolOutputGuardrailResult

if TYPE_CHECKING:
    from .agent import Agent


MAX_TURNS_REACHED = "max_turns_reached"
MAX_STEPS_REACHED = "max_steps_reached"


@dataclass(frozen=True)
class ApprovalSnapshot:
    tool_name: str
    call_id: str
    arguments: dict[str, Any]
    status: Literal["pending", "approved", "rejected"]
    reason: str | None = None
    rejection_message: str | None = None


@dataclass(frozen=True)
class RunStateSnapshot:
    input: Any
    last_agent_name: str | None
    last_response_id: str | None
    current_turn: int
    steps_taken: int
    max_turns: int | None
    max_steps: int | None
    tool_approvals: tuple[ApprovalSnapshot, ...]
    model_responses: tuple[dict[str, Any], ...]
    new_items: tuple[dict[str, Any], ...]


def _approval_snapshot_from_state(
    approval: ApprovalSnapshot | Mapping[str, Any],
) -> ApprovalSnapshot:
    if isinstance(approval, ApprovalSnapshot):
        return approval
    status = approval["status"]
    if status not in ("pending", "approved", "rejected"):
        raise ValueError(f"Unknown approval status: {status!r}")
    return ApprovalSnapshot(
        tool_name=str(approval["tool_name"]),
        call_id=str(approval["call_id"]),
        arguments=dict(approval.get("arguments") or {}),
        status=status,
        reason=approval.get("reason"),
        rejection_message=approval.get("rejection_message"),
    )


def _snapshot_from_state(
    snapshot: RunStateSnapshot | Mapping[str, Any],
) -> RunStateSnapshot:
    if isinstance(snapshot, RunStateSnapshot):
        return snapshot
    return RunStateSnapshot(
        input=snapshot.get("input"),
        last_agent_name=snapshot.get("last_agent_name"),
        last_response_id=snapshot.get("last_response_id"),
        current_turn=int(snapshot.get("current_turn", 0)),
        steps_taken=int(snapshot.get("steps_taken", 0)),
        max_turns=snapshot.get("max_turns"),
        max_steps=snapshot.get("max_steps"),
        tool_approvals=tuple(
            _approval_snapshot_from_state(approval)
            for approval in snapshot.get("tool_approvals", ())
        ),
        model_responses=tuple(
            dict(response) for response in snapshot.get("model_responses", ())
        ),
        new_items=tuple(dict(item) for item in snapshot.get("new_items", ())),
    )


#agent 因 工具调用请求 暂停后保存状态，恢复时把 JSON dict 还原成审批请求对象，避免 pending 信息丢失
def _tool_approval_request_from_state(
    payload: Any,
    approvals_by_call_id: Mapping[str, ApprovalSnapshot] | None = None,
) -> ToolApprovalRequest | Any:
    if isinstance(payload, ToolApprovalRequest):
        return payload
    if not isinstance(payload, Mapping):
        return payload
    if "tool_name" not in payload and "call_id" in payload:
        approval = (approvals_by_call_id or {}).get(str(payload["call_id"]))
        if approval is not None:
            return ToolApprovalRequest(
                tool_name=approval.tool_name,
                call_id=approval.call_id,
                arguments=dict(approval.arguments),
                reason=approval.reason,
            )
    return ToolApprovalRequest(
        tool_name=str(payload["tool_name"]),
        call_id=str(payload["call_id"]),
        arguments=dict(payload.get("arguments") or {}),
        reason=payload.get("reason"),
    )

# 。入参是 item 类型和原始 payload，返回恢复后的 payload。现在只特殊处理审批请求，其它 run item 保持原样，避免扩大本节改动范围
def _run_item_payload_from_snapshot(
    item_type: str,
    payload: Any,
    approvals_by_call_id: Mapping[str, ApprovalSnapshot] | None = None,
) -> Any:
    if item_type == "tool_approval_required":
        return _tool_approval_request_from_state(payload, approvals_by_call_id)
    return payload


def _run_items_from_snapshot(
    items: tuple[dict[str, Any], ...],
    approvals: tuple[ApprovalSnapshot, ...] = (),
) -> list[RunItem]:
    approvals_by_call_id = {
        approval.call_id: approval
        for approval in approvals
    }
    return [
        RunItem(
            item_type=item["item_type"],
            step_number=int(item["step_number"]),
            payload=_run_item_payload_from_snapshot(
                item["item_type"],
                item.get("payload"),
                approvals_by_call_id,
            ),
            metadata=dict(item.get("metadata") or {}),
        )
        for item in items
    ]


def _pending_tool_calls_from_approvals(
    approvals: tuple[ApprovalSnapshot, ...],
) -> tuple[ToolCall, ...]:
    return tuple(
        ToolCall(
            approval.tool_name,
            dict(approval.arguments),
            approval.call_id,
        )
        for approval in approvals
    )


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
    pending_tool_calls: tuple[ToolCall, ...] = ()
    input_guardrail_results: list[InputGuardrailResult] = field(default_factory=list)
    output_guardrail_results: list[OutputGuardrailResult] = field(default_factory=list)
    tool_input_guardrail_results: list[ToolInputGuardrailResult] = field(default_factory=list)
    tool_output_guardrail_results: list[ToolOutputGuardrailResult] = field(default_factory=list)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: RunStateSnapshot | Mapping[str, Any],
        *,
        agent: Agent | None = None,
        context_wrapper: RunContextWrapper | None = None,
    ) -> RunState:
        restored_snapshot = _snapshot_from_state(snapshot)
        context = context_wrapper or RunContextWrapper()
        context.import_tool_approvals(restored_snapshot.tool_approvals)
        return cls(
            new_items=_run_items_from_snapshot(
                restored_snapshot.new_items,
                restored_snapshot.tool_approvals,
            ),
            input=restored_snapshot.input,
            last_agent=agent,
            current_turn=restored_snapshot.current_turn,
            max_turns=restored_snapshot.max_turns,
            steps_taken=restored_snapshot.steps_taken,
            max_steps=restored_snapshot.max_steps,
            context_wrapper=context,
            pending_tool_calls=_pending_tool_calls_from_approvals(
                restored_snapshot.tool_approvals
            ),
        )

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
