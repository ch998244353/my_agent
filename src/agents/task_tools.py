from __future__ import annotations

from typing import Literal

from .task_events import task_node_to_dict
from .task_state import TaskNode, TaskSpec, TaskStatus, TaskStore
from .tools import FunctionTool, function_tool


_TaskStatusInput = Literal[
    "pending",
    "running",
    "completed",
    "failed",
    "blocked",
    "cancelled",
]
_RESULT_UNSET = object()


# 判断一个任务是否属于指定根任务的子任务树
def _is_in_root_scope(
    store: TaskStore,
    root_task_id: str,
    task_id: str,
) -> bool:
    node = store.get(task_id)
    while node.task_id != root_task_id:
        if node.parent_id is None:
            return False
        node = store.get(node.parent_id)
    return True


# 验证目标任务是否可以被当前调用者操作 且 位于 root 子树
def _require_owned_task(
    store: TaskStore,
    *,
    root_task_id: str,
    owner_id: str,
    task_id: str,
) -> TaskNode:
    node = store.get(task_id)
    if not _is_in_root_scope(store, root_task_id, task_id):   #是否在子任务重 
        raise ValueError(
            f"Task {task_id!r} is outside root task {root_task_id!r}."
        )
    if node.owner_id != owner_id:  # 是否归于当前这个agent管辖
        raise ValueError(
            f"Task {task_id!r} is owned by {node.owner_id!r}, not {owner_id!r}."
        )
    return node


def create_task_tools(
    store: TaskStore,
    *,
    root_task_id: str,
    owner_id: str,
) -> tuple[FunctionTool, ...]:
    if not isinstance(store, TaskStore):
        raise TypeError("store must be a TaskStore.")
    store.get(root_task_id)
    if not isinstance(owner_id, str):
        raise TypeError("owner_id must be a string.")
    if not owner_id.strip():
        raise ValueError("owner_id must be a non-empty string.")


    # 在绑定的 root 任务树中创建一个属于当前 owner 的新任务
    @function_tool   # 这个函数为 function call
    def create_task(
        summary: str,
        dependency_ids: tuple[str, ...] = (),
        parent_id: str | None = None,
    ) -> dict[str, object]:
        """Create a task inside the bound planning root.

        Args:
            summary: Short description of the task.
            dependency_ids: Existing tasks that must finish first.
            parent_id: Parent task, or the bound root when omitted.
        """
        effective_parent_id = (  # 是否挂在某个节点
            root_task_id if parent_id is None else parent_id
        )
        if not _is_in_root_scope(
            store,
            root_task_id,
            effective_parent_id,
        ):
            raise ValueError(
                f"parent_id {effective_parent_id!r} is outside "
                f"root task {root_task_id!r}."
            )


        # 所有依赖任务也必须处于同一个 root 子树中  防止出现跨任务树依赖
        dependencies = tuple(dependency_ids)
        for dependency_id in dependencies:
            if not _is_in_root_scope(store, root_task_id, dependency_id):
                raise ValueError(
                    f"dependency_id {dependency_id!r} is outside "
                    f"root task {root_task_id!r}."
                )

        node = store.create(
            TaskSpec(
                summary=summary,
                parent_id=effective_parent_id,
                dependency_ids=dependencies,
                owner_id=owner_id,
            )
        )
        return task_node_to_dict(node)


    # 修改绑定任务树中、属于当前 owner 的现有任务状态
    @function_tool
    def update_task(
        task_id: str,
        expected_revision: int,
        status: _TaskStatusInput,
        result: object = _RESULT_UNSET,
        error: str | None = None,
    ) -> dict[str, object]:
        """Change the status of a task owned inside the bound planning root.

        Args:
            task_id: Existing task to update.
            expected_revision: Revision last seen by the caller.
            status: New task status.
            result: Explicit completion result, including null.
            error: Failure, blocking, or cancellation reason.
        """
        _require_owned_task(
            store,
            root_task_id=root_task_id,
            owner_id=owner_id,
            task_id=task_id,
        )
        transition_args: dict[str, object] = {
            "expected_revision": expected_revision,
            "to_status": TaskStatus(status),
            "error": error,
        }
        if result is not _RESULT_UNSET:
            transition_args["result"] = result
        node = store.transition(task_id, **transition_args)
        return task_node_to_dict(node)


    # 查询当前 Agent 在绑定 root 子树中可见的任务 
    @function_tool
    def list_tasks(
        status: _TaskStatusInput | None = None,
        ready_only: bool = False,  # 这个任务是否前置依赖完成 且 状态为 pending
    ) -> list[dict[str, object]]:
        """List tasks visible inside the bound planning root.

        Args:
            status: Optional task status filter.
            ready_only: Return only pending tasks whose dependencies completed.
        """
        if ready_only and status is not None:
            raise ValueError("status and ready_only cannot be used together.")

        if ready_only:
            nodes = (
                node
                for node in store.ready()
                if node.owner_id == owner_id
            )
        else:
            parsed_status = (
                TaskStatus(status) if status is not None else None
            )
            nodes = store.list(
                status=parsed_status,
                owner_id=owner_id,
            )

        return [
            task_node_to_dict(node)
            for node in nodes
            if _is_in_root_scope(store, root_task_id, node.task_id)
        ]

    return (create_task, update_task, list_tasks)
