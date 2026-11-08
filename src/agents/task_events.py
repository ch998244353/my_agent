from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import datetime

from .task_state import (
    TASK_EVENT_SCHEMA_VERSION,
    TaskEvent,
    TaskEventType,
    TaskNode,
    TaskStatus,
    TaskStore,
)


_TASK_NODE_FIELDS = frozenset(
    {
        "task_id",
        "summary",
        "status",
        "revision",
        "created_at",
        "parent_id",
        "dependency_ids",
        "owner_id",
        "result",
        "error",
        "blocked_by",
        "started_at",
        "finished_at",
    }
)

_TASK_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "task_id",
        "previous_revision",
        "revision",
        "event_type",
        "timestamp",
        "reason",
        "node",
    }
)


def _require_fields(
    name: str,
    data: object,
    expected: frozenset[str],
) -> Mapping[str, object]:
    if not isinstance(data, Mapping):
        raise TypeError(f"{name} must be a mapping.")
    actual = frozenset(data)
    if actual != expected:
        missing = ", ".join(sorted(expected - actual))
        extra = ", ".join(sorted(map(repr, actual - expected)))
        raise ValueError(
            f"{name} fields do not match; missing=[{missing}], extra=[{extra}]."
        )
    return data
 

# 把 Python UTC 时间变成稳定的 JSON 字符串
def _datetime_to_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")


# 从 Z 字符串恢复 UTC 时间
def _datetime_from_z(
    name: str,
    value: object,
    *,
    required: bool,
) -> datetime | None:
    if value is None:
        if required:
            raise ValueError(f"{name} must be a UTC datetime ending in 'Z'.")
        return None
    if type(value) is not str:
        raise TypeError(f"{name} must be a string.")
    if not value.endswith("Z"):
        raise ValueError(f"{name} must be a UTC datetime ending in 'Z'.")
    try:
        parsed = datetime.fromisoformat(f"{value[:-1]}+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid UTC datetime.") from exc
    if _datetime_to_z(parsed) != value:
        raise ValueError(f"{name} must use the canonical UTC 'Z' format.")
    return parsed



# 比较 JSON 前后的真实数据类型
def _same_json_value(original: object, restored: object) -> bool:
    if type(original) is not type(restored):
        return False
    if isinstance(original, list):
        return len(original) == len(restored) and all(
            _same_json_value(left, right)
            for left, right in zip(original, restored, strict=True)
        )
    if isinstance(original, dict):
        return (
            all(type(key) is str for key in original)
            and original.keys() == restored.keys()
            and all(
                _same_json_value(original[key], restored[key])
                for key in original
            )
        )
    return original == restored


#让一个值真正经过一次 JSON 保存和读取，确认它不会损坏或变形
def _lossless_json_value(name: str, value: object) -> object:
    try:
        restored = json.loads(
            json.dumps(value, ensure_ascii=False, allow_nan=False)
        )
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{name} must be a lossless JSON value.") from exc
    if not _same_json_value(value, restored):
        raise TypeError(f"{name} must be a lossless JSON value.")
    return restored



# 把 TaskNode 写成普通字
def task_node_to_dict(node: TaskNode) -> dict[str, object]:
    if not isinstance(node, TaskNode):
        raise TypeError("node must be a TaskNode.")
    return {
        "task_id": node.task_id,
        "summary": node.summary,
        "status": node.status.value,
        "revision": node.revision,
        "created_at": _datetime_to_z(node.created_at),
        "parent_id": node.parent_id,
        "dependency_ids": list(node.dependency_ids),
        "owner_id": node.owner_id,
        "result": _lossless_json_value("node.result", node.result),
        "error": node.error,
        "blocked_by": list(node.blocked_by),
        "started_at": _datetime_to_z(node.started_at),
        "finished_at": _datetime_to_z(node.finished_at),
    }


# 从字典恢复 TaskNode
def task_node_from_dict(data: Mapping[str, object]) -> TaskNode:
    values = _require_fields("TaskNode", data, _TASK_NODE_FIELDS)

    status_value = values["status"]
    if type(status_value) is not str:
        raise TypeError("TaskNode.status must be a string.")
    try:
        status = TaskStatus(status_value)
    except ValueError as exc:
        raise ValueError(f"Unsupported task status: {status_value!r}.") from exc

    dependency_ids = values["dependency_ids"]
    if type(dependency_ids) is not list:
        raise TypeError("TaskNode.dependency_ids must be a list.")
    blocked_by = values["blocked_by"]
    if type(blocked_by) is not list:
        raise TypeError("TaskNode.blocked_by must be a list.")

    return TaskNode(
        task_id=values["task_id"],
        summary=values["summary"],
        status=status,
        revision=values["revision"],
        created_at=_datetime_from_z(
            "TaskNode.created_at",
            values["created_at"],
            required=True,
        ),
        parent_id=values["parent_id"],
        dependency_ids=tuple(dependency_ids),
        owner_id=values["owner_id"],
        result=_lossless_json_value("TaskNode.result", values["result"]),
        error=values["error"],
        blocked_by=tuple(blocked_by),
        started_at=_datetime_from_z(
            "TaskNode.started_at",
            values["started_at"],
            required=False,
        ),
        finished_at=_datetime_from_z(
            "TaskNode.finished_at",
            values["finished_at"],
            required=False,
        ),
    )


# 把 TaskEvent 写成事件字典
def task_event_to_dict(event: TaskEvent) -> dict[str, object]:
    if not isinstance(event, TaskEvent):
        raise TypeError("event must be a TaskEvent.")
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "task_id": event.task_id,
        "previous_revision": event.previous_revision,
        "revision": event.revision,
        "event_type": event.event_type.value,
        "timestamp": _datetime_to_z(event.timestamp),
        "reason": event.reason,
        "node": task_node_to_dict(event.node),
    }


# 从事件字典恢复 TaskEvent
def task_event_from_dict(data: Mapping[str, object]) -> TaskEvent:
    values = _require_fields("TaskEvent", data, _TASK_EVENT_FIELDS)

    schema_version = values["schema_version"]
    if type(schema_version) is not int:
        raise TypeError("TaskEvent.schema_version must be an integer.")
    if schema_version != TASK_EVENT_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported task event schema version: {schema_version!r}."
        )

    event_type_value = values["event_type"]
    if type(event_type_value) is not str:
        raise TypeError("TaskEvent.event_type must be a string.")
    try:
        event_type = TaskEventType(event_type_value)
    except ValueError as exc:
        raise ValueError(
            f"Unsupported task event type: {event_type_value!r}."
        ) from exc

    return TaskEvent(
        schema_version=schema_version,
        event_id=values["event_id"],
        task_id=values["task_id"],
        previous_revision=values["previous_revision"],
        revision=values["revision"],
        event_type=event_type,
        timestamp=_datetime_from_z(
            "TaskEvent.timestamp",
            values["timestamp"],
            required=True,
        ),
        reason=values["reason"],
        node=task_node_from_dict(values["node"]),
    )


# 把历史事件按原顺序放进一个空任务仓库，最后返回恢复完成的仓库
def replay_task_events(
    events: Iterable[TaskEvent],
    *,
    max_nodes: int = 100,
    max_depth: int = 8,
) -> TaskStore:
    batch = tuple(events)
    store = TaskStore(max_nodes=max_nodes, max_depth=max_depth)
    store._apply_event_batch(batch)
    return store
