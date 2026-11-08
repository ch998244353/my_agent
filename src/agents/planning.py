from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .coding_agent import (
    CodingAgentProfile,
    CodingAgentSetup,
    build_coding_agent,
)
from .memory import AgentMemory
from .task_events import TaskEvent, replay_task_events
from .task_state import TaskNode, TaskSpec, TaskStore
from .workspace_manifest import WorkspaceManifest


PLAN_DEFINITION_SCHEMA_VERSION = 1

__all__ = [
    "PLAN_DEFINITION_SCHEMA_VERSION",
    "PlanDefinition",
    "PlanExecution",
    "PlanStepSpec",
    "PlanValidationError",
    "build_plan_step_agent",
    "build_planning_agent",
    "plan_definition_from_dict",
    "plan_definition_to_dict",
    "render_plan_markdown",
]

_PLAN_DEFINITION_FIELDS = frozenset({"schema_version", "objective", "steps"})
_PLAN_STEP_FIELDS = frozenset(
    {
        "step_id",
        "title",
        "instruction",
        "completion_criteria",
        "dependency_ids",
        "is_final_verification",
    }
)

_PLANNING_AGENT_INSTRUCTIONS = """\
Read and search only within the workspace allowed by the manifest.
Record unknown information or ask for clarification instead of guessing.
Produce a complete, verifiable plan whose steps are defined before use.
Stop reading once you have enough evidence; do not repeat the same read or search.
Your final answer must be one JSON object and no other text.
Question example: {"kind":"question","question":"Which result is required?","definition":null}
Plan answers must use {"kind":"plan","question":null,"definition":{...}} and match the supplied JSON Schema.
Do not claim that code was changed when no editing tool is available.
Do not create, claim, or update TaskStore tasks.
"""

_PLAN_STEP_AGENT_INSTRUCTIONS = """\
Execute only the single plan step supplied through Runner for this run.
Read, edit, run shell commands, and test only within the manifest boundaries.
run_test_command only accepts the manifest test allowlist.
For any other test command, use run_shell_command and wait for approval.
Do not start other plan steps or modify TaskStore tasks.
Return the changes made and the verification result for this step.
"""

# 诉调用方违反了哪条计划规则，并保留步骤和依赖位置
class PlanValidationError(ValueError):
    """A stable planning rule failure with optional step locations."""

    def __init__(
        self,
        code: str,
        *,
        step_id: str | None = None,
        dependency_id: str | None = None,
    ) -> None:
        self.code = code
        self.step_id = step_id
        self.dependency_id = dependency_id
        super().__init__(code)


def _is_non_empty_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


# 检查一个输入是不是字典类对象，并且字段集合必须完全相等
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


# 已经确定的计划步骤
@dataclass(frozen=True)
class PlanStepSpec:
    step_id: str # 编号
    title: str # 给人看的短标题
    instruction: str  # 这个步骤具体要做什么
    completion_criteria: str  # 完成标准
    dependency_ids: tuple[str, ...] = ()  # 开始前必须完成的步骤
    is_final_verification: bool = False  # 是不是最终验证步骤

    def __post_init__(self) -> None:
        if not _is_non_empty_text(self.step_id):
            raise PlanValidationError("invalid_step_id")
        if not _is_non_empty_text(self.title):
            raise PlanValidationError("invalid_title", step_id=self.step_id)
        if not _is_non_empty_text(self.instruction):
            raise PlanValidationError("invalid_instruction", step_id=self.step_id)
        if not _is_non_empty_text(self.completion_criteria):
            raise PlanValidationError(
                "invalid_completion_criteria",
                step_id=self.step_id,
            )
        if type(self.is_final_verification) is not bool:
            raise PlanValidationError("invalid_final_flag", step_id=self.step_id)

        if isinstance(self.dependency_ids, (str, bytes)):
            raise PlanValidationError("invalid_dependency_id", step_id=self.step_id)
        try:
            dependency_ids = tuple(self.dependency_ids)
        except TypeError:
            raise PlanValidationError(
                "invalid_dependency_id",
                step_id=self.step_id,
            ) from None


        # 检查依赖是否合法
        seen: set[str] = set()
        for dependency_id in dependency_ids:
            if not _is_non_empty_text(dependency_id):
                raise PlanValidationError(
                    "invalid_dependency_id",
                    step_id=self.step_id,
                    dependency_id=(
                        dependency_id if isinstance(dependency_id, str) else None
                    ),
                )
            if dependency_id in seen:
                raise PlanValidationError(
                    "duplicate_dependency",
                    step_id=self.step_id,
                    dependency_id=dependency_id,
                )
            if dependency_id == self.step_id:
                raise PlanValidationError(
                    "self_dependency",
                    step_id=self.step_id,
                    dependency_id=dependency_id,
                )
            seen.add(dependency_id)

        object.__setattr__(self, "dependency_ids", dependency_ids)


# 保存一份经过确认的完整计划
@dataclass(frozen=True)
class PlanDefinition:
    objective: str # 整份计划要实现的目标
    steps: tuple[PlanStepSpec, ...]  # 步骤节点

    def __post_init__(self) -> None:
        if not _is_non_empty_text(self.objective):
            raise PlanValidationError("invalid_objective")

        if isinstance(self.steps, (str, bytes)):
            raise PlanValidationError("invalid_step_count")
        try:
            steps = tuple(self.steps)
        except TypeError:
            raise PlanValidationError("invalid_step_count") from None
        if any(not isinstance(step, PlanStepSpec) for step in steps):
            raise PlanValidationError("invalid_step_count")
        if not 2 <= len(steps) <= 12:
            raise PlanValidationError("invalid_step_count")

        # 重复编号检查
        steps_by_id: dict[str, PlanStepSpec] = {}
        for step in steps:
            if step.step_id in steps_by_id:
                raise PlanValidationError(
                    "duplicate_step_id",
                    step_id=step.step_id,
                )
            steps_by_id[step.step_id] = step

        for step in steps:
            for dependency_id in step.dependency_ids:
                if dependency_id not in steps_by_id:
                    raise PlanValidationError(
                        "unknown_dependency",
                        step_id=step.step_id,
                        dependency_id=dependency_id,
                    )



        object.__setattr__(self, "steps", steps)  # 修改不可变tulple的值
        _topological_steps(self)

        final_steps = tuple(
            step for step in steps if step.is_final_verification
        )
        if len(final_steps) != 1:
            raise PlanValidationError("invalid_final_count")
        final_step = final_steps[0]
        if final_step is not steps[-1]:
            raise PlanValidationError(
                "final_not_last",
                step_id=final_step.step_id,
            )

        # 这段代码找出所有工作分支的终点
        ordinary_steps = tuple(step for step in steps if step is not final_step)
        ordinary_ids = {step.step_id for step in ordinary_steps}
        depended_on = {
            dependency_id
            for step in ordinary_steps
            for dependency_id in step.dependency_ids
            if dependency_id in ordinary_ids
        }
        ordinary_leaves = ordinary_ids - depended_on
        if set(final_step.dependency_ids) != ordinary_leaves:
            raise PlanValidationError(
                "final_dependencies_mismatch",
                step_id=final_step.step_id,
            )


# 把用户顺序转换成可执行顺序  : 进行拓扑排序
def _topological_steps(
    definition: PlanDefinition,
) -> tuple[PlanStepSpec, ...]:
    steps = definition.steps
    steps_by_id = {step.step_id: step for step in steps}
    positions = {step.step_id: index for index, step in enumerate(steps)}
    indegree = {step.step_id: len(step.dependency_ids) for step in steps}
    dependents: dict[str, list[str]] = {step.step_id: [] for step in steps}
    for step in steps:
        for dependency_id in step.dependency_ids:
            dependents[dependency_id].append(step.step_id)

    ready = [step.step_id for step in steps if indegree[step.step_id] == 0]
    ordered: list[PlanStepSpec] = []
    while ready:
        step_id = ready.pop(0)
        ordered.append(steps_by_id[step_id])
        for dependent_id in dependents[step_id]:
            indegree[dependent_id] -= 1
            if indegree[dependent_id] == 0:
                ready.append(dependent_id)
        ready.sort(key=positions.__getitem__)

    if len(ordered) != len(steps):
        unresolved = next(step for step in steps if indegree[step.step_id] > 0)
        raise PlanValidationError(
            "dependency_cycle",
            step_id=unresolved.step_id,
        )
    return tuple(ordered)


def _validate_execution_inputs(
    definition: PlanDefinition,
    owner_id: str,
) -> None:
    if not isinstance(definition, PlanDefinition):
        raise TypeError("definition must be a PlanDefinition.")
    if not isinstance(owner_id, str):
        raise TypeError("owner_id must be a string.")
    if not owner_id.strip():
        raise ValueError("owner_id must be a non-empty string.")


def _validate_execution_store(
    definition: PlanDefinition,
    store: TaskStore,
    owner_id: str,
) -> None:
    nodes_by_id = {node.task_id: node for node in store.list()}
    step_ids = {step.step_id for step in definition.steps}
    if set(nodes_by_id) != step_ids:
        raise PlanValidationError("execution_mismatch")

    for step in definition.steps:
        node = nodes_by_id[step.step_id]
        if (
            node.summary != step.title
            or node.dependency_ids != step.dependency_ids
            or node.owner_id != owner_id
            or node.parent_id is not None
        ):
            raise PlanValidationError(
                "execution_mismatch",
                step_id=step.step_id,
            )



# 把这个静态计划转换成真正可以执行、可以更新状态、可以判断下一步该跑谁的任务集合
@dataclass(frozen=True, eq=False, kw_only=True)
class PlanExecution:
    definition: PlanDefinition
    _task_store: TaskStore = field(repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.definition, PlanDefinition):
            raise TypeError("definition must be a PlanDefinition.")
        if not isinstance(self._task_store, TaskStore):
            raise TypeError("_task_store must be a TaskStore.")


    # 把合法计划转换成执行节点
    @classmethod
    def materialize(
        cls,
        definition: PlanDefinition,
        *,
        owner_id: str,
    ) -> PlanExecution:
        _validate_execution_inputs(definition, owner_id)
        store = TaskStore()
        for step in _topological_steps(definition):
            store.create(
                TaskSpec(
                    summary=step.title,
                    task_id=step.step_id,
                    dependency_ids=step.dependency_ids,
                    owner_id=owner_id,
                )
            )
        return cls(definition=definition, _task_store=store)


    # 从 typed TaskEvent 恢复 execution
    @classmethod
    def from_events(
        cls,
        definition: PlanDefinition,
        events: Iterable[TaskEvent],
        *,
        owner_id: str,
    ) -> PlanExecution:
        _validate_execution_inputs(definition, owner_id)
        store = replay_task_events(events)
        _validate_execution_store(definition, store, owner_id)
        return cls(definition=definition, _task_store=store)

    @property
    def task_store(self) -> TaskStore:
        return self._task_store

    def task_for_step(self, step_id: str) -> TaskNode:
        return self._task_store.get(step_id)

    def step_for_task(self, task_id: str) -> PlanStepSpec:
        self._task_store.get(task_id)
        for step in self.definition.steps:
            if step.step_id == task_id:
                return step
        raise PlanValidationError("execution_mismatch", step_id=task_id)


    # 从 TaskStore 取得当前真正可以运行的节点，再按 definition 顺序选择第一项。
    def next_ready(self) -> TaskNode | None:
        ready_by_id = {node.task_id: node for node in self._task_store.ready()}
        for step in self.definition.steps:
            node = ready_by_id.get(step.step_id)
            if node is not None:
                return node
        return None


# 把计划明确转换成只含 JSON 原生值的新字典
def plan_definition_to_dict(
    definition: PlanDefinition,
) -> dict[str, object]:
    if not isinstance(definition, PlanDefinition):
        raise TypeError("definition must be a PlanDefinition.")
    return {
        "schema_version": PLAN_DEFINITION_SCHEMA_VERSION,
        "objective": definition.objective,
        "steps": [
            {
                "step_id": step.step_id,
                "title": step.title,
                "instruction": step.instruction,
                "completion_criteria": step.completion_criteria,
                "dependency_ids": list(step.dependency_ids),
                "is_final_verification": step.is_final_verification,
            }
            for step in definition.steps
        ],
    }


# 将json 转回 PlanDefinition
def plan_definition_from_dict(
    data: Mapping[str, object],
) -> PlanDefinition:
    values = _require_fields(
        "PlanDefinition",
        data,
        _PLAN_DEFINITION_FIELDS,
    )

    schema_version = values["schema_version"]
    if type(schema_version) is not int:
        raise TypeError("PlanDefinition.schema_version must be an integer.")
    if schema_version != PLAN_DEFINITION_SCHEMA_VERSION:
        raise ValueError(
            f"Unsupported plan definition schema version: {schema_version!r}."
        )

    objective = values["objective"]
    if type(objective) is not str:
        raise TypeError("PlanDefinition.objective must be a string.")
    step_values = values["steps"]
    if type(step_values) is not list:
        raise TypeError("PlanDefinition.steps must be a list.")

    steps: list[PlanStepSpec] = []
    for index, step_data in enumerate(step_values):
        step = _require_fields(
            f"PlanDefinition.steps[{index}]",
            step_data,
            _PLAN_STEP_FIELDS,
        )
        for field in (
            "step_id",
            "title",
            "instruction",
            "completion_criteria",
        ):
            if type(step[field]) is not str:
                raise TypeError(
                    f"PlanDefinition.steps[{index}].{field} must be a string."
                )

        dependency_ids = step["dependency_ids"]
        if type(dependency_ids) is not list:
            raise TypeError(
                f"PlanDefinition.steps[{index}].dependency_ids must be a list."
            )
        if any(type(dependency_id) is not str for dependency_id in dependency_ids):
            raise TypeError(
                f"PlanDefinition.steps[{index}].dependency_ids must contain strings."
            )

        is_final_verification = step["is_final_verification"]
        if type(is_final_verification) is not bool:
            raise TypeError(
                f"PlanDefinition.steps[{index}].is_final_verification "
                "must be a boolean."
            )
        steps.append(
            PlanStepSpec(
                step_id=step["step_id"],
                title=step["title"],
                instruction=step["instruction"],
                completion_criteria=step["completion_criteria"],
                dependency_ids=dependency_ids,
                is_final_verification=is_final_verification,
            )
        )

    return PlanDefinition(objective=objective, steps=steps)


# 将 PlanDefinition 渲染后输出
def render_plan_markdown(definition: PlanDefinition) -> str:
    if not isinstance(definition, PlanDefinition):
        raise TypeError("definition must be a PlanDefinition.")

    blocks = [f"# {definition.objective}"]
    for index, step in enumerate(definition.steps, start=1):
        dependencies = (
            ", ".join(f"`{dependency_id}`" for dependency_id in step.dependency_ids)
            or "无"
        )
        blocks.append(
            "\n".join(
                (
                    f"## {index}. {step.title} (`{step.step_id}`)",
                    "",
                    f"- 依赖：{dependencies}",
                    f"- 最终验证：{'是' if step.is_final_verification else '否'}",
                    "",
                    "### 要做什么",
                    "",
                    step.instruction,
                    "",
                    "### 完成标准",
                    "",
                    step.completion_criteria,
                )
            )
        )
    return "\n\n".join(blocks) + "\n"


def build_planning_agent(
    *,
    model: Any,
    manifest: WorkspaceManifest,
    memory: AgentMemory | None = None,
    output_type: type[Any] | dict[str, Any] | None = None,
) -> CodingAgentSetup:
    return build_coding_agent(
        model=model,
        manifest=manifest,
        profile=CodingAgentProfile.read_only(
            name="PlanningAgent",
            instructions=_PLANNING_AGENT_INSTRUCTIONS,
        ),
        memory=memory,
        output_type=output_type,
    )


def build_plan_step_agent(
    *,
    model: Any,
    manifest: WorkspaceManifest,
) -> CodingAgentSetup:
    return build_coding_agent(
        model=model,
        manifest=manifest,
        profile=CodingAgentProfile.edit_local(
            name="PlanStepAgent",
            instructions=_PLAN_STEP_AGENT_INSTRUCTIONS,
        ),
    )
