from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from .contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from .guardrails import InputGuardrailResult, OutputGuardrailResult
from .model_retry import ModelAttemptRecord, ModelExecution, ModelExecutionError
from .models import ModelErrorKind
from .result import RunResult
from .run_context import RunContextWrapper
from .tool_guardrails import ToolInputGuardrailResult, ToolOutputGuardrailResult

if TYPE_CHECKING:
    from .agent import Agent


MAX_TURNS_REACHED = "max_turns_reached"
MAX_STEPS_REACHED = "max_steps_reached"
RUN_STATE_SNAPSHOT_SCHEMA_VERSION = 4


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
    model_attempts: tuple[dict[str, Any], ...] = ()
    last_model_terminal_reason: str | None = None
    task_id: str | None = None
    task_revision: int | None = None
    model_state: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _validate_task_link(self.task_id, self.task_revision)


# 导出当前版本快照
def run_state_snapshot_to_dict(snapshot: RunStateSnapshot) -> dict[str, Any]:
    payload = asdict(snapshot)
    payload["schema_version"] = RUN_STATE_SNAPSHOT_SCHEMA_VERSION
    return payload


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


def _model_attempt_to_state(record: ModelAttemptRecord) -> dict[str, Any]:
    return {
        "request_id": record.request_id,
        "attempt_id": record.attempt_id,
        "attempt_number": record.attempt_number,
        "model_name": record.model_name,
        "mode": record.mode,
        "outcome": record.outcome,
        "duration_seconds": record.duration_seconds,
        "error_kind": (
            record.error_kind.value if record.error_kind is not None else None
        ),
        "status_code": record.status_code,
        "retry_delay_seconds": record.retry_delay_seconds,
        "reason": record.reason,
        "response_id": record.response_id,
        "usage": dict(record.usage) if record.usage is not None else None,
        "ttft_seconds": record.ttft_seconds,
    }


def _model_attempt_from_state(data: Mapping[str, Any]) -> ModelAttemptRecord:
    error_kind = data.get("error_kind")
    return ModelAttemptRecord(
        request_id=data["request_id"],
        attempt_id=data["attempt_id"],
        attempt_number=data["attempt_number"],
        model_name=data["model_name"],
        mode=data["mode"],
        outcome=data["outcome"],
        duration_seconds=data["duration_seconds"],
        error_kind=ModelErrorKind(error_kind) if error_kind is not None else None,
        status_code=data.get("status_code"),
        retry_delay_seconds=data.get("retry_delay_seconds"),
        reason=data.get("reason"),
        response_id=data.get("response_id"),
        usage=data.get("usage"),
        ttft_seconds=data.get("ttft_seconds"),
    )


def _snapshot_from_state(
    snapshot: RunStateSnapshot | Mapping[str, Any],
) -> RunStateSnapshot:
    if isinstance(snapshot, RunStateSnapshot):
        return snapshot
    schema_version = snapshot.get("schema_version")
    has_model_attempt_fields = schema_version in (2, 3, 4)
    has_task_link_fields = schema_version in (3, 4)
    model_state = snapshot.get("model_state") if schema_version == 4 else None
    if model_state is not None and not isinstance(model_state, Mapping):
        raise TypeError("model_state must be a mapping or null")
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
        model_attempts=(
            tuple(
                _model_attempt_to_state(_model_attempt_from_state(attempt))
                for attempt in snapshot.get("model_attempts", ())
            )
            if has_model_attempt_fields
            else ()
        ),
        last_model_terminal_reason=(
            snapshot.get("last_model_terminal_reason")
            if has_model_attempt_fields
            else None
        ),
        task_id=snapshot.get("task_id") if has_task_link_fields else None,
        task_revision=(
            snapshot.get("task_revision") if has_task_link_fields else None
        ),
        model_state=dict(model_state) if model_state is not None else None,
    )


def run_state_snapshot_from_dict(data: Mapping[str, Any]) -> RunStateSnapshot:
    schema_version = data.get("schema_version")
    if schema_version is not None and (
        type(schema_version) is not int or schema_version not in (1, 2, 3, 4)
    ):
        raise ValueError(
            "Unsupported run state snapshot schema version: "
            f"{schema_version!r}"
        )
    return _snapshot_from_state(data)


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


def _paused_turn_tool_calls(
    snapshot: RunStateSnapshot,
) -> tuple[ToolCall, ...]:
    approval_calls = _pending_tool_calls_from_approvals(snapshot.tool_approvals)
    if not approval_calls or not snapshot.model_responses:
        return approval_calls

    raw_calls = snapshot.model_responses[-1].get("tool_calls")
    if not isinstance(raw_calls, list) or not raw_calls:
        return approval_calls
    try:
        turn_calls = tuple(
            ToolCall(
                tool_name=raw_call["tool_name"],
                arguments=dict(raw_call.get("arguments") or {}),
                call_id=raw_call["call_id"],
            )
            for raw_call in raw_calls
            if isinstance(raw_call, Mapping)
        )
    except (KeyError, TypeError, ValueError):
        return approval_calls

    approval_keys = {
        (approval.tool_name, approval.call_id)
        for approval in snapshot.tool_approvals
    }
    turn_keys = {(call.tool_name, call.call_id) for call in turn_calls}
    return turn_calls if approval_keys <= turn_keys else approval_calls


def _validate_task_link(
    task_id: str | None,
    task_revision: int | None,
) -> None:
    if (task_id is None) != (task_revision is None):
        raise ValueError("task_id and task_revision must be provided together.")
    if task_id is None:
        return
    if not isinstance(task_id, str):
        raise TypeError("task_id must be a string.")
    if not task_id.strip():
        raise ValueError("task_id must be a non-empty string.")
    if isinstance(task_revision, bool) or not isinstance(task_revision, int):
        raise TypeError("task_revision must be an integer.")
    if task_revision < 1:
        raise ValueError("task_revision must be a positive integer.")


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
    model_attempts: list[ModelAttemptRecord] = field(default_factory=list)
    last_model_terminal_reason: str | None = None
    task_id: str | None = None
    task_revision: int | None = None
    handoff_depth: int = 0
    context_wrapper: RunContextWrapper = field(default_factory=RunContextWrapper)
    pending_tool_calls: tuple[ToolCall, ...] = ()
    input_guardrail_results: list[InputGuardrailResult] = field(default_factory=list)
    output_guardrail_results: list[OutputGuardrailResult] = field(default_factory=list)
    tool_input_guardrail_results: list[ToolInputGuardrailResult] = field(default_factory=list)
    tool_output_guardrail_results: list[ToolOutputGuardrailResult] = field(default_factory=list)

    def __post_init__(self) -> None:
        _validate_task_link(self.task_id, self.task_revision)

    @classmethod
    def from_snapshot(
        cls,
        snapshot: RunStateSnapshot | Mapping[str, Any],
        *,
        agent: Agent | None = None,
        context_wrapper: RunContextWrapper | None = None,
    ) -> RunState:
        restored_snapshot = (
            snapshot
            if isinstance(snapshot, RunStateSnapshot)
            else run_state_snapshot_from_dict(snapshot)
        )
        context = context_wrapper or RunContextWrapper()
        context.import_tool_approvals(restored_snapshot.tool_approvals)
        if agent is not None and restored_snapshot.model_state is not None:
            restore_state = getattr(agent.model, "restore_state", None)
            if not callable(restore_state):
                raise ValueError("agent model cannot restore saved model state")
            restore_state(restored_snapshot.model_state)
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
            model_attempts=[
                _model_attempt_from_state(attempt)
                for attempt in restored_snapshot.model_attempts
            ],
            last_model_terminal_reason=restored_snapshot.last_model_terminal_reason,
            task_id=restored_snapshot.task_id,
            task_revision=restored_snapshot.task_revision,
            context_wrapper=context,
            pending_tool_calls=_paused_turn_tool_calls(restored_snapshot),
        )

    def approve_tool_call(self, tool_name: str, call_id: str) -> None:
        self.context_wrapper.approve_tool_call(tool_name, call_id)

    def reject_tool_call(
        self,
        tool_name: str,
        call_id: str,
        rejection_message: str | None = None,
    ) -> None:
        self.context_wrapper.reject_tool_call(
            tool_name,
            call_id,
            rejection_message,
        )

    # 运行时判断 和 递增方法
    def can_call_model(self) -> bool:
        return self.model_limit_reason() is None

    def record_model_turn(self) -> None:
        self.current_turn += 1

    def record_model_execution(self, execution: ModelExecution) -> None:
        if not isinstance(execution, ModelExecution):
            raise TypeError("execution must be a ModelExecution.")
        self.model_attempts.extend(execution.attempts)
        self.last_model_terminal_reason = "completed"

    # 保存所有失败发送，并使用稳定错误类别作为终止原因
    def record_model_execution_error(self, error: ModelExecutionError) -> None:
        if not isinstance(error, ModelExecutionError):
            raise TypeError("error must be a ModelExecutionError.")
        self.model_attempts.extend(error.attempts)
        kind = error.last_error.kind
        self.last_model_terminal_reason = kind.value if kind is not None else "unknown"

    def record_model_cancelled(
        self,
        attempts: tuple[ModelAttemptRecord, ...] = (),
    ) -> None:
        attempt_values = tuple(attempts)
        if not all(
            isinstance(attempt, ModelAttemptRecord) for attempt in attempt_values
        ):
            raise TypeError("attempts must contain only ModelAttemptRecord values.")
        self.model_attempts.extend(attempt_values)
        self.last_model_terminal_reason = "cancelled"

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
        model_attempts=tuple(run_state.model_attempts),
        last_model_terminal_reason=run_state.last_model_terminal_reason,
        task_id=run_state.task_id,
        task_revision=run_state.task_revision,
    )
