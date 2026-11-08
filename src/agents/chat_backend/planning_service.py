from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Literal
from uuid import UUID

from ..approval_summaries import approval_summary_for_tool_call
from ..coding_agent import CodingAgentSetup
from ..environment import LocalEnvironment
from ..events import RunEvent
from ..planning import (
    PLAN_DEFINITION_SCHEMA_VERSION,
    PlanDefinition,
    PlanExecution,
    PlanStepSpec,
    build_plan_step_agent,
    build_planning_agent,
    plan_definition_from_dict,
    render_plan_markdown,
)
from ..result import RunResult
from ..run_context import RunContextWrapper
from ..run_state import RunState, run_state_snapshot_from_dict
from ..runner import Runner
from ..task_state import TaskNode, TaskStatus
from ..tool_runtime import clip_tool_text
from ..verification import VerificationPolicy, VerificationRunner
from ..workspace_manifest import WorkspaceManifest
from .conversation import (
    ConversationArchivedError,
    ConversationNotFoundError,
    MessageStore,
)
from .model_profiles import ModelProfile, ModelProfileStore, model_for_profile
from .planning_store import (
    ActivePlanningRunError,
    CorruptPlanningRunError,
    InvalidPlanningTransition,
    PlanningConflictError,
    PlanningRun,
    PlanningRunNotFoundError,
    PlanningStatus,
    PlanStore,
)
from .postgres_session import CommitOnSuccessSession, PostgresSession


__all__ = (
    "PlanningApprovalSnapshot",
    "PlanningDraftOutcome",
    "PlanningInputError",
    "PlanningProgressEvent",
    "PlanningRecoveryReport",
    "PlanningRunError",
    "PlanningService",
    "PlanningSnapshot",
    "PlanningStepSnapshot",
)


class PlanningInputError(ValueError):
    pass


class PlanningRunError(RuntimeError):
    pass


_SAFE_RUN_ERRORS = {
    "model_step_failed": "planning model step failed",
    "tool_step_failed": "planning tool step failed",
    "verification_failed": "planning verification failed",
    "verification_unavailable": "planning verification could not run",
    "recovery_failed": "planning recovery failed",
}

_MAX_STEP_RESULT_LENGTH = 20_000
_MAX_REJECTION_MESSAGE_LENGTH = 2_000
_MAX_CONTROL_TEXT_LENGTH = 20_000


def _safe_run_error(
    code: Literal[
        "model_step_failed",
        "tool_step_failed",
        "verification_failed",
        "verification_unavailable",
        "recovery_failed",
    ],
) -> str:
    try:
        return _SAFE_RUN_ERRORS[code]
    except (KeyError, TypeError):
        raise PlanningInputError(
            "unsupported planning run error code"
        ) from None


def _public_run_error(value: str | None) -> str | None:
    if value is None:
        return None
    if value in _SAFE_RUN_ERRORS.values():
        return value
    return "planning run could not continue"


def _require_control_request(
    run_id: UUID,
    expected_row_revision: int,
) -> None:
    if not isinstance(run_id, UUID):
        raise PlanningInputError("run_id must be a UUID")
    if (
        type(expected_row_revision) is not int
        or expected_row_revision <= 0
    ):
        raise PlanningInputError(
            "expected_row_revision must be a positive integer"
        )


def _require_control_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanningInputError(f"{name} must be non-empty text")
    normalized = value.strip()
    if len(normalized) > _MAX_CONTROL_TEXT_LENGTH:
        raise PlanningInputError(f"{name} must not exceed 20000 characters")
    return normalized


# 把一个 PlanStepSpec 和它直接依赖的结果转成单次 Runner 输入
def _step_prompt(step: PlanStepSpec, execution: PlanExecution) -> str:
    dependency_context = ""
    if step.dependency_ids:
        result_limit = max(1, _MAX_STEP_RESULT_LENGTH // len(step.dependency_ids))
        dependency_results: list[str] = []
        for dependency_id in step.dependency_ids:
            dependency = execution.task_store.get(dependency_id)
            if (
                dependency.status is not TaskStatus.COMPLETED
                or not isinstance(dependency.result, str)
                or not dependency.result.strip()
            ):
                raise PlanningRunError("planning dependency result is invalid")
            dependency_results.append(
                f"[{dependency_id}]\n"
                f"{clip_tool_text(dependency.result.strip(), result_limit)}"
            )
        dependency_context = (
            "\n\nCompleted direct dependency results:\n"
            + "\n\n".join(dependency_results)
        )
    return (
        f"Current plan step: {step.step_id}\n\n"
        f"Instruction:\n{step.instruction}\n\n"
        f"Completion criteria:\n{step.completion_criteria}\n\n"
        f"{dependency_context}\n\n"
        f"Execute only step {step.step_id}. Do not start another plan step."
    )


def _frozen_test_command(run: PlanningRun) -> str:
    try:
        command = run.workspace_snapshot["default_test_command"]
    except Exception as exc:
        raise PlanningRunError(
            "planning verification command is invalid"
        ) from exc
    if not isinstance(command, str) or not command.strip():
        raise PlanningRunError("planning verification command is invalid")
    return command


PLANNING_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {
            "type": "string",
            "enum": ["question", "plan"],
        },
        "question": {
            "type": ["string", "null"],
            "minLength": 1,
        },
        "definition": {
            "type": ["object", "null"],
            "properties": {
                "schema_version": {
                    "type": "integer",
                    "enum": [PLAN_DEFINITION_SCHEMA_VERSION],
                },
                "objective": {"type": "string", "minLength": 1},
                "steps": {
                    "type": "array",
                    "minItems": 2,
                    "maxItems": 12,
                    "items": {
                        "type": "object",
                        "properties": {
                            "step_id": {"type": "string", "minLength": 1},
                            "title": {"type": "string", "minLength": 1},
                            "instruction": {"type": "string", "minLength": 1},
                            "completion_criteria": {
                                "type": "string",
                                "minLength": 1,
                            },
                            "dependency_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                            "is_final_verification": {"type": "boolean"},
                        },
                        "required": [
                            "step_id",
                            "title",
                            "instruction",
                            "completion_criteria",
                            "dependency_ids",
                            "is_final_verification",
                        ],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["schema_version", "objective", "steps"],
            "additionalProperties": False,
        },
    },
    "required": ["kind", "question", "definition"],
    "additionalProperties": False,
}


# 调用者查看计划时真正拿到的数据
@dataclass(frozen=True)
class PlanningStepSnapshot:
    step_id: str
    title: str
    instruction: str
    completion_criteria: str
    dependency_ids: tuple[str, ...]
    is_final_verification: bool
    status: TaskStatus
    blocked_by: tuple[str, ...]
    result: str | None
    error: str | None


@dataclass(frozen=True)
class PlanningApprovalSnapshot:
    tool_name: str
    call_id: str
    summary: str
    reason: str | None


@dataclass(frozen=True)
class PlanningSnapshot:
    run_id: UUID  # 计划编号
    conversation_id: UUID
    version: int  # 版本号
    row_revision: int  # 修订号
    status: PlanningStatus  # 状态
    objective: str  # 目标
    plan_markdown: str | None
    current_step_id: str | None  #正在执行步骤
    completed_step_ids: tuple[str, ...]  # 已完成步骤
    pending_approval_keys: tuple[tuple[str, str], ...]
    steps: tuple[PlanningStepSnapshot, ...]
    pending_approvals: tuple[PlanningApprovalSnapshot, ...]
    allowed_actions: tuple[str, ...]
    last_error: str | None


# 计划草稿结果状态
@dataclass(frozen=True)
class PlanningDraftOutcome:
    kind: Literal["question", "plan"]  # 信息不足 / 已生成计划
    content: str
    snapshot: PlanningSnapshot
    message_id: UUID


@dataclass(frozen=True)
class PlanningRecoveryReport:
    scanned: int  # 扫描了多少条运行中计划
    recovered_run_ids: tuple[UUID, ...]  # 哪些计划恢复成功
    isolated_run_ids: tuple[UUID, ...]  # 哪些损坏记录被隔离
    deferred_run_ids: tuple[UUID, ...]  # 哪些计划暂时没有恢复


@dataclass(frozen=True)
class PlanningProgressEvent:
    planning_run_id: UUID
    task_id: str | None
    event: RunEvent


def _parse_draft_output(
    value: object,
) -> tuple[Literal["question", "plan"], str | PlanDefinition]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("draft output must be valid JSON") from exc
    if not isinstance(value, Mapping):
        raise TypeError("draft output must be a mapping")
    if frozenset(value) != frozenset({"kind", "question", "definition"}):
        raise ValueError("draft output fields do not match")

    kind = value["kind"]
    question = value["question"]
    definition = value["definition"]
    if kind == "question":
        if not isinstance(question, str) or not question.strip():
            raise ValueError("draft question must be non-empty text")
        if definition is not None:
            raise ValueError("question draft cannot include a definition")
        return "question", question.strip()
    if kind == "plan":
        if question is not None:
            raise ValueError("plan draft cannot include a question")
        if not isinstance(definition, Mapping):
            raise TypeError("plan draft definition must be a mapping")
        return "plan", plan_definition_from_dict(definition)
    raise ValueError("draft kind is invalid")



# 、把持久化计划转换成安全快照
def _snapshot_from_run(run: PlanningRun) -> PlanningSnapshot:
    current_step_id = None
    completed_step_ids: tuple[str, ...] = ()
    nodes: dict[str, TaskNode] = {}
    public_run_error = _public_run_error(run.last_error)
    if run.definition is not None and run.task_events:
        execution = PlanExecution.from_events(
            run.definition,
            run.task_events,
            owner_id=str(run.id),
        )
        nodes = {
            node.task_id: node
            for node in execution.task_store.list()
        }
        running_ids = tuple(
            step.step_id
            for step in run.definition.steps
            if nodes[step.step_id].status is TaskStatus.RUNNING
        )
        if running_ids:
            current_step_id = running_ids[0]
        completed_step_ids = tuple(
            step.step_id
            for step in run.definition.steps
            if nodes[step.step_id].status is TaskStatus.COMPLETED
        )

    steps = (
        ()
        if run.definition is None
        else tuple(
            PlanningStepSnapshot(
                step_id=step.step_id,
                title=step.title,
                instruction=step.instruction,
                completion_criteria=step.completion_criteria,
                dependency_ids=step.dependency_ids,
                is_final_verification=step.is_final_verification,
                status=(
                    nodes[step.step_id].status
                    if step.step_id in nodes
                    else TaskStatus.PENDING
                ),
                blocked_by=(
                    nodes[step.step_id].blocked_by
                    if step.step_id in nodes
                    else ()
                ),
                result=(
                    nodes[step.step_id].result
                    if step.step_id in nodes
                    and isinstance(nodes[step.step_id].result, str)
                    else None
                ),
                error=(
                    nodes[step.step_id].error
                    if step.step_id in nodes
                    and nodes[step.step_id].error is not None
                    else (
                        public_run_error
                        if run.status == "paused"
                        and step.step_id in nodes
                        and nodes[step.step_id].status is TaskStatus.RUNNING
                        else None
                    )
                ),
            )
            for step in run.definition.steps
        )
    )

    pending_approvals = (
        ()
        if run.run_state_snapshot is None
        else tuple(
            PlanningApprovalSnapshot(
                tool_name=approval.tool_name,
                call_id=approval.call_id,
                summary=approval_summary_for_tool_call(
                    approval.tool_name,
                    approval.call_id,
                    approval.arguments,
                    approval.reason,
                ),
                reason=approval.reason,
            )
            for approval in run.run_state_snapshot.tool_approvals
            if approval.status == "pending"
        )
    )
    return PlanningSnapshot(
        run_id=run.id,
        conversation_id=run.conversation_id,
        version=run.version,
        row_revision=run.row_revision,
        status=run.status,
        objective=run.objective,
        plan_markdown=(
            render_plan_markdown(run.definition)
            if run.definition is not None
            else None
        ),
        current_step_id=current_step_id,
        completed_step_ids=completed_step_ids,
        pending_approval_keys=tuple(
            (approval.tool_name, approval.call_id)
            for approval in pending_approvals
        ),
        steps=steps,
        pending_approvals=pending_approvals,
        allowed_actions={
            "drafting": ("draft", "cancel"),
            "awaiting_approval": ("approve", "revise", "cancel"),
            "running": ("cancel",),
            "awaiting_tool_approval": ("decide_tool_approval", "cancel"),
            "paused": ("retry_current", "revise", "cancel"),
        }.get(run.status, ()),
        last_error=public_run_error,
    )


#  负责把“生成 Plan 草案”整个业务流程串起来：读取计划状态 → 获取模型和工作区配置 → 调用 Planning Agent → 校验模型输出 → 保存 Plan 和对话消息
class PlanningService:
    def __init__(
        self,
        plans: PlanStore,  # plan的数据库操作服务类
        messages: MessageStore,  # conversation 服务类
        profiles: ModelProfileStore,  # 根据 profile id 获取当前模型配置
        manifest_provider: Callable[[], WorkspaceManifest],  # 在真正需要时读取当前工作区 manifest
    ) -> None:
        self._plans = plans
        self._messages = messages
        self._profiles = profiles
        self._manifest_provider = manifest_provider


    # 获取当前正在使用的模型配置，如果没配置或读取失败就抛出统一的 Planning 错误
    def _active_profile(self) -> ModelProfile:
        try:
            profile = self._profiles.active()
        except Exception as exc:
            raise PlanningRunError(
                "planning model profile could not be loaded"
            ) from exc
        if profile is None:
            raise PlanningRunError("planning model profile is not configured")
        return profile

    def _current_manifest(self) -> WorkspaceManifest:
        try:
            manifest = self._manifest_provider()
        except Exception as exc:
            raise PlanningRunError("planning workspace could not be loaded") from exc
        if not isinstance(manifest, WorkspaceManifest):
            raise PlanningRunError("planning workspace could not be loaded")
        return manifest


    # 检查保存的 profile 和给的是否一致 , 从冻结后的 PlanningRun 恢复  profile + model + manifest
    def _resolve_frozen_resources(
        self,
        run: PlanningRun,
    ) -> tuple[ModelProfile, object, WorkspaceManifest]:
        try:
            profile_id = UUID(str(run.model_snapshot["id"]))
            profile = self._profiles.get(profile_id)
            current_profile = profile.public_dict()
        except Exception as exc:
            raise PlanningRunError(
                "planning model profile could not be loaded"
            ) from exc
        if any(
            current_profile[field] != run.model_snapshot.get(field)
            for field in (
                "id",
                "provider",
                "protocol",
                "base_url",
                "model",
                "has_api_key",
            )
        ):
            raise PlanningRunError("planning model profile changed")

        manifest = self._resolve_frozen_manifest(run)
        try:
            model = model_for_profile(profile)
        except Exception as exc:
            raise PlanningRunError("planning agent could not be created") from exc
        return profile, model, manifest

    def _resolve_frozen_manifest(self, run: PlanningRun) -> WorkspaceManifest:
        manifest = self._current_manifest()
        try:
            current_workspace = manifest.metadata()
        except Exception as exc:
            raise PlanningRunError(
                "planning workspace could not be loaded"
            ) from exc
        if current_workspace != dict(run.workspace_snapshot):
            raise PlanningRunError("planning workspace changed")
        return manifest


    # 执行一次完整的 Plan 草案生成流程
    async def draft(
        self,
        conversation_id: UUID,
        content: str,
        expected_row_revision: int | None = None,
        on_event: Callable[[PlanningProgressEvent], None] | None = None,
    ) -> PlanningDraftOutcome:
        if not isinstance(conversation_id, UUID):
            raise PlanningInputError("conversation_id must be a UUID")
        if not isinstance(content, str) or not content.strip():
            raise PlanningInputError("content must be non-empty text")
        content = content.strip()
        if len(content) > 20_000:
            raise PlanningInputError("content must not exceed 20000 characters")
        if expected_row_revision is not None and (
            type(expected_row_revision) is not int or expected_row_revision <= 0
        ):
            raise PlanningInputError(
                "expected_row_revision must be a positive integer"
            )

        try:
            run = await asyncio.to_thread(
                self._plans.get_active,
                conversation_id,
            )
        except Exception as exc:
            raise PlanningRunError("planning data could not be read") from exc

        model: object | None = None
        if run is None:
            if expected_row_revision is not None:
                raise PlanningInputError(
                    "expected_row_revision requires an active draft"
                )
            profile = await asyncio.to_thread(self._active_profile)
            manifest = await asyncio.to_thread(self._current_manifest)
            try:
                run = await asyncio.to_thread(
                    self._plans.create_draft,
                    conversation_id,
                    content,
                    model_profile=profile,
                    manifest=manifest,
                )
            except (
                ActivePlanningRunError,
                ConversationArchivedError,
                ConversationNotFoundError,
            ):
                raise
            except Exception as exc:
                raise PlanningRunError(
                    "planning draft could not be created"
                ) from exc
        else:
            if run.status != "drafting":
                raise InvalidPlanningTransition(run.id, run.status, "draft")
            if (
                expected_row_revision is not None
                and expected_row_revision != run.row_revision
            ):
                raise PlanningConflictError(
                    run.id,
                    expected_row_revision,
                    run.row_revision,
                )
            profile, model, manifest = await asyncio.to_thread(
                self._resolve_frozen_resources,
                run,
            )

        try:
            # 创建 Planning Agent
            if model is None:
                model = await asyncio.to_thread(model_for_profile, profile)
            setup = await asyncio.to_thread(
                build_planning_agent,
                model=model,
                manifest=manifest,
                output_type=PLANNING_DRAFT_SCHEMA,
            )
            setup = replace(
                setup,
                agent=setup.agent.clone(
                    instructions=(
                        f"{setup.agent.instructions or ''}\n\n"
                        "The authoritative planning objective is:\n"
                        f"{run.objective}\n\n"
                        "Treat the latest user message as clarification or retry "
                        "instructions; never replace the objective with an unrelated task."
                    )
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning agent could not be created") from exc


        persistent = PostgresSession(conversation_id, self._messages)
        session = CommitOnSuccessSession(persistent, self._messages)
        config = replace(setup.run_config, session=session)
        try:
            if on_event is None:
                result = await Runner.run_async(setup.agent, content, config)
            else:
                stream = Runner.run_stream(setup.agent, content, config)
                async with stream:
                    try:
                        async for event in stream:
                            on_event(PlanningProgressEvent(run.id, None, event))
                        result = await stream.result()
                    except asyncio.CancelledError:
                        stream.controller.cancel("planning_cancelled")
                        raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning run failed") from exc

        if not result.reached_final_answer:
            raise PlanningRunError("planning run did not produce a draft")
        try:
            kind, parsed = _parse_draft_output(result.final_output)
        except (TypeError, ValueError) as exc:
            raise PlanningRunError(
                "planning model returned an invalid draft"
            ) from exc

        if kind == "question":
            content_to_store = str(parsed)
            latest = run
        else:
            if not isinstance(parsed, PlanDefinition):
                raise PlanningRunError(
                    "planning model returned an invalid draft"
                )
            content_to_store = render_plan_markdown(parsed)
            try:
                latest = await asyncio.to_thread(
                    self._plans.submit_for_approval,
                    run.id,
                    run.row_revision,
                    parsed,
                )
            except (InvalidPlanningTransition, PlanningConflictError):
                raise
            except Exception as exc:
                raise PlanningRunError("planning plan could not be saved") from exc

        try:
            stored = await asyncio.to_thread(
                session.commit,
                str(result.final_output),
                stored_answer=content_to_store,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise PlanningRunError(
                "planning messages could not be saved"
            ) from exc
        if (
            not stored
            or stored[-1].role != "assistant"
            or stored[-1].content != content_to_store
        ):
            raise PlanningRunError("planning messages could not be saved")
        return PlanningDraftOutcome(
            kind,
            content_to_store,
            _snapshot_from_run(latest),
            stored[-1].id,
        )


    # 批准后保存数据库状态 , 并开启真实运行
    async def approve(
        self,
        run_id: UUID,
        expected_row_revision: int,
        on_event: Callable[[PlanningProgressEvent], None] | None = None,
    ) -> PlanningSnapshot:
        try:
            run = await asyncio.to_thread(
                self._plans.approve,
                run_id,
                expected_row_revision,
            )
            return await self._drive_running(run, on_event)
        except asyncio.CancelledError:
            raise
        except (
            InvalidPlanningTransition,
            PlanningConflictError,
            PlanningRunNotFoundError,
        ):
            raise
        except PlanningRunError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning execution failed") from exc

    async def decide_tool_approval(
        self,
        run_id: UUID,
        expected_row_revision: int,
        tool_name: str,
        call_id: str,
        decision: str,
        rejection_message: str | None = None,
        on_event: Callable[[PlanningProgressEvent], None] | None = None,
    ) -> PlanningSnapshot:
        _require_control_request(run_id, expected_row_revision)
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise PlanningInputError("tool_name must be non-empty text")
        if not isinstance(call_id, str) or not call_id.strip():
            raise PlanningInputError("call_id must be non-empty text")
        if decision not in ("approve", "reject"):
            raise PlanningInputError("decision must be approve or reject")

        tool_name = tool_name.strip()
        call_id = call_id.strip()
        normalized_rejection = None
        if decision == "reject":
            if (
                not isinstance(rejection_message, str)
                or not rejection_message.strip()
            ):
                raise PlanningInputError(
                    "reject requires a non-empty rejection_message"
                )
            normalized_rejection = rejection_message.strip()
            if len(normalized_rejection) > _MAX_REJECTION_MESSAGE_LENGTH:
                raise PlanningInputError(
                    "rejection_message must not exceed 2000 characters"
                )
        elif rejection_message is not None:
            raise PlanningInputError(
                "approve cannot include rejection_message"
            )

        try:
            run = await asyncio.to_thread(self._plans.get, run_id)
        except PlanningRunNotFoundError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning data could not be read") from exc
        if run.status != "awaiting_tool_approval":
            raise InvalidPlanningTransition(
                run.id,
                run.status,
                "decide_tool_approval",
                "running",
            )
        if run.row_revision != expected_row_revision:
            raise PlanningConflictError(
                run.id,
                expected_row_revision,
                run.row_revision,
            )
        if run.definition is None:
            raise PlanningRunError(
                "planning approval state could not be restored"
            )
        try:
            execution = PlanExecution.from_events(
                run.definition,
                run.task_events,
                owner_id=str(run.id),
            )
        except (TypeError, ValueError) as exc:
            raise PlanningRunError(
                "planning approval state could not be restored"
            ) from exc

        try:
            setup, run_state, node = await asyncio.to_thread(
                self._restore_approval_run,
                run,
                execution,
            )
        except PlanningRunError:
            try:
                await asyncio.to_thread(
                    self._plans.save_execution,
                    run.id,
                    expected_row_revision,
                    execution=execution,
                    status="paused",
                    last_error=_safe_run_error("recovery_failed"),
                )
            except (
                InvalidPlanningTransition,
                PlanningConflictError,
                PlanningRunNotFoundError,
            ):
                raise
            except Exception as exc:
                raise PlanningRunError(
                    "planning approval state could not be restored"
                ) from exc
            raise

        pending_keys = {
            (approval.tool_name, approval.call_id)
            for approval in run.run_state_snapshot.tool_approvals
            if approval.status == "pending"
        }
        if (tool_name, call_id) not in pending_keys:
            raise PlanningInputError("pending tool approval was not found")
        if decision == "approve":
            run_state.approve_tool_call(tool_name, call_id)
        else:
            run_state.reject_tool_call(
                tool_name,
                call_id,
                normalized_rejection,
            )

        try:
            run = await asyncio.to_thread(
                self._plans.save_execution,
                run.id,
                expected_row_revision,
                execution=execution,
                status="running",
            )
            run = await self._resume_current_step(
                run,
                execution,
                node,
                setup,
                run_state,
                on_event,
            )
            if run.status == "running":
                return await self._drive_running(run, on_event)
            return _snapshot_from_run(run)
        except asyncio.CancelledError:
            raise
        except (
            InvalidPlanningTransition,
            PlanningConflictError,
            PlanningRunNotFoundError,
        ):
            raise
        except PlanningRunError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning approval resume failed") from exc

    async def retry_current(
        self,
        run_id: UUID,
        expected_row_revision: int,
        on_event: Callable[[PlanningProgressEvent], None] | None = None,
    ) -> PlanningSnapshot:
        _require_control_request(run_id, expected_row_revision)
        try:
            run = await asyncio.to_thread(self._plans.get, run_id)
        except PlanningRunNotFoundError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning data could not be read") from exc
        if run.status != "paused":
            raise InvalidPlanningTransition(
                run.id,
                run.status,
                "retry_current",
                "running",
            )
        if run.definition is None:
            raise PlanningRunError("planning execution is invalid")
        try:
            execution = PlanExecution.from_events(
                run.definition,
                run.task_events,
                owner_id=str(run.id),
            )
        except (TypeError, ValueError) as exc:
            raise PlanningRunError("planning execution is invalid") from exc

        try:
            run = await asyncio.to_thread(
                self._plans.save_execution,
                run.id,
                expected_row_revision,
                execution=execution,
                status="running",
            )
            return await self._drive_running(run, on_event)
        except asyncio.CancelledError:
            raise
        except (
            InvalidPlanningTransition,
            PlanningConflictError,
            PlanningRunNotFoundError,
        ):
            raise
        except PlanningRunError:
            raise
        except Exception as exc:
            raise PlanningRunError("planning retry failed") from exc

    async def revise(
        self,
        run_id: UUID,
        expected_row_revision: int,
        feedback: str,
        on_event: Callable[[PlanningProgressEvent], None] | None = None,
    ) -> PlanningDraftOutcome:
        _require_control_request(run_id, expected_row_revision)
        feedback = _require_control_text("feedback", feedback)
        try:
            new_run = await asyncio.to_thread(
                self._plans.revise,
                run_id,
                expected_row_revision,
                feedback,
            )
        except (
            InvalidPlanningTransition,
            PlanningConflictError,
            PlanningRunNotFoundError,
        ):
            raise
        except Exception as exc:
            raise PlanningRunError("planning revision could not be created") from exc

        return await self.draft(
            new_run.conversation_id,
            feedback,
            new_run.row_revision,
            on_event,
        )

    async def cancel(
        self,
        run_id: UUID,
        expected_row_revision: int,
        reason: str,
    ) -> PlanningSnapshot:
        _require_control_request(run_id, expected_row_revision)
        reason = _require_control_text("reason", reason)
        try:
            run = await asyncio.to_thread(
                self._plans.cancel,
                run_id,
                expected_row_revision,
                reason,
            )
        except (
            InvalidPlanningTransition,
            PlanningConflictError,
            PlanningRunNotFoundError,
        ):
            raise
        except Exception as exc:
            raise PlanningRunError("planning cancellation failed") from exc
        return _snapshot_from_run(run)

    async def recover_running(
        self,
        limit: int = 100,
        on_event: Callable[[PlanningProgressEvent], None] | None = None,
    ) -> PlanningRecoveryReport:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise PlanningInputError("limit must be an integer between 1 and 100")

        recovered: list[UUID] = []
        isolated: list[UUID] = []
        deferred: list[UUID] = []
        scanned = 0
        remaining = limit

        while remaining:
            try:
                runs = await asyncio.to_thread(
                    self._plans.list_recoverable_running,
                    limit=remaining,
                )
            except CorruptPlanningRunError as exc:
                if exc.run_id is None:
                    raise PlanningRunError("planning recovery scan failed") from exc
                scanned += 1
                remaining -= 1
                try:
                    await asyncio.to_thread(
                        self._plans.quarantine_running,
                        exc.run_id,
                        _safe_run_error("recovery_failed"),
                    )
                except (PlanningRunNotFoundError, InvalidPlanningTransition):
                    recovered.append(exc.run_id)
                except Exception:
                    deferred.append(exc.run_id)
                    break
                else:
                    isolated.append(exc.run_id)
                continue
            except Exception as exc:
                raise PlanningRunError("planning recovery scan failed") from exc

            for run in runs:
                scanned += 1
                remaining -= 1
                try:
                    recovered_run = await self._recover_one(run, on_event)
                except asyncio.CancelledError:
                    raise
                except CorruptPlanningRunError as exc:
                    corrupt_id = exc.run_id or run.id
                    try:
                        await asyncio.to_thread(
                            self._plans.quarantine_running,
                            corrupt_id,
                            _safe_run_error("recovery_failed"),
                        )
                    except (PlanningRunNotFoundError, InvalidPlanningTransition):
                        recovered.append(run.id)
                    except Exception:
                        deferred.append(run.id)
                    else:
                        isolated.append(run.id)
                except Exception:
                    deferred.append(run.id)
                else:
                    (recovered if recovered_run else deferred).append(run.id)
            break

        return PlanningRecoveryReport(
            scanned=scanned,
            recovered_run_ids=tuple(recovered),
            isolated_run_ids=tuple(isolated),
            deferred_run_ids=tuple(deferred),
        )

    async def _recover_one(
        self,
        candidate: PlanningRun,
        on_event: Callable[[PlanningProgressEvent], None] | None,
    ) -> bool:
        try:
            run = await asyncio.to_thread(self._plans.get, candidate.id)
        except PlanningRunNotFoundError:
            return True
        if run.status != "running":
            return True

        try:
            snapshot = await self._drive_running(run, on_event)
        except (PlanningConflictError, InvalidPlanningTransition):
            try:
                latest = await asyncio.to_thread(self._plans.get, candidate.id)
            except PlanningRunNotFoundError:
                return True
            if latest.status != "running":
                return True
            raise
        return snapshot.status != "running"


    # 真正的执行计划
    async def _drive_running(
        self,
        run: PlanningRun,
        on_event: Callable[[PlanningProgressEvent], None] | None,
    ) -> PlanningSnapshot:
        while True:
            if run.definition is None:
                raise PlanningRunError("planning execution is invalid")
            try:
                execution = PlanExecution.from_events(
                    run.definition,
                    run.task_events,
                    owner_id=str(run.id),
                )
            except (TypeError, ValueError) as exc:
                raise PlanningRunError("planning execution is invalid") from exc

            running = execution.task_store.list(status=TaskStatus.RUNNING)
            if len(running) > 1:
                raise PlanningRunError("planning execution is invalid")
            node = running[0] if running else execution.next_ready()
            if node is None:
                raise PlanningRunError("planning execution has no ready step")

            step = execution.step_for_task(node.task_id)
            if step.is_final_verification:
                if node.status is not TaskStatus.RUNNING:
                    run = await self._claim_task(run, execution, node)
                run = await self._run_final_verification(
                    run,
                    execution,
                    execution.task_store.get(node.task_id),
                )
                return _snapshot_from_run(run)
            if node.status is not TaskStatus.RUNNING:
                run = await self._claim_task(run, execution, node)

            run = await self._run_current_step(
                run,
                execution,
                execution.task_store.get(node.task_id),
                on_event,
            )
            if run.status != "running":
                return _snapshot_from_run(run)


    # 领取 Task：先保存
    async def _claim_task(
        self,
        run: PlanningRun,
        execution: PlanExecution,
        node: TaskNode,
    ) -> PlanningRun:

        # 用 Task revision 把 PENDING 改成 RUNNING
        execution.task_store.transition(
            node.task_id,
            expected_revision=node.revision,
            to_status=TaskStatus.RUNNING,
        )

        # 保存到 PostgreSQL
        return await asyncio.to_thread(
            self._plans.save_execution,
            run.id,
            run.row_revision,
            execution=execution,
            status="running",
        )

    def _restore_approval_run(
        self,
        run: PlanningRun,
        execution: PlanExecution,
    ) -> tuple[CodingAgentSetup, RunState, TaskNode]:
        try:
            snapshot = run.run_state_snapshot
            if snapshot is None:
                raise ValueError("approval snapshot is missing")
            approval_keys = tuple(
                (approval.tool_name, approval.call_id)
                for approval in snapshot.tool_approvals
            )
            if (
                not approval_keys
                or len(set(approval_keys)) != len(approval_keys)
                or any(
                    not isinstance(tool_name, str)
                    or not tool_name.strip()
                    or not isinstance(call_id, str)
                    or not call_id.strip()
                    for tool_name, call_id in approval_keys
                )
            ):
                raise ValueError("approval keys are invalid")

            running = execution.task_store.list(status=TaskStatus.RUNNING)
            if len(running) != 1:
                raise ValueError("approval requires one running task")
            node = running[0]
            if execution.step_for_task(node.task_id).is_final_verification:
                raise ValueError("final verification cannot request tool approval")

            _, model, manifest = self._resolve_frozen_resources(run)
            setup = build_plan_step_agent(model=model, manifest=manifest)
            context_wrapper = RunContextWrapper(
                context=setup.run_config.context,
                metadata=dict(setup.run_config.metadata or {}),
            )
            run_state = RunState.from_snapshot(
                snapshot,
                agent=setup.agent,
                context_wrapper=context_wrapper,
            )
            if (
                run_state.task_id != node.task_id
                or run_state.task_revision != node.revision
            ):
                raise ValueError("approval task link changed")

            last_response_id = snapshot.last_response_id
            if last_response_id is not None:
                if (
                    not isinstance(last_response_id, str)
                    or not last_response_id.strip()
                ):
                    raise ValueError("last response id is invalid")
                if hasattr(setup.agent.model, "previous_response_id"):
                    setup.agent.model.previous_response_id = last_response_id
            return setup, run_state, node
        except Exception as exc:
            raise PlanningRunError(
                "planning approval state could not be restored"
            ) from exc


    # 创建并运行当前步骤
    async def _run_current_step(
        self,
        run: PlanningRun,
        execution: PlanExecution,
        node: TaskNode,
        on_event: Callable[[PlanningProgressEvent], None] | None,
    ) -> PlanningRun:
        result = None
        failure_code: Literal[
            "model_step_failed",
            "tool_step_failed",
        ] | None = None
        try:
            # 创建已有的 PlanStepAgent
            _, model, manifest = await asyncio.to_thread(
                self._resolve_frozen_resources,
                run,
            )
            setup = await asyncio.to_thread(
                build_plan_step_agent,
                model=model,
                manifest=manifest,
            )
            config = replace(
                setup.run_config,
                task_store=execution.task_store,
                task_id=node.task_id,
            )
            result = await self._collect_step_stream(
                run,
                node,
                Runner.run_stream(
                    setup.agent,
                    _step_prompt(
                        execution.step_for_task(node.task_id),
                        execution,
                    ),
                    config,
                ),
                on_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_code = "model_step_failed"

        return await self._save_step_result(
            run,
            execution,
            node,
            result,
            failure_code,
        )

    async def _resume_current_step(
        self,
        run: PlanningRun,
        execution: PlanExecution,
        node: TaskNode,
        setup: CodingAgentSetup,
        run_state: RunState,
        on_event: Callable[[PlanningProgressEvent], None] | None,
    ) -> PlanningRun:
        result = None
        failure_code: Literal[
            "model_step_failed",
            "tool_step_failed",
        ] | None = None
        try:
            config = replace(
                setup.run_config,
                task_store=execution.task_store,
                task_id=node.task_id,
            )
            result = await self._collect_step_stream(
                run,
                node,
                Runner.resume_stream(setup.agent, run_state, config),
                on_event,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_code = "model_step_failed"

        return await self._save_step_result(
            run,
            execution,
            node,
            result,
            failure_code,
        )

    async def _collect_step_stream(
        self,
        run: PlanningRun,
        node: TaskNode,
        stream: Any,
        on_event: Callable[[PlanningProgressEvent], None] | None,
    ) -> RunResult:
        async with stream:
            try:
                async for event in stream:
                    if on_event is not None:
                        on_event(
                            PlanningProgressEvent(
                                run.id,
                                node.task_id,
                                event,
                            )
                        )
                return await stream.result()
            except asyncio.CancelledError:
                stream.controller.cancel("planning_cancelled")
                raise

    async def _save_step_result(
        self,
        run: PlanningRun,
        execution: PlanExecution,
        node: TaskNode,
        result: RunResult | None,
        failure_code: Literal[
            "model_step_failed",
            "tool_step_failed",
        ] | None,
    ) -> PlanningRun:
        if result is not None and result.pending_approvals:
            try:
                run_state = run_state_snapshot_from_dict(result.to_state())
            except (TypeError, ValueError):
                failure_code = "tool_step_failed"
            else:
                return await asyncio.to_thread(
                    self._plans.save_execution,
                    run.id,
                    run.row_revision,
                    execution=execution,
                    status="awaiting_tool_approval",
                    run_state=run_state,
                )

        if result is not None and failure_code is None:
            if any(item.item_type == "tool_error" for item in result.new_items):
                failure_code = "tool_step_failed"
            else:
                output = result.final_output
                if (
                    not result.reached_final_answer
                    or not isinstance(output, str)
                    or not output.strip()
                    or len(output.strip()) > _MAX_STEP_RESULT_LENGTH
                ):
                    failure_code = "model_step_failed"
                else:
                    current = execution.task_store.get(node.task_id)
                    execution.task_store.transition(
                        current.task_id,
                        expected_revision=current.revision,
                        to_status=TaskStatus.COMPLETED,
                        result=output.strip(),
                    )
                    return await asyncio.to_thread(
                        self._plans.save_execution,
                        run.id,
                        run.row_revision,
                        execution=execution,
                        status="running",
                    )

        return await asyncio.to_thread(
            self._plans.save_execution,
            run.id,
            run.row_revision,
            execution=execution,
            status="paused",
            last_error=_safe_run_error(failure_code or "model_step_failed"),
        )

    async def _run_final_verification(
        self,
        run: PlanningRun,
        execution: PlanExecution,
        node: TaskNode,
    ) -> PlanningRun:
        failure_code: Literal[
            "verification_failed",
            "verification_unavailable",
        ] | None = None
        try:
            manifest = await asyncio.to_thread(
                self._resolve_frozen_manifest,
                run,
            )
            command = _frozen_test_command(run)
            environment = LocalEnvironment(
                env=manifest.env,
                workspace=manifest.build_workspace(),
            )
            policy = VerificationPolicy(commands=(command,), max_attempts=1)
            results = await asyncio.to_thread(
                VerificationRunner(environment).run,
                policy,
            )
            if len(results) != 1 or results[0].command != command:
                failure_code = "verification_unavailable"
            elif not results[0].passed:
                failure_code = "verification_failed"
            else:
                current = execution.task_store.get(node.task_id)
                execution.task_store.transition(
                    current.task_id,
                    expected_revision=current.revision,
                    to_status=TaskStatus.COMPLETED,
                    result=f"Final verification passed: {command[:500]}",
                )
                return await asyncio.to_thread(
                    self._plans.save_execution,
                    run.id,
                    run.row_revision,
                    execution=execution,
                    status="completed",
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            failure_code = "verification_unavailable"

        return await asyncio.to_thread(
            self._plans.save_execution,
            run.id,
            run.row_revision,
            execution=execution,
            status="paused",
            last_error=_safe_run_error(
                failure_code or "verification_unavailable"
            ),
        )

    def get_snapshot(self, run_id: UUID) -> PlanningSnapshot:
        return _snapshot_from_run(self._plans.get(run_id))

    def get_latest_snapshot(
        self,
        conversation_id: UUID,
    ) -> PlanningSnapshot | None:
        if not isinstance(conversation_id, UUID):
            raise PlanningInputError("conversation_id must be a UUID")
        run = self._plans.get_latest(conversation_id)
        return None if run is None else _snapshot_from_run(run)
