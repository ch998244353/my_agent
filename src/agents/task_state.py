from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from threading import RLock
from typing import Callable, Iterable
from uuid import uuid4


TASK_EVENT_SCHEMA_VERSION = 1


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


# 限制事件只能表达两种变化
class TaskEventType(str, Enum):
    CREATED = "created"
    UPDATED = "updated"


_TERMINAL_TASK_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
}

_DEPENDENCY_BLOCKING_STATUSES = {
    TaskStatus.FAILED,
    TaskStatus.BLOCKED,
    TaskStatus.CANCELLED,
}


class TaskStateError(RuntimeError):
    pass


class TaskNotFoundError(TaskStateError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id!r} was not found.")


class TaskAlreadyExistsError(TaskStateError):
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id
        super().__init__(f"Task {task_id!r} already exists.")


# 记录非法状态变化
class InvalidTaskTransition(TaskStateError):
    def __init__(
        self,
        task_id: str,
        from_status: TaskStatus,
        to_status: TaskStatus,
        reason: str,
    ) -> None:
        self.task_id = task_id
        self.from_status = from_status
        self.to_status = to_status
        self.reason = reason
        super().__init__(
            f"Task {task_id!r} cannot transition from {from_status.value!r} "
            f"to {to_status.value!r}: {reason}"
        )


# 任务依赖形成 环
class TaskDependencyCycle(TaskStateError):
    def __init__(self, path: Iterable[str]) -> None:
        self.path = tuple(path)
        super().__init__(f"Task dependency cycle: {' -> '.join(self.path)}")

 
# 前置任务未完成
class TaskDependencyNotReady(TaskStateError):
    def __init__(
        self,
        task_id: str,
        dependency_id: str,
        dependency_status: TaskStatus,
    ) -> None:
        self.task_id = task_id
        self.dependency_id = dependency_id
        self.dependency_status = dependency_status
        super().__init__(
            f"Task {task_id!r} cannot start because dependency "
            f"{dependency_id!r} is {dependency_status.value!r}."
        )


# revision 冲突
class TaskConflictError(TaskStateError):
    def __init__(
        self,
        task_id: str,
        expected_revision: int,
        actual_revision: int,
        *,
        event_id: str | None = None,
        reason: str = "revision_conflict",
    ) -> None:
        self.task_id = task_id
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        self.event_id = event_id
        self.reason = reason
        if reason == "event_id_conflict":
            message = (
                f"Task event {event_id!r} conflicts with a previously "
                "applied event."
            )
        else:
            message = (
                f"Task {task_id!r} revision conflict: expected "
                f"{expected_revision}, found {actual_revision}."
            )
            if event_id is not None:
                message = f"{message[:-1]} while applying event {event_id!r}."
        super().__init__(message)


class TaskLimitExceeded(TaskStateError):
    def __init__(
        self,
        limit_name: str,
        limit: int,
        attempted: int,
    ) -> None:
        self.limit_name = limit_name
        self.limit = limit
        self.attempted = attempted
        super().__init__(
            f"Task {limit_name} limit exceeded: limit={limit}, "
            f"attempted={attempted}."
        )


def _validate_text(name: str, value: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string.")
    if not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")


def _validate_optional_text(name: str, value: str | None) -> None:
    if value is not None:
        _validate_text(name, value)


# 把依赖集合固定成 tuple
def _string_tuple(name: str, values: Iterable[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise TypeError(f"{name} must be an iterable of strings, not a string.")
    try:
        items = tuple(values)
    except TypeError as exc:
        raise TypeError(f"{name} must be an iterable of strings.") from exc
    for item in items:
        _validate_text(f"{name} item", item)
    if len(items) != len(set(items)):
        raise ValueError(f"{name} must not contain duplicate values.")
    return items


# 统一检查 UTC 时间
def _validate_utc_datetime(
    name: str,
    value: datetime | None,
    *,
    required: bool,
) -> None:
    if value is None:
        if required:
            raise ValueError(f"{name} must be a UTC-aware datetime.")
        return
    if not isinstance(value, datetime):
        raise TypeError(f"{name} must be a datetime.")
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be a UTC-aware datetime.")


# 任务描述
@dataclass(frozen=True, kw_only=True)
class TaskSpec:
    summary: str
    task_id: str | None = None
    parent_id: str | None = None
    dependency_ids: tuple[str, ...] = ()
    owner_id: str | None = None

    def __post_init__(self) -> None:
        _validate_text("summary", self.summary)
        _validate_optional_text("task_id", self.task_id)
        _validate_optional_text("parent_id", self.parent_id)
        _validate_optional_text("owner_id", self.owner_id)
        dependency_ids = _string_tuple("dependency_ids", self.dependency_ids)
        if self.task_id is not None and self.task_id in dependency_ids:
            raise ValueError("dependency_ids must not contain task_id.")
        object.__setattr__(self, "dependency_ids", dependency_ids)



# 保存 某一时刻完整任务
@dataclass(frozen=True, kw_only=True)
class TaskNode:
    task_id: str
    summary: str
    status: TaskStatus
    revision: int  # 任务版本号，每次状态或数据发生修改时加一，用于防止并发修改冲突
    created_at: datetime
    parent_id: str | None = None
    dependency_ids: tuple[str, ...] = ()  # 前置依赖任务编号列表，这些任务完成后当前任务才能开始
    owner_id: str | None = None  # 当前负责执行这个任务的 Agent、Worker 或执行器的编号
    result: object | None = None
    error: str | None = None
    blocked_by: tuple[str, ...] = ()  # 直接导致当前任务被阻塞的任务编号列表
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        spec = TaskSpec(
            summary=self.summary,
            task_id=self.task_id,
            parent_id=self.parent_id,
            dependency_ids=self.dependency_ids,
            owner_id=self.owner_id,
        )
        object.__setattr__(self, "dependency_ids", spec.dependency_ids)
        if not isinstance(self.status, TaskStatus):
            raise TypeError("status must be a TaskStatus.")
        if type(self.revision) is not int:
            raise TypeError("revision must be an integer.")
        if self.revision < 1:
            raise ValueError("revision must be a positive integer.")

        blocked_by = _string_tuple("blocked_by", self.blocked_by)
        if self.task_id in blocked_by:
            raise ValueError("blocked_by must not contain task_id.")
        object.__setattr__(self, "blocked_by", blocked_by)

        _validate_optional_text("error", self.error)
        _validate_utc_datetime("created_at", self.created_at, required=True)
        _validate_utc_datetime("started_at", self.started_at, required=False)
        _validate_utc_datetime("finished_at", self.finished_at, required=False)

        # 保证任务状态和结束时间 相等
        is_terminal = self.status in _TERMINAL_TASK_STATUSES
        if is_terminal != (self.finished_at is not None):
            raise ValueError("finished_at must be set exactly for terminal tasks.")

        # 六种状态各自允许字段
        if self.status is TaskStatus.PENDING:
            if self.result is not None or self.error:
                raise ValueError("pending tasks cannot have result or error.")
        elif self.status is TaskStatus.RUNNING:
            if self.started_at is None:
                raise ValueError("running tasks require started_at.")
            if self.result is not None or self.error:
                raise ValueError("running tasks cannot have result or error.")
        elif self.status is TaskStatus.COMPLETED:
            if self.started_at is None or self.error:
                raise ValueError("completed tasks require started_at and no error.")
        elif self.status is TaskStatus.FAILED:
            if self.started_at is None or not self.error or self.result is not None:
                raise ValueError(
                    "failed tasks require started_at and error, with no result."
                )
        elif self.status is TaskStatus.BLOCKED:
            if not self.error or self.result is not None:
                raise ValueError("blocked tasks require error and no result.")
        elif self.status is TaskStatus.CANCELLED and self.result is not None:
            raise ValueError("cancelled tasks cannot have a result.")

        if self.status is not TaskStatus.BLOCKED and self.blocked_by:
            raise ValueError("blocked_by is only valid for blocked tasks.")


# 描述一次 tasknode 的变化
@dataclass(frozen=True, kw_only=True)
class TaskEvent:
    event_id: str
    task_id: str
    previous_revision: int
    revision: int
    event_type: TaskEventType
    timestamp: datetime  # 变化发生的 UTC 时间
    node: TaskNode
    reason: str | None = None
    schema_version: int = TASK_EVENT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _validate_text("event_id", self.event_id)
        _validate_text("task_id", self.task_id)
        if type(self.previous_revision) is not int:
            raise TypeError("previous_revision must be an integer.")
        if self.previous_revision < 0:
            raise ValueError("previous_revision must not be negative.")
        if type(self.revision) is not int:
            raise TypeError("revision must be an integer.")
        if self.revision < 1:
            raise ValueError("revision must be a positive integer.")
        if not isinstance(self.event_type, TaskEventType):
            raise TypeError("event_type must be a TaskEventType.")
        _validate_utc_datetime("timestamp", self.timestamp, required=True)
        if not isinstance(self.node, TaskNode):
            raise TypeError("node must be a TaskNode.")
        _validate_optional_text("reason", self.reason)
        if type(self.schema_version) is not int:
            raise TypeError("schema_version must be an integer.")
        if self.schema_version != TASK_EVENT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported task event schema version: {self.schema_version!r}."
            )
        if self.node.task_id != self.task_id:
            raise ValueError("node.task_id must match task_id.")
        if self.node.revision != self.revision:
            raise ValueError("node.revision must match revision.")

        if self.event_type is TaskEventType.CREATED:
            if self.previous_revision != 0 or self.revision != 1:
                raise ValueError("created events require revision 0 -> 1.")
        elif self.previous_revision < 1 or self.revision != self.previous_revision + 1:
            raise ValueError(
                "updated events require consecutive positive revisions."
            )


# 合法状态变化表
_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PENDING: frozenset(
        {TaskStatus.RUNNING, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.BLOCKED: frozenset({TaskStatus.PENDING, TaskStatus.CANCELLED}),
    TaskStatus.COMPLETED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


# 任务完成时，None 也可能是合法结果，例如一个只产生副作用、不返回数据的工具
# _UNSET 是模块内部专用的标记对象，专门表示“没有传入”
_UNSET = object()


# 把旧 TaskNode 变成目标状态的新 TaskNode
def transition_task_node(
    node: TaskNode,
    to_status: TaskStatus,
    *,
    now: datetime,
    result: object = _UNSET,  # 只有完成任务时才能传
    error: str | None = None,
    blocked_by: Iterable[str] = (),  # 导致当前任务阻塞的任务编号
) -> TaskNode:
    if not isinstance(node, TaskNode):
        raise TypeError("node must be a TaskNode.")
    if not isinstance(to_status, TaskStatus):
        raise TypeError("to_status must be a TaskStatus.")
    _validate_utc_datetime("now", now, required=True)


    # 检查状态变化是否允许
    if to_status not in _ALLOWED_TRANSITIONS[node.status]:
        raise InvalidTaskTransition(
            node.task_id,
            node.status,
            to_status,
            "this transition is not allowed",
        )

    blockers = _string_tuple("blocked_by", blocked_by)
    if to_status is not TaskStatus.BLOCKED and blockers:
        raise InvalidTaskTransition(
            node.task_id,
            node.status,
            to_status,
            "blocked_by is only valid when blocking a task",
        )

    # 进入 completed 时必须明确传入结果，包括明确传入 None
    if to_status is TaskStatus.COMPLETED:
        if result is _UNSET:
            raise InvalidTaskTransition(
                node.task_id,
                node.status,
                to_status,
                "completed tasks require an explicit result",
            )
    elif result is not _UNSET:
        raise InvalidTaskTransition(
            node.task_id,
            node.status,
            to_status,
            "result is only valid when completing a task",
        )


    '''
    任务状态和错误字段保持一致：
    failed、blocked 必须写出具体原因。
    空字符串和只有空格的字符串都不算原因。
    cancelled 可以写取消原因，也可以不写。
    running、completed、pending 不能携带错误。
    '''

    if to_status in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
        if not isinstance(error, str) or not error.strip():
            raise InvalidTaskTransition(
                node.task_id,
                node.status,
                to_status,
                "failed and blocked tasks require a non-empty error",
            )
    elif to_status is not TaskStatus.CANCELLED and error is not None:
        raise InvalidTaskTransition(
            node.task_id,
            node.status,
            to_status,
            "error is only valid when failing, blocking, or cancelling a task",
        )
    else:
        _validate_optional_text("error", error)

    changes: dict[str, object] = {
        "status": to_status,
        "revision": node.revision + 1,
        "result": None,
        "error": None,
        "blocked_by": (),
        "finished_at": None,
    }
    # 根据目标状态写入专属字段
    if to_status is TaskStatus.RUNNING:
        changes["started_at"] = node.started_at or now
    elif to_status is TaskStatus.COMPLETED:
        changes["result"] = result
        changes["finished_at"] = now
    elif to_status is TaskStatus.FAILED:
        changes["error"] = error
        changes["finished_at"] = now
    elif to_status is TaskStatus.BLOCKED:
        changes["error"] = error
        changes["blocked_by"] = blockers
    elif to_status is TaskStatus.CANCELLED:
        changes["error"] = error
        changes["finished_at"] = now

    return replace(node, **changes)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _new_task_id() -> str:
    return str(uuid4())


# 接收变化前后的节点，自动判断这是创建还是更新，然后生成一条合法事件
def _task_event_from_change(
    previous: TaskNode | None, # 表示任务以前不存在，因此生成 CREATED
    node: TaskNode,
    *,
    event_id: str,
    timestamp: datetime,
    reason: str,
) -> TaskEvent:
    if previous is not None and not isinstance(previous, TaskNode):
        raise TypeError("previous must be a TaskNode or None.")
    if not isinstance(node, TaskNode):
        raise TypeError("node must be a TaskNode.")
    if previous is not None and previous.task_id != node.task_id:
        raise ValueError("previous.task_id must match node.task_id.")
    return TaskEvent(
        event_id=event_id,
        task_id=node.task_id,
        previous_revision=0 if previous is None else previous.revision,
        revision=node.revision,
        event_type=(
            TaskEventType.CREATED
            if previous is None
            else TaskEventType.UPDATED
        ),
        timestamp=timestamp,
        node=node,
        reason=reason,
    )


# 从指定节点 出发沿 传入的图 寻找目标节点，用来判断新增依赖后是否会形成环
def _dependency_path(
    nodes: Mapping[str, TaskNode],
    start_id: str,
    target_id: str,
) -> tuple[str, ...] | None:
    # ponytail: O(n²) scans are bounded by max_nodes; add indexes if it grows.
    stack = [(start_id, (start_id,))]
    visited: set[str] = set()
    while stack:
        current_id, path = stack.pop()
        if current_id == target_id:
            return path
        if current_id in visited:
            continue
        visited.add(current_id)
        current = nodes.get(current_id)
        if current is None:
            raise TaskNotFoundError(current_id)
        for dependency_id in reversed(current.dependency_ids):
            if dependency_id not in visited:
                stack.append((dependency_id, (*path, dependency_id)))
    return None


# 确认事件中的新节点不会引用不存在的任务，也不会制造过深父子层级或依赖环
def _validate_event_node_graph(
    nodes: Mapping[str, TaskNode],
    node: TaskNode,
    *,
    max_depth: int,
) -> None:
    if node.parent_id == node.task_id:
        raise ValueError("parent_id must not equal task_id.")

    depth = 0
    parent_id = node.parent_id
    visited_parents: set[str] = set()
    while parent_id is not None:
        if parent_id in visited_parents:
            raise ValueError("task parent graph must not contain a cycle.")
        visited_parents.add(parent_id)
        parent = nodes.get(parent_id)
        if parent is None:
            raise TaskNotFoundError(parent_id)
        depth += 1
        parent_id = parent.parent_id
    if depth > max_depth:
        raise TaskLimitExceeded("max_depth", max_depth, depth)

    check_dependency_cycles = node.task_id in nodes
    for dependency_id in node.dependency_ids:
        if dependency_id not in nodes:
            raise TaskNotFoundError(dependency_id)
        if check_dependency_cycles:
            path = _dependency_path(nodes, dependency_id, node.task_id)
            if path is not None:
                raise TaskDependencyCycle((node.task_id, *path))


# 判断 updated event 是否属于现有合法变化
def _is_allowed_task_update(current: TaskNode, event: TaskEvent) -> bool:
    candidate = event.node
    if (
        candidate.summary,
        candidate.parent_id,
        candidate.owner_id,
        candidate.created_at,
    ) != (
        current.summary,
        current.parent_id,
        current.owner_id,
        current.created_at,
    ):
        return False

    if candidate.status is not current.status:
        expected = transition_task_node(
            current,
            candidate.status,
            now=event.timestamp,
            result=(
                candidate.result
                if candidate.status is TaskStatus.COMPLETED
                else _UNSET
            ),
            error=candidate.error,
            blocked_by=candidate.blocked_by,
        )
        return expected == candidate

    if (
        current.status in {TaskStatus.PENDING, TaskStatus.BLOCKED}
        and len(candidate.dependency_ids) == len(current.dependency_ids) + 1
        and candidate.dependency_ids[:-1] == current.dependency_ids
    ):
        return candidate == replace(
            current,
            dependency_ids=candidate.dependency_ids,
            revision=current.revision + 1,
        )

    if (
        current.status is TaskStatus.BLOCKED
        and len(candidate.blocked_by) > len(current.blocked_by)
        and candidate.blocked_by[: len(current.blocked_by)]
        == current.blocked_by
    ):
        return candidate == replace(
            current,
            blocked_by=candidate.blocked_by,
            revision=current.revision + 1,
        )

    return False


# 拿旧任务图和一条事件，判断这条事件是否应该写入临时任务图
'''按照固定顺序检查一条事件，合法时写入临时节点表，完全重复时不做任何修改'''
def _reduce_task_event(
    nodes: dict[str, TaskNode],
    seen_events: dict[str, TaskEvent],
    event: TaskEvent,
    *,
    max_nodes: int,
    max_depth: int,
) -> bool:
    if not isinstance(event, TaskEvent):
        raise TypeError("event must be a TaskEvent.")

    previous_event = seen_events.get(event.event_id)
    if previous_event is not None:
        if previous_event is event or previous_event == event:
            return False
        raise TaskConflictError(
            event.task_id,
            previous_event.revision,
            event.revision,
            event_id=event.event_id,
            reason="event_id_conflict",
        )

    if event.event_type is TaskEventType.CREATED:
        if event.task_id in nodes:
            raise TaskAlreadyExistsError(event.task_id)
        if len(nodes) >= max_nodes:
            raise TaskLimitExceeded("max_nodes", max_nodes, len(nodes) + 1)
        if (
            event.node.status is not TaskStatus.PENDING
            or event.node.started_at is not None
        ):
            raise ValueError("created events require a fresh pending node.")
    else:
        current = nodes.get(event.task_id)
        if current is None:
            raise TaskNotFoundError(event.task_id)
        if current.revision != event.previous_revision:
            raise TaskConflictError(
                event.task_id,
                event.previous_revision,
                current.revision,
                event_id=event.event_id,
            )
        if not _is_allowed_task_update(current, event):
            raise ValueError(
                "updated event contains a change that TaskStore does not allow."
            )

    _validate_event_node_graph(
        nodes,
        event.node,
        max_depth=max_depth,
    )
    nodes[event.task_id] = event.node
    seen_events[event.event_id] = event
    return True


# 保存当前进程中的任务节点
class TaskStore:
    def __init__(
        self,
        max_nodes: int = 100,
        max_depth: int = 8,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _new_task_id,
        event_id_factory: Callable[[], str] = _new_task_id,
    ) -> None:
        if type(max_nodes) is not int:
            raise TypeError("max_nodes must be an integer.")
        if max_nodes < 1:
            raise ValueError("max_nodes must be a positive integer.")
        if type(max_depth) is not int:
            raise TypeError("max_depth must be an integer.")
        if max_depth < 0:
            raise ValueError("max_depth must not be negative.")
        if not callable(clock):
            raise TypeError("clock must be callable.")
        if not callable(id_factory):
            raise TypeError("id_factory must be callable.")
        if not callable(event_id_factory):
            raise TypeError("event_id_factory must be callable.")

        self._nodes: dict[str, TaskNode] = {}  # 每个任务当前最新的节点
        self._seen_events: dict[str, TaskEvent] = {}  # event_id → 完整事件，用于识别重复和冲突
        self._events: list[TaskEvent] = []   # 按照首次成功应用顺序保存的 事件
        self._max_nodes = max_nodes
        self._max_depth = max_depth
        self._clock = clock
        self._id_factory = id_factory
        self._event_id_factory = event_id_factory  # 允许每次产生事件时取得一个独立编号，同时便于测试重复编号和生成失败
        # ponytail: one lock fits max_nodes=100; split only after measured contention.
        self._lock = RLock()


    # 使用编号查询 _nodes
    def _require_node(self, task_id: str) -> TaskNode:
        node = self._nodes.get(task_id)
        if node is None:
            raise TaskNotFoundError(task_id)
        return node

    # 计算 parent 深度
    def _parent_depth(self, parent_id: str | None) -> int:
        depth = 0
        while parent_id is not None:
            depth += 1
            parent_id = self._nodes[parent_id].parent_id
        return depth

    # create()接收一个 TaskSpec，成功时返回新建的 TaskNode
    def create(self, spec: TaskSpec) -> TaskNode:
        if not isinstance(spec, TaskSpec):
            raise TypeError("spec must be a TaskSpec.")
        with self._lock:
            if len(self._nodes) >= self._max_nodes:
                raise TaskLimitExceeded(
                    "max_nodes",
                    self._max_nodes,
                    len(self._nodes) + 1,
                )

            task_id = (
                spec.task_id if spec.task_id is not None else self._id_factory()
            )
            _validate_text("task_id", task_id)
            if task_id in self._nodes:
                raise TaskAlreadyExistsError(task_id)

            if spec.parent_id == task_id:
                raise ValueError("parent_id must not equal task_id.")
            if spec.parent_id is not None:
                self._require_node(spec.parent_id)

            # 任务不能依赖自己   并且每个 dependency 必须已经存在
            if task_id in spec.dependency_ids:
                raise ValueError("dependency_ids must not contain task_id.")
            for dependency_id in spec.dependency_ids:
                self._require_node(dependency_id)

            depth = self._parent_depth(spec.parent_id)
            if depth > self._max_depth:
                raise TaskLimitExceeded("max_depth", self._max_depth, depth)

            created_at = self._clock()
            _validate_utc_datetime("clock()", created_at, required=True)
            node = TaskNode(
                task_id=task_id,
                summary=spec.summary,
                status=TaskStatus.PENDING,
                revision=1,
                created_at=created_at,
                parent_id=spec.parent_id,
                dependency_ids=spec.dependency_ids,
                owner_id=spec.owner_id,
            )
            event = _task_event_from_change(
                None,
                node,
                event_id=self._event_id_factory(),
                timestamp=created_at,
                reason="created",
            )
            self._apply_event_batch((event,))
            return node


    # 根据任务编号返回冻结节点
    def get(self, task_id: str) -> TaskNode:
        _validate_text("task_id", task_id)
        with self._lock:
            return self._require_node(task_id)


    # 这个方法把符合条件的节点复制到 tuple 中返回 : 1.筛选status状态  2.返回owner_id该负责人名下
    def list(
        self,
        *,
        status: TaskStatus | None = None,
        owner_id: str | None = None,
    ) -> tuple[TaskNode, ...]:
        if status is not None and not isinstance(status, TaskStatus):
            raise TypeError("status must be a TaskStatus or None.")
        _validate_optional_text("owner_id", owner_id)
        with self._lock:
            return tuple(
                node
                for node in self._nodes.values()
                if (status is None or node.status is status)
                and (owner_id is None or node.owner_id == owner_id)
            )

    def events(self) -> tuple[TaskEvent, ...]:
        with self._lock:
            return tuple(self._events)


    # 将 taskevent 的节点变化 更新到 Store
    def _apply_event_batch(
        self,
        events: Iterable[TaskEvent],
    ) -> tuple[TaskEvent, ...]:
        try:
            batch = tuple(events)
        except TypeError as exc:
            raise TypeError(
                "events must be an iterable of TaskEvent."
            ) from exc
        if any(not isinstance(event, TaskEvent) for event in batch):
            raise TypeError("events must contain only TaskEvent values.")
        if not batch:
            return ()

        with self._lock:
            working_nodes = dict(self._nodes)
            working_seen = dict(self._seen_events)
            applied: list[TaskEvent] = []
            for event in batch:
                if _reduce_task_event(
                    working_nodes,
                    working_seen,
                    event,
                    max_nodes=self._max_nodes,
                    max_depth=self._max_depth,
                ):
                    applied.append(event)

            if not applied:
                return ()
            self._nodes = working_nodes
            self._seen_events = working_seen
            self._events.extend(applied)
            return tuple(applied)

    def _assert_dependencies_completed(self, node: TaskNode) -> None:
        for dependency_id in node.dependency_ids:
            dependency = self._require_node(dependency_id)
            if dependency.status is not TaskStatus.COMPLETED:
                raise TaskDependencyNotReady(
                    node.task_id,
                    dependency_id,
                    dependency.status,
                )


    # 把“准备提交的新节点”存入字典，同时记住它第一次出现的位置
    def _stage_node(
        self,
        node: TaskNode, # 新任务节点
        staged: dict[str, TaskNode], # 暂存所有待提交节点，格式为 task_id -> 最新 TaskNode
        order: list[str],  # 记录任务最终提交的顺序
    ) -> None:
        if node.task_id not in staged:
            order.append(node.task_id)
        staged[node.task_id] = node


    # 从被取消的父任务开始，一层一层找出全部后代，并计划取消尚未结束的节点
    def _stage_cancelled_descendants(
        self,
        parent_id: str,
        *,
        now: datetime,
        staged: dict[str, TaskNode],
        order: list[str],
    ) -> None:
        parent_ids = {parent_id}
        visited = {parent_id}
        # ponytail: O(n²) scan is bounded by max_nodes=100; index if it grows.
        while parent_ids:
            child_ids: list[str] = []
            for stored in self._nodes.values():
                if stored.parent_id not in parent_ids:
                    continue
                if stored.task_id in visited:
                    continue

                visited.add(stored.task_id)
                child_ids.append(stored.task_id)
                current = staged.get(stored.task_id, stored)
                if current.status in _TERMINAL_TASK_STATUSES:
                    continue
                self._stage_node(
                    transition_task_node(
                        current,
                        TaskStatus.CANCELLED,
                        now=now,
                        error=f"parent {parent_id!r} was cancelled",
                    ),
                    staged,
                    order,
                )
            parent_ids = set(child_ids)


    # 从一批“已经失败、阻塞或取消”的任务出发，把所有直接或间接依赖它们的任务递归标记为 BLOCKED
    def _stage_dependency_blocks(
        self,
        source_ids: Iterable[str],
        *,
        now: datetime,
        staged: dict[str, TaskNode],
        order: list[str],
    ) -> None:
        queue = deque(dict.fromkeys(source_ids))
        visited: set[str] = set()
        # ponytail: O(n²) scan is bounded by max_nodes=100; index if it grows.
        while queue:
            source_id = queue.popleft()
            if source_id in visited:
                continue
            visited.add(source_id)
            source = staged.get(source_id)
            if source is None:
                source = self._require_node(source_id)
            if source.status not in _DEPENDENCY_BLOCKING_STATUSES:
                continue

            for stored in self._nodes.values():
                if source_id not in stored.dependency_ids:
                    continue
                current = staged.get(stored.task_id, stored)
                if current.status in _TERMINAL_TASK_STATUSES:
                    continue

                bad_dependencies: list[str] = []
                for dependency_id in current.dependency_ids:
                    dependency = staged.get(dependency_id)
                    if dependency is None:
                        dependency = self._require_node(dependency_id)
                    if dependency.status in _DEPENDENCY_BLOCKING_STATUSES:
                        bad_dependencies.append(dependency_id)

                if current.status in {
                    TaskStatus.PENDING,
                    TaskStatus.RUNNING,
                }:
                    blocker = staged.get(bad_dependencies[0])
                    if blocker is None:
                        blocker = self._require_node(bad_dependencies[0])
                    updated = transition_task_node(
                        current,
                        TaskStatus.BLOCKED,
                        now=now,
                        error=(
                            f"dependency {blocker.task_id!r} is "
                            f"{blocker.status.value!r}"
                        ),
                        blocked_by=bad_dependencies,
                    )
                    self._stage_node(updated, staged, order)
                else:
                    blockers = tuple(
                        dict.fromkeys(
                            (*current.blocked_by, *bad_dependencies)
                        )
                    )
                    if blockers != current.blocked_by:
                        original = self._require_node(current.task_id)
                        self._stage_node(
                            replace(
                                current,
                                blocked_by=blockers,
                                revision=original.revision + 1,
                            ),
                            staged,
                            order,
                        )
                if stored.task_id not in visited:
                    queue.append(stored.task_id)


    # 更新 tasknode 状态
    def transition(
        self,
        task_id: str,
        *,
        expected_revision: int,
        to_status: TaskStatus,
        result: object = _UNSET,
        error: str | None = None,
    ) -> TaskNode:
        _validate_text("task_id", task_id)
        if type(expected_revision) is not int:
            raise TypeError("expected_revision must be an integer.")
        if expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer.")
        if not isinstance(to_status, TaskStatus):
            raise TypeError("to_status must be a TaskStatus.")

        with self._lock:
            node = self._require_node(task_id)
            if node.revision != expected_revision:
                raise TaskConflictError(
                    task_id,
                    expected_revision,
                    node.revision,
                )
            if to_status is TaskStatus.RUNNING or (
                node.status is TaskStatus.BLOCKED
                and to_status is TaskStatus.PENDING
            ):
                self._assert_dependencies_completed(node)

            now = self._clock()
            updated = transition_task_node(
                node,
                to_status,
                now=now,
                result=result,
                error=error,
            )
            staged: dict[str, TaskNode] = {}
            order: list[str] = []
            self._stage_node(updated, staged, order)
            if updated.status is TaskStatus.CANCELLED:
                self._stage_cancelled_descendants(
                    updated.task_id,
                    now=now,
                    staged=staged,
                    order=order,
                )

            blocking_sources = tuple(
                changed_id
                for changed_id in order
                if staged[changed_id].status
                in _DEPENDENCY_BLOCKING_STATUSES
            )
            if blocking_sources:
                self._stage_dependency_blocks(
                    blocking_sources,
                    now=now,
                    staged=staged,
                    order=order,
                )

            events: list[TaskEvent] = []
            for changed_id in order:
                changed = staged[changed_id]
                if changed_id == task_id:
                    reason = "transition"
                elif changed.status is TaskStatus.CANCELLED:
                    reason = "parent_cancelled"
                else:
                    reason = "dependency_blocked"
                events.append(
                    _task_event_from_change(
                        self._nodes[changed_id],
                        changed,
                        event_id=self._event_id_factory(),
                        timestamp=now,
                        reason=reason,
                    )
                )
            self._apply_event_batch(events)
            return staged[task_id]


    # 给已有目标任务追加一个依赖
    def add_dependency(
        self,
        task_id: str,
        dependency_id: str,
        *,
        expected_revision: int,
    ) -> TaskNode:
        _validate_text("task_id", task_id)
        _validate_text("dependency_id", dependency_id)
        if type(expected_revision) is not int:
            raise TypeError("expected_revision must be an integer.")
        if expected_revision < 1:
            raise ValueError("expected_revision must be a positive integer.")

        with self._lock:
            node = self._require_node(task_id)
            self._require_node(dependency_id)
            if node.status not in {TaskStatus.PENDING, TaskStatus.BLOCKED}:
                raise ValueError(
                    "dependencies can only be added to pending or blocked tasks."
                )
            if node.revision != expected_revision:
                raise TaskConflictError(
                    task_id,
                    expected_revision,
                    node.revision,
                )
            if task_id == dependency_id:
                raise ValueError("a task cannot depend on itself.")
            if dependency_id in node.dependency_ids:
                raise ValueError("dependency already exists.")

            # 判断新增后是否有环
            path = _dependency_path(self._nodes, dependency_id, task_id)
            if path is not None:
                raise TaskDependencyCycle((task_id, *path))

            updated = replace(
                node,
                dependency_ids=(*node.dependency_ids, dependency_id),
                revision=node.revision + 1,
            )
            now = self._clock()
            event = _task_event_from_change(
                node,
                updated,
                event_id=self._event_id_factory(),
                timestamp=now,
                reason="dependency_added",
            )
            self._apply_event_batch((event,))
            return updated

    # 扫描当前 Store，返回所有可以开始的冻结节点
    def ready(self) -> tuple[TaskNode, ...]:
        with self._lock:
            return tuple(
                node
                for node in self._nodes.values()
                if node.status is TaskStatus.PENDING
                and all(
                    self._require_node(dependency_id).status
                    is TaskStatus.COMPLETED
                    for dependency_id in node.dependency_ids
                )
            )

    # 接收一个任务编号，从这个节点的 blocked_by开始按层查找，返回全部唯一 blocker
    def blocking_chain(self, task_id: str) -> tuple[str, ...]:
        _validate_text("task_id", task_id)
        with self._lock:
            start = self._require_node(task_id)
            result: list[str] = []
            visited = {task_id}
            queue = deque(start.blocked_by)
            # ponytail: max_nodes bounds this level scan; index only if it grows.
            while queue:
                level = tuple(queue)
                queue.clear()
                for blocker_id in level:
                    self._require_node(blocker_id)
                level_ids = set(level) - visited
                if not level_ids:
                    break

                next_level: list[str] = []
                for blocker_id, blocker in self._nodes.items():
                    if blocker_id not in level_ids:
                        continue
                    visited.add(blocker_id)
                    result.append(blocker_id)
                    next_level.extend(blocker.blocked_by)
                queue.extend(next_level)
            return tuple(result)


    # 外部系统已经确认某个 owner 消失了，把这个 owner 负责的、还没结束的任务统一标记为 BLOCKED
    def mark_owner_missing(self, owner_id: str) -> tuple[TaskNode, ...]:
        _validate_text("owner_id", owner_id)
        with self._lock:
            targets = tuple(
                node
                for node in self._nodes.values()
                if node.owner_id == owner_id
                and node.status in {TaskStatus.PENDING, TaskStatus.RUNNING}
            )
            if not targets:
                return ()

            now = self._clock()
            staged: dict[str, TaskNode] = {}
            order: list[str] = []

            # 为全部目标任务计算新状态
            for node in targets:
                self._stage_node(
                    transition_task_node(
                        node,
                        TaskStatus.BLOCKED,
                        now=now,
                        error=f"owner {owner_id!r} is missing",
                    ),
                    staged,
                    order,
                )
            self._stage_dependency_blocks(
                tuple(order),
                now=now,
                staged=staged,
                order=order,
            )
            target_ids = {node.task_id for node in targets}
            events = tuple(
                _task_event_from_change(
                    self._nodes[changed_id],
                    staged[changed_id],
                    event_id=self._event_id_factory(),
                    timestamp=now,
                    reason=(
                        "owner_missing"
                        if changed_id in target_ids
                        else "dependency_blocked"
                    ),
                )
                for changed_id in order
            )
            self._apply_event_batch(events)
            return tuple(staged[node.task_id] for node in targets)
