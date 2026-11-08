from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal, NoReturn, cast
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.types.json import Jsonb

from ..planning import (
    PlanDefinition,
    PlanExecution,
    plan_definition_from_dict,
    plan_definition_to_dict,
)
from ..run_state import (
    RunStateSnapshot,
    run_state_snapshot_from_dict,
    run_state_snapshot_to_dict,
)
from ..task_events import task_event_from_dict, task_event_to_dict
from ..task_state import TaskEvent, TaskNode, TaskStateError, TaskStatus
from ..workspace_manifest import WorkspaceManifest
from .conversation import ConversationArchivedError, ConversationNotFoundError
from .database import Database
from .model_profiles import ModelProfile


PlanningStatus = Literal[
    "drafting",
    "awaiting_approval",
    "running",
    "awaiting_tool_approval",
    "paused",
    "completed",
    "failed",
    "cancelled",
    "superseded",
]

_ACTIVE_PLANNING_STATUSES: tuple[PlanningStatus, ...] = (
    "drafting",
    "awaiting_approval",
    "running",
    "awaiting_tool_approval",
    "paused",
)
_ACTIVE_PLANNING_STATUS_SQL = ", ".join(
    "%s" for _ in _ACTIVE_PLANNING_STATUSES
)

_PLANNING_RUN_COLUMNS = (
    "id, conversation_id, version, row_revision, status, objective, "
    "supersedes_version, revision_note, last_error, definition, task_events, "
    "run_state_snapshot, model_snapshot, workspace_snapshot, created_at, updated_at"
)
_PLANNING_VERSION_COLUMNS = (
    "id, conversation_id, version, row_revision, status, objective, "
    "supersedes_version, revision_note, created_at, updated_at"
)


# 数据库里一整版计划
@dataclass(frozen=True)
class PlanningRun:
    id: UUID
    conversation_id: UUID
    version: int
    row_revision: int
    status: PlanningStatus
    objective: str
    supersedes_version: int | None
    revision_note: str | None
    last_error: str | None
    definition: PlanDefinition | None
    task_events: tuple[TaskEvent, ...]
    run_state_snapshot: RunStateSnapshot | None
    model_snapshot: Mapping[str, Any]
    workspace_snapshot: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime



# 保存历史列表需要显示的内容
@dataclass(frozen=True)
class PlanningVersion:
    id: UUID
    conversation_id: UUID
    version: int
    row_revision: int
    status: PlanningStatus
    objective: str
    supersedes_version: int | None
    revision_note: str | None
    created_at: datetime
    updated_at: datetime


class PlanningRunNotFoundError(LookupError):
    def __init__(self, run_id: UUID) -> None:
        self.run_id = run_id
        super().__init__(f"planning run not found: {run_id}")


class ActivePlanningRunError(RuntimeError):
    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"conversation already has an active plan: {conversation_id}")


class PlanningConflictError(RuntimeError):
    def __init__(
        self,
        run_id: UUID,
        expected_revision: int,
        actual_revision: int,
    ) -> None:
        self.run_id = run_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__(
            f"planning run revision conflict: {run_id}; "
            f"expected {expected_revision}, actual {actual_revision}"
        )


class InvalidPlanningTransition(RuntimeError):
    def __init__(
        self,
        run_id: UUID,
        current_status: PlanningStatus,
        operation: str,
        target_status: PlanningStatus | None = None,
    ) -> None:
        self.run_id = run_id
        self.current_status = current_status
        self.operation = operation
        self.target_status = target_status
        target = f" to {target_status}" if target_status is not None else ""
        super().__init__(
            f"planning run {run_id} cannot {operation}{target} "
            f"from {current_status}"
        )


class CorruptPlanningRunError(RuntimeError):
    def __init__(self, run_id: UUID | None, reason: str) -> None:
        self.run_id = run_id
        self.reason = reason
        location = str(run_id) if run_id is not None else "unknown"
        super().__init__(f"planning run is corrupt: {location}; {reason}")


def _raise_corrupt(
    run_id: UUID | None,
    reason: str,
    cause: BaseException,
) -> NoReturn:
    raise CorruptPlanningRunError(run_id, reason) from cause


def _row_run_id(row: object) -> UUID | None:
    if isinstance(row, Sequence) and not isinstance(row, (str, bytes)) and row:
        value = row[0]
        return value if isinstance(value, UUID) else None
    return None


def _require_row(row: object, size: int) -> Sequence[object]:
    if not isinstance(row, Sequence) or isinstance(row, (str, bytes)):
        raise TypeError("row must be a sequence")
    if len(row) != size:
        raise ValueError(f"row must contain {size} columns")
    return row


def _require_uuid(name: str, value: object) -> UUID:
    if not isinstance(value, UUID):
        raise TypeError(f"{name} must be a UUID")
    return value


def _require_positive_integer(name: str, value: object) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _require_limit(limit: object) -> int:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer between 1 and 100")
    return limit


def _require_status(value: object) -> PlanningStatus:
    if type(value) is not str or value not in (
        "drafting",
        "awaiting_approval",
        "running",
        "awaiting_tool_approval",
        "paused",
        "completed",
        "failed",
        "cancelled",
        "superseded",
    ):
        raise ValueError("unsupported planning status")
    return cast(PlanningStatus, value)


def _require_execution_status(value: object) -> PlanningStatus:
    if type(value) is not str or value not in (
        "running",
        "awaiting_tool_approval",
        "paused",
        "completed",
        "failed",
    ):
        raise ValueError("unsupported execution planning status")
    return cast(PlanningStatus, value)


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _require_optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _require_text(name, value)


def _require_supersedes(value: object, version: int) -> int | None:
    if value is None:
        return None
    supersedes = _require_positive_integer("supersedes_version", value)
    if supersedes >= version:
        raise ValueError("supersedes_version must be less than version")
    return supersedes


def _require_utc_datetime(name: str, value: object) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a UTC datetime")
    return value


def _decode_definition(
    value: object,
    run_id: UUID,
) -> PlanDefinition | None:
    if value is None:
        return None
    try:
        if not isinstance(value, Mapping):
            raise TypeError("definition must be a mapping")
        return plan_definition_from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        _raise_corrupt(run_id, "definition invalid", exc)


def _decode_task_events(value: object, run_id: UUID) -> tuple[TaskEvent, ...]:
    try:
        if type(value) is not list:
            raise TypeError("task_events must be a list")
        return tuple(task_event_from_dict(item) for item in value)
    except (KeyError, TaskStateError, TypeError, ValueError) as exc:
        _raise_corrupt(run_id, "task events invalid", exc)


def _decode_run_state(
    value: object,
    run_id: UUID,
) -> RunStateSnapshot | None:
    if value is None:
        return None
    try:
        if not isinstance(value, Mapping):
            raise TypeError("run_state_snapshot must be a mapping")
        return run_state_snapshot_from_dict(value)
    except (KeyError, TypeError, ValueError) as exc:
        _raise_corrupt(run_id, "run state snapshot invalid", exc)


def _copy_json_mapping(
    name: str,
    value: object,
    run_id: UUID,
) -> Mapping[str, Any]:
    try:
        if not isinstance(value, Mapping):
            raise TypeError(f"{name} must be a mapping")
        if any(type(key) is not str for key in value):
            raise TypeError(f"{name} keys must be strings")
        return dict(value)
    except (TypeError, ValueError) as exc:
        _raise_corrupt(run_id, f"{name} invalid", exc)


def _validate_state_combination(
    status: PlanningStatus,
    definition: PlanDefinition | None,
    task_events: tuple[TaskEvent, ...],
    run_state: RunStateSnapshot | None,
    last_error: str | None,
) -> None:
    if definition is None and task_events:
        raise ValueError("task events require a definition")
    if status == "drafting" and definition is not None:
        raise ValueError("drafting cannot have a definition")
    if status in (
        "awaiting_approval",
        "running",
        "awaiting_tool_approval",
        "paused",
        "completed",
        "failed",
        "superseded",
    ) and definition is None:
        raise ValueError(f"{status} requires a definition")
    if status in ("drafting", "awaiting_approval") and task_events:
        raise ValueError(f"{status} cannot have task events")
    if status in (
        "running",
        "awaiting_tool_approval",
        "paused",
        "completed",
        "failed",
    ):
        if not task_events:
            raise ValueError(f"{status} requires task events")

    if status == "awaiting_tool_approval":
        if run_state is None:
            raise ValueError("awaiting_tool_approval requires a run state snapshot")
        if not any(
            approval.status == "pending" for approval in run_state.tool_approvals
        ):
            raise ValueError("awaiting_tool_approval requires a pending approval")
    elif run_state is not None:
        raise ValueError(f"{status} cannot have a run state snapshot")

    if status in ("paused", "failed"):
        if last_error is None:
            raise ValueError(f"{status} requires last_error")
    elif last_error is not None:
        raise ValueError(f"{status} cannot have last_error")


def _validate_execution_state(
    status: PlanningStatus,
    nodes: Sequence[TaskNode],
    run_state: RunStateSnapshot | None,
) -> None:
    if status == "running":
        if any(node.status is TaskStatus.FAILED for node in nodes):
            raise ValueError("running cannot have failed tasks")
        running_count = sum(
            node.status is TaskStatus.RUNNING for node in nodes
        )
        if running_count > 1:
            raise ValueError("running allows at most one running task")
        if running_count == 0 and not any(
            node.status in (TaskStatus.PENDING, TaskStatus.BLOCKED)
            for node in nodes
        ):
            raise ValueError("running requires unfinished tasks")
        return

    if status == "paused":
        if any(node.status is TaskStatus.FAILED for node in nodes):
            raise ValueError("paused cannot have failed tasks")
        if sum(node.status is TaskStatus.RUNNING for node in nodes) != 1:
            raise ValueError("paused requires exactly one running task")
        return

    if status == "awaiting_tool_approval":
        running_nodes = tuple(
            node for node in nodes if node.status is TaskStatus.RUNNING
        )
        if len(running_nodes) != 1:
            raise ValueError(
                "awaiting_tool_approval requires exactly one running task"
            )
        if run_state is None:
            raise ValueError(
                "awaiting_tool_approval requires a run state snapshot"
            )
        task = running_nodes[0]
        if (
            run_state.task_id != task.task_id
            or run_state.task_revision != task.revision
        ):
            raise ValueError("run state task link does not match the running task")
        return

    if status == "completed":
        if any(node.status is not TaskStatus.COMPLETED for node in nodes):
            raise ValueError("completed requires every task to be completed")
        return

    if status in ("failed", "cancelled", "superseded"):
        statuses = {node.status for node in nodes}
        if not statuses <= {
            TaskStatus.COMPLETED,
            TaskStatus.CANCELLED,
        }:
            raise ValueError(f"{status} cannot leave unfinished tasks")


def _planning_run_from_row(row: Sequence[object]) -> PlanningRun:
    run_id = _row_run_id(row)
    try:
        values = _require_row(row, 16)
        decoded_id = _require_uuid("id", values[0])
        run_id = decoded_id
        conversation_id = _require_uuid("conversation_id", values[1])
        version = _require_positive_integer("version", values[2])
        row_revision = _require_positive_integer("row_revision", values[3])
        status = _require_status(values[4])
        objective = _require_text("objective", values[5])
        supersedes_version = _require_supersedes(values[6], version)
        revision_note = _require_optional_text("revision_note", values[7])
        last_error = _require_optional_text("last_error", values[8])
        created_at = _require_utc_datetime("created_at", values[14])
        updated_at = _require_utc_datetime("updated_at", values[15])
    except (TypeError, ValueError) as exc:
        _raise_corrupt(run_id, "row values invalid", exc)

    definition = _decode_definition(values[9], decoded_id)
    task_events = _decode_task_events(values[10], decoded_id)
    run_state = _decode_run_state(values[11], decoded_id)
    model_snapshot = _copy_json_mapping(
        "model snapshot",
        values[12],
        decoded_id,
    )
    workspace_snapshot = _copy_json_mapping(
        "workspace snapshot",
        values[13],
        decoded_id,
    )

    try:
        _validate_state_combination(
            status,
            definition,
            task_events,
            run_state,
            last_error,
        )
    except ValueError as exc:
        _raise_corrupt(decoded_id, "status combination invalid", exc)

    if definition is not None and task_events:
        try:
            execution = PlanExecution.from_events(
                definition,
                task_events,
                owner_id=str(decoded_id),
            )
            _validate_execution_state(
                status,
                execution.task_store.list(),
                run_state,
            )
        except (TaskStateError, TypeError, ValueError) as exc:
            _raise_corrupt(decoded_id, "plan execution invalid", exc)

    return PlanningRun(
        id=decoded_id,
        conversation_id=conversation_id,
        version=version,
        row_revision=row_revision,
        status=status,
        objective=objective,
        supersedes_version=supersedes_version,
        revision_note=revision_note,
        last_error=last_error,
        definition=definition,
        task_events=task_events,
        run_state_snapshot=run_state,
        model_snapshot=model_snapshot,
        workspace_snapshot=workspace_snapshot,
        created_at=created_at,
        updated_at=updated_at,
    )


def _planning_version_from_row(row: Sequence[object]) -> PlanningVersion:
    run_id = _row_run_id(row)
    try:
        values = _require_row(row, 10)
        decoded_id = _require_uuid("id", values[0])
        run_id = decoded_id
        conversation_id = _require_uuid("conversation_id", values[1])
        version = _require_positive_integer("version", values[2])
        row_revision = _require_positive_integer("row_revision", values[3])
        status = _require_status(values[4])
        objective = _require_text("objective", values[5])
        supersedes_version = _require_supersedes(values[6], version)
        revision_note = _require_optional_text("revision_note", values[7])
        created_at = _require_utc_datetime("created_at", values[8])
        updated_at = _require_utc_datetime("updated_at", values[9])
    except (TypeError, ValueError) as exc:
        _raise_corrupt(run_id, "version row values invalid", exc)

    return PlanningVersion(
        id=decoded_id,
        conversation_id=conversation_id,
        version=version,
        row_revision=row_revision,
        status=status,
        objective=objective,
        supersedes_version=supersedes_version,
        revision_note=revision_note,
        created_at=created_at,
        updated_at=updated_at,
    )


# 在准备修改某个 PlanningRun 之前，拿到一个“当前最新且允许你继续操作”的 PlanningRun
def _lock_run(
    connection: Connection,
    run_id: UUID,
    expected_row_revision: int,
) -> PlanningRun:
    row = connection.execute(
        f"SELECT {_PLANNING_RUN_COLUMNS} "
        "FROM planning_runs WHERE id = %s FOR UPDATE",
        (run_id,),
    ).fetchone()
    if row is None:
        raise PlanningRunNotFoundError(run_id)
    run = _planning_run_from_row(row)
    if run.row_revision != expected_row_revision:
        raise PlanningConflictError(
            run_id,
            expected_row_revision,
            run.row_revision,
        )
    return run

# 真正完成一次 PlanningRun 的生命周期修改
def _update_run(
    connection: Connection,
    run: PlanningRun,
    set_clause: str,
    parameters: Sequence[object],
) -> PlanningRun:
    row = connection.execute(
        f"UPDATE planning_runs SET {set_clause}, "
        "row_revision = row_revision + 1, updated_at = now() "
        "WHERE id = %s AND row_revision = %s "
        f"RETURNING {_PLANNING_RUN_COLUMNS}",
        (*parameters, run.id, run.row_revision),
    ).fetchone()
    if row is None:
        actual_row = connection.execute(
            "SELECT row_revision FROM planning_runs WHERE id = %s",
            (run.id,),
        ).fetchone()
        actual_revision = (
            _require_positive_integer("row_revision", actual_row[0])
            if actual_row is not None
            else run.row_revision
        )
        raise PlanningConflictError(
            run.id,
            run.row_revision,
            actual_revision,
        )
    return _planning_run_from_row(row)


def _cancel_unfinished_tasks(run: PlanningRun) -> list[dict[str, object]]:
    if not run.task_events:
        return []
    if run.definition is None:
        _raise_corrupt(
            run.id,
            "definition missing",
            ValueError("task events require a definition"),
        )

    try:
        execution = PlanExecution.from_events(
            run.definition,
            run.task_events,
            owner_id=str(run.id),
        )
        store = execution.task_store
        task_ids = tuple(node.task_id for node in store.list())
        for task_id in task_ids:
            node = store.get(task_id)
            if node.status in (
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            ):
                continue
            store.transition(
                task_id,
                expected_revision=node.revision,
                to_status=TaskStatus.CANCELLED,
            )
        return [task_event_to_dict(event) for event in store.events()]
    except (TaskStateError, TypeError, ValueError) as exc:
        _raise_corrupt(run.id, "unfinished task cancellation invalid", exc)



# 一份连接plan数据库 及其操作 的服务类 功能有 : 为conversatiion 创建plan,根据 PlanningRun ID 获取某一份完整计划,查找conversation对应plan等
class PlanStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    # 给某个 Conversation 创建一份新的 PlanningRun 草案
    def create_draft(
        self,
        conversation_id: UUID,
        objective: str,
        *,
        model_profile: ModelProfile,
        manifest: WorkspaceManifest,
    ) -> PlanningRun:
        conversation_id = _require_uuid("conversation_id", conversation_id)
        objective = _require_text("objective", objective).strip()
        if not isinstance(model_profile, ModelProfile):
            raise TypeError("model_profile must be a ModelProfile")
        if not isinstance(manifest, WorkspaceManifest):
            raise TypeError("manifest must be a WorkspaceManifest")

        try:
            with self._database.transaction() as connection:
                conversation = connection.execute(
                    "SELECT status, mode FROM conversations "
                    "WHERE id = %s FOR UPDATE",
                    (conversation_id,),
                ).fetchone()
                if conversation is None:
                    raise ConversationNotFoundError(conversation_id)
                if conversation[0] != "active":
                    raise ConversationArchivedError(conversation_id)

                active = connection.execute(
                    "SELECT 1 FROM planning_runs WHERE conversation_id = %s "
                    f"AND status IN ({_ACTIVE_PLANNING_STATUS_SQL}) LIMIT 1",
                    (conversation_id, *_ACTIVE_PLANNING_STATUSES),
                ).fetchone()
                if active is not None:
                    raise ActivePlanningRunError(conversation_id)

                version_row = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 "
                    "FROM planning_runs WHERE conversation_id = %s",
                    (conversation_id,),
                ).fetchone()
                if version_row is None:
                    raise RuntimeError("failed to allocate planning version")
                version = _require_positive_integer("version", version_row[0])

                run_id = uuid4()
                model_snapshot = model_profile.public_dict()
                workspace_snapshot = manifest.metadata()
                row = connection.execute(
                    "INSERT INTO planning_runs "
                    "(id, conversation_id, version, status, objective, "
                    "model_snapshot, workspace_snapshot) "
                    "VALUES (%s, %s, %s, 'drafting', %s, %s, %s) "
                    f"RETURNING {_PLANNING_RUN_COLUMNS}",
                    (
                        run_id,
                        conversation_id,
                        version,
                        objective,
                        Jsonb(model_snapshot),
                        Jsonb(workspace_snapshot),
                    ),
                ).fetchone()
                if row is None:
                    raise RuntimeError("planning draft insert returned no row")

                connection.execute(
                    "UPDATE conversations SET mode = 'plan' WHERE id = %s",
                    (conversation_id,),
                )
                run = _planning_run_from_row(row)
        except UniqueViolation as exc:
            if (
                getattr(getattr(exc, "diag", None), "constraint_name", None)
                != "planning_runs_active_conversation_key"
            ):
                raise
            raise ActivePlanningRunError(conversation_id) from exc
        return run


    # 根据 PlanningRun ID 获取某一份完整计划
    def get(self, run_id: UUID) -> PlanningRun:
        run_id = _require_uuid("run_id", run_id)
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_PLANNING_RUN_COLUMNS} "
                "FROM planning_runs WHERE id = %s",
                (run_id,),
            ).fetchone()
        if row is None:
            raise PlanningRunNotFoundError(run_id)
        return _planning_run_from_row(row)


    # 查询一个 Conversation 当前正在进行的 PlanningRun
    def get_active(self, conversation_id: UUID) -> PlanningRun | None:
        conversation_id = _require_uuid("conversation_id", conversation_id)
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_PLANNING_RUN_COLUMNS} FROM planning_runs "
                "WHERE conversation_id = %s "
                f"AND status IN ({_ACTIVE_PLANNING_STATUS_SQL}) "
                "ORDER BY version DESC LIMIT 1",
                (conversation_id, *_ACTIVE_PLANNING_STATUSES),
            ).fetchone()
        return _planning_run_from_row(row) if row is not None else None


    # 查询一个 Conversation 最新的一版计划，包括已经结束的计划
    def get_latest(self, conversation_id: UUID) -> PlanningRun | None:
        conversation_id = _require_uuid("conversation_id", conversation_id)
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_PLANNING_RUN_COLUMNS} FROM planning_runs "
                "WHERE conversation_id = %s "
                "ORDER BY version DESC, id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return _planning_run_from_row(row) if row is not None else None


    # 获取一个 Conversation 最近的 PlanningRun 历史版本列表
    def list_versions(
        self,
        conversation_id: UUID,
        *,
        limit: int = 50,
    ) -> tuple[PlanningVersion, ...]:
        conversation_id = _require_uuid("conversation_id", conversation_id)
        limit = _require_limit(limit)
        with self._database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_PLANNING_VERSION_COLUMNS} FROM planning_runs "
                "WHERE conversation_id = %s "
                "ORDER BY version DESC, id DESC LIMIT %s",
                (conversation_id, limit),
            ).fetchall()
        return tuple(_planning_version_from_row(row) for row in rows)


    # Agent 已经把计划写完了，现在正式提交给用户审批
    def submit_for_approval(
        self,
        run_id: UUID,
        expected_row_revision: int,
        definition: PlanDefinition,
    ) -> PlanningRun:
        run_id = _require_uuid("run_id", run_id)
        expected_row_revision = _require_positive_integer(
            "expected_row_revision",
            expected_row_revision,
        )
        if not isinstance(definition, PlanDefinition):
            raise TypeError("definition must be a PlanDefinition")
        payload = plan_definition_to_dict(definition)

        with self._database.transaction() as connection:
            run = _lock_run(connection, run_id, expected_row_revision)
            if run.status != "drafting":
                raise InvalidPlanningTransition(
                    run_id,
                    run.status,
                    "submit_for_approval",
                    "awaiting_approval",
                )
            return _update_run(   # 将状态改为 "awaiting_approval"
                connection,
                run,
                "status = %s, definition = %s",
                ("awaiting_approval", Jsonb(payload)),
            )

    # 用户批准方案，把 PlanDefinition 正式物化为可执行任务，并开启运行
    def approve(
        self,
        run_id: UUID,
        expected_row_revision: int,
    ) -> PlanningRun:
        run_id = _require_uuid("run_id", run_id)
        expected_row_revision = _require_positive_integer(
            "expected_row_revision",
            expected_row_revision,
        )

        with self._database.transaction() as connection:
            run = _lock_run(connection, run_id, expected_row_revision)
            if run.status != "awaiting_approval":
                raise InvalidPlanningTransition(
                    run_id,
                    run.status,
                    "approve",
                    "running",
                )
            if run.definition is None:
                _raise_corrupt(
                    run_id,
                    "definition missing",
                    ValueError("awaiting_approval requires a definition"),
                )
            try:
                execution = PlanExecution.materialize(
                    run.definition,
                    owner_id=str(run_id),
                )
                event_payloads = [
                    task_event_to_dict(event)
                    for event in execution.task_store.events()
                ]
            except (TaskStateError, TypeError, ValueError) as exc:
                _raise_corrupt(run_id, "plan materialization invalid", exc)

            return _update_run(
                connection,
                run,
                "status = %s, task_events = %s",
                ("running", Jsonb(event_payloads)),
            )


    # 起草新计划
    def revise(
        self,
        run_id: UUID, #  我要退回哪份计划
        expected_row_revision: int,  #  我看到的是这份计划的哪个 revision
        feedback: str,  # 为什么退回、希望怎么修改
    ) -> PlanningRun:
        run_id = _require_uuid("run_id", run_id)
        expected_row_revision = _require_positive_integer(
            "expected_row_revision",
            expected_row_revision,
        )
        feedback = _require_text("feedback", feedback).strip()

        with self._database.transaction() as connection:
            run = _lock_run(connection, run_id, expected_row_revision)
            if run.status not in (
                "awaiting_approval",
                "running",
                "awaiting_tool_approval",
                "paused",
            ):
                raise InvalidPlanningTransition(
                    run_id,
                    run.status,
                    "revise",
                    "drafting",
                )
            event_payloads = _cancel_unfinished_tasks(run)
            _update_run(
                connection,
                run,
                "status = %s, task_events = %s, "
                "run_state_snapshot = NULL, last_error = NULL",
                ("superseded", Jsonb(event_payloads)),
            )

            new_run_id = uuid4()
            row = connection.execute(
                "INSERT INTO planning_runs "
                "(id, conversation_id, version, status, objective, "
                "supersedes_version, revision_note, model_snapshot, "
                "workspace_snapshot) "
                "VALUES (%s, %s, %s, 'drafting', %s, %s, %s, %s, %s) "
                f"RETURNING {_PLANNING_RUN_COLUMNS}",
                (
                    new_run_id,
                    run.conversation_id,
                    run.version + 1,
                    run.objective,
                    run.version,
                    feedback,
                    Jsonb(dict(run.model_snapshot)),
                    Jsonb(dict(run.workspace_snapshot)),
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("planning revision insert returned no row")
            return _planning_run_from_row(row)


    # 取消计划
    def cancel(
        self,
        run_id: UUID,
        expected_row_revision: int,
        reason: str,
    ) -> PlanningRun:
        run_id = _require_uuid("run_id", run_id)
        expected_row_revision = _require_positive_integer(
            "expected_row_revision",
            expected_row_revision,
        )
        reason = _require_text("reason", reason).strip()

        with self._database.transaction() as connection:
            run = _lock_run(connection, run_id, expected_row_revision)
            if run.status not in _ACTIVE_PLANNING_STATUSES:
                raise InvalidPlanningTransition(
                    run_id,
                    run.status,
                    "cancel",
                    "cancelled",
                )

            event_payloads = _cancel_unfinished_tasks(run)

            cancelled = _update_run(
                connection,
                run,
                "status = %s, revision_note = %s, task_events = %s, "
                "run_state_snapshot = NULL, last_error = NULL",
                ("cancelled", reason, Jsonb(event_payloads)),
            )
            connection.execute(
                "UPDATE conversations SET mode = 'default' WHERE id = %s",
                (run.conversation_id,),
            )
            return cancelled


    # 把当前内存中的 PlanExecution 安全保存到 PostgreSQL
    def save_execution(
        self,
        run_id: UUID,
        expected_row_revision: int,
        *,
        execution: PlanExecution,
        status: Literal[
            "running",
            "awaiting_tool_approval",
            "paused",
            "completed",
            "failed",
        ],
        run_state: RunStateSnapshot | None = None,
        last_error: str | None = None,
    ) -> PlanningRun:
        run_id = _require_uuid("run_id", run_id)
        expected_row_revision = _require_positive_integer(
            "expected_row_revision",
            expected_row_revision,
        )
        target_status = _require_execution_status(status)
        if not isinstance(execution, PlanExecution):
            raise TypeError("execution must be a PlanExecution")
        if run_state is not None and not isinstance(run_state, RunStateSnapshot):
            raise TypeError("run_state must be a RunStateSnapshot or None")
        if target_status == "awaiting_tool_approval":
            if run_state is None:
                raise ValueError(
                    "awaiting_tool_approval requires a run state snapshot"
                )
            if not any(
                approval.status == "pending"
                for approval in run_state.tool_approvals
            ):
                raise ValueError(
                    "awaiting_tool_approval requires a pending approval"
                )
        elif run_state is not None:
            raise ValueError(f"{target_status} cannot have a run state snapshot")

        if target_status in ("paused", "failed"):
            normalized_error = _require_text("last_error", last_error).strip()
        elif last_error is not None:
            raise ValueError(f"{target_status} cannot have last_error")
        else:
            normalized_error = None

        with self._database.transaction() as connection:
            run = _lock_run(connection, run_id, expected_row_revision)
            if run.status not in (
                "running",
                "awaiting_tool_approval",
                "paused",
            ) or (run.status == "paused" and target_status != "running"):
                raise InvalidPlanningTransition(
                    run_id,
                    run.status,
                    "save_execution",
                    target_status,
                )
            if execution.definition != run.definition:
                _raise_corrupt(
                    run_id,
                    "execution definition changed",
                    ValueError("execution definition does not match the saved plan"),
                )

            events = execution.task_store.events()
            try:
                restored = PlanExecution.from_events(
                    execution.definition,
                    events,
                    owner_id=str(run_id),
                )
            except (TaskStateError, TypeError, ValueError) as exc:
                _raise_corrupt(run_id, "plan execution invalid", exc)
            if (
                len(events) < len(run.task_events)
                or events[: len(run.task_events)] != run.task_events
            ):
                _raise_corrupt(
                    run_id,
                    "task event history changed",
                    ValueError("saved task events must remain an exact prefix"),
                )

            _validate_execution_state(
                target_status,
                restored.task_store.list(),
                run_state,
            )
            event_payloads = [task_event_to_dict(event) for event in events]
            snapshot_payload = (
                run_state_snapshot_to_dict(run_state)
                if run_state is not None
                else None
            )
            updated = _update_run(
                connection,
                run,
                "status = %s, task_events = %s, "
                "run_state_snapshot = %s, last_error = %s",
                (
                    target_status,
                    Jsonb(event_payloads),
                    Jsonb(snapshot_payload) if snapshot_payload is not None else None,
                    normalized_error,
                ),
            )
            if target_status in ("completed", "failed"):
                connection.execute(
                    "UPDATE conversations SET mode = 'default' WHERE id = %s",
                    (run.conversation_id,),
                )
            return updated


    # 把已经确认无法解码的 running 计划隔离，避免它阻塞后续恢复
    def quarantine_running(self, run_id: UUID, reason: str) -> None:
        run_id = _require_uuid("run_id", run_id)
        reason = _require_text("reason", reason).strip()

        with self._database.transaction() as connection:
            row = connection.execute(
                "SELECT id, conversation_id, status FROM planning_runs "
                "WHERE id = %s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if row is None:
                raise PlanningRunNotFoundError(run_id)
            locked_id, conversation_id, status = row
            if _require_uuid("id", locked_id) != run_id:
                raise RuntimeError("locked planning run id changed")
            conversation_id = _require_uuid("conversation_id", conversation_id)
            current_status = _require_status(status)
            if current_status != "running":
                raise InvalidPlanningTransition(
                    run_id,
                    current_status,
                    "quarantine_running",
                    "failed",
                )

            connection.execute(
                "UPDATE planning_runs SET status = 'failed', last_error = %s, "
                "run_state_snapshot = NULL, row_revision = row_revision + 1, "
                "updated_at = now() WHERE id = %s",
                (reason, run_id),
            )
            connection.execute(
                "UPDATE conversations SET mode = 'default' WHERE id = %s",
                (conversation_id,),
            )

    # 程序重启以后，找出“哪些 Agent 任务可以自动继续跑”。
    def list_recoverable_running(
        self,
        *,
        limit: int = 100,
    ) -> tuple[PlanningRun, ...]:
        limit = _require_limit(limit)
        with self._database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_PLANNING_RUN_COLUMNS} FROM planning_runs "
                "WHERE status = 'running' "
                "ORDER BY updated_at ASC, id ASC LIMIT %s",
                (limit,),
            ).fetchall()
        return tuple(_planning_run_from_row(row) for row in rows)
