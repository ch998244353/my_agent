import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from agents.planning import (
    PLAN_DEFINITION_SCHEMA_VERSION,
    PlanDefinition,
    PlanExecution,
    PlanStepSpec,
    PlanValidationError,
    _topological_steps,
    build_plan_step_agent,
    build_planning_agent,
    plan_definition_from_dict,
    plan_definition_to_dict,
    render_plan_markdown,
)
from agents.memory import AgentMemory
from agents.task_events import task_event_from_dict, task_event_to_dict
from agents.task_state import (
    TaskConflictError,
    TaskNotFoundError,
    TaskSpec,
    TaskStatus,
    TaskStore,
)
from agents.workspace_manifest import WorkspaceManifest


class PlanStepSpecTests(unittest.TestCase):
    def step(self, **overrides: object) -> PlanStepSpec:
        values: dict[str, object] = {
            "step_id": "inspect",
            "title": "Inspect the repository",
            "instruction": "Read the directly related source files.",
            "completion_criteria": "The current call chain is documented.",
        }
        values.update(overrides)
        return PlanStepSpec(**values)

    def test_valid_step_normalizes_dependencies_and_is_frozen(self) -> None:
        step = self.step(
            dependency_ids=["read-docs", "read-source"],
            is_final_verification=True,
        )

        self.assertEqual(step.dependency_ids, ("read-docs", "read-source"))
        self.assertIs(step.is_final_verification, True)
        with self.assertRaises(FrozenInstanceError):
            step.title = "Changed"

    def test_dependency_iterable_is_consumed_once_and_keeps_order(self) -> None:
        dependencies = (dependency_id for dependency_id in ("a", "b"))

        step = self.step(dependency_ids=dependencies)

        self.assertEqual(step.dependency_ids, ("a", "b"))

    def test_text_fields_reject_non_strings_empty_and_whitespace(self) -> None:
        cases = [
            ("step_id", None, "invalid_step_id"),
            ("step_id", "", "invalid_step_id"),
            ("step_id", "  ", "invalid_step_id"),
            ("title", 1, "invalid_title"),
            ("title", "", "invalid_title"),
            ("title", "  ", "invalid_title"),
            ("instruction", [], "invalid_instruction"),
            ("instruction", "", "invalid_instruction"),
            ("instruction", "  ", "invalid_instruction"),
            (
                "completion_criteria",
                object(),
                "invalid_completion_criteria",
            ),
            ("completion_criteria", "", "invalid_completion_criteria"),
            ("completion_criteria", "  ", "invalid_completion_criteria"),
        ]

        for field, value, code in cases:
            with self.subTest(field=field, value=value), self.assertRaises(
                PlanValidationError
            ) as raised:
                self.step(**{field: value})
            self.assertEqual(raised.exception.code, code)

    def test_final_flag_requires_a_real_bool(self) -> None:
        for value in (0, 1, "yes", None):
            with self.subTest(value=value), self.assertRaises(
                PlanValidationError
            ) as raised:
                self.step(is_final_verification=value)
            self.assertEqual(raised.exception.code, "invalid_final_flag")
            self.assertEqual(raised.exception.step_id, "inspect")

    def test_dependency_collection_rejects_scalar_inputs(self) -> None:
        for value in (None, 1, "dependency", b"dependency"):
            with self.subTest(value=value), self.assertRaises(
                PlanValidationError
            ) as raised:
                self.step(dependency_ids=value)
            self.assertEqual(raised.exception.code, "invalid_dependency_id")

    def test_dependency_ids_must_be_non_empty_strings(self) -> None:
        for dependency_id in (None, 1, "", "  "):
            with self.subTest(dependency_id=dependency_id), self.assertRaises(
                PlanValidationError
            ) as raised:
                self.step(dependency_ids=[dependency_id])
            self.assertEqual(raised.exception.code, "invalid_dependency_id")
            self.assertEqual(raised.exception.step_id, "inspect")

    def test_duplicate_and_self_dependencies_have_stable_errors(self) -> None:
        cases = [
            (["read", "read"], "duplicate_dependency", "read"),
            (["inspect"], "self_dependency", "inspect"),
        ]

        for dependency_ids, code, dependency_id in cases:
            with self.subTest(code=code), self.assertRaises(
                PlanValidationError
            ) as raised:
                self.step(dependency_ids=dependency_ids)
            self.assertEqual(raised.exception.code, code)
            self.assertEqual(raised.exception.step_id, "inspect")
            self.assertEqual(raised.exception.dependency_id, dependency_id)

    def test_error_exposes_locations_without_echoing_long_text(self) -> None:
        secret_instruction = "private instruction body"
        secret_criteria = "private completion criteria"

        with self.assertRaises(PlanValidationError) as raised:
            self.step(
                instruction=secret_instruction,
                completion_criteria=secret_criteria,
                is_final_verification=1,
            )

        error = raised.exception
        self.assertEqual(str(error), "invalid_final_flag")
        self.assertEqual(error.step_id, "inspect")
        self.assertIsNone(error.dependency_id)
        self.assertNotIn(secret_instruction, str(error))
        self.assertNotIn(secret_criteria, str(error))

    def test_step_does_not_duplicate_execution_state(self) -> None:
        step = self.step()

        for field in ("status", "revision", "result", "error", "owner_id"):
            with self.subTest(field=field):
                self.assertFalse(hasattr(step, field))


class PlanValidationErrorTests(unittest.TestCase):
    def test_error_keeps_only_rule_and_location_fields(self) -> None:
        error = PlanValidationError(
            "duplicate_dependency",
            step_id="build",
            dependency_id="inspect",
        )

        self.assertEqual(str(error), "duplicate_dependency")
        self.assertEqual(error.code, "duplicate_dependency")
        self.assertEqual(error.step_id, "build")
        self.assertEqual(error.dependency_id, "inspect")
        self.assertIsInstance(error, ValueError)


class PlanDefinitionTests(unittest.TestCase):
    def step(
        self,
        step_id: str,
        *,
        dependency_ids: object = (),
        final: bool = False,
    ) -> PlanStepSpec:
        return PlanStepSpec(
            step_id=step_id,
            title=f"Title {step_id}",
            instruction=f"Complete {step_id}.",
            completion_criteria=f"{step_id} is verified.",
            dependency_ids=dependency_ids,
            is_final_verification=final,
        )

    def assert_plan_error(
        self,
        code: str,
        *,
        objective: object = "Ship the change",
        steps: object,
        step_id: str | None = None,
        dependency_id: str | None = None,
    ) -> None:
        with self.assertRaises(PlanValidationError) as raised:
            PlanDefinition(objective=objective, steps=steps)
        self.assertEqual(raised.exception.code, code)
        self.assertEqual(raised.exception.step_id, step_id)
        self.assertEqual(raised.exception.dependency_id, dependency_id)

    def test_minimum_plan_normalizes_steps_and_is_frozen(self) -> None:
        work = self.step("work")
        final = self.step("verify", dependency_ids=["work"], final=True)

        definition = PlanDefinition(
            objective="Ship the change",
            steps=[work, final],
        )

        self.assertEqual(definition.steps, (work, final))
        with self.assertRaises(FrozenInstanceError):
            definition.objective = "Changed"

    def test_twelve_steps_is_the_valid_upper_boundary(self) -> None:
        ordinary = [
            self.step(
                f"step-{index}",
                dependency_ids=(() if index == 1 else (f"step-{index - 1}",)),
            )
            for index in range(1, 12)
        ]
        final = self.step(
            "verify",
            dependency_ids=("step-11",),
            final=True,
        )

        definition = PlanDefinition(
            objective="Complete eleven changes",
            steps=[*ordinary, final],
        )

        self.assertEqual(len(definition.steps), 12)

    def test_zero_one_and_thirteen_steps_are_rejected(self) -> None:
        cases = [
            (),
            (self.step("only"),),
            tuple(self.step(f"step-{index}") for index in range(13)),
        ]

        for steps in cases:
            with self.subTest(count=len(steps)):
                self.assert_plan_error("invalid_step_count", steps=steps)

    def test_objective_must_be_non_empty_text(self) -> None:
        steps = (
            self.step("work"),
            self.step("verify", dependency_ids=("work",), final=True),
        )

        for objective in (None, 1, "", "  "):
            with self.subTest(objective=objective):
                self.assert_plan_error(
                    "invalid_objective",
                    objective=objective,
                    steps=steps,
                )

    def test_steps_must_be_an_iterable_of_step_specs(self) -> None:
        invalid_values = [None, 1, "work", [self.step("work"), object()]]

        for steps in invalid_values:
            with self.subTest(steps=steps):
                self.assert_plan_error("invalid_step_count", steps=steps)

    def test_duplicate_step_ids_are_rejected(self) -> None:
        self.assert_plan_error(
            "duplicate_step_id",
            steps=(
                self.step("same"),
                self.step("same", final=True),
            ),
            step_id="same",
        )

    def test_unknown_dependency_reports_both_locations(self) -> None:
        self.assert_plan_error(
            "unknown_dependency",
            steps=(
                self.step("work", dependency_ids=("missing",)),
                self.step("verify", dependency_ids=("work",), final=True),
            ),
            step_id="work",
            dependency_id="missing",
        )

    def test_forward_dependency_is_valid_and_topology_is_stable(self) -> None:
        first_in_definition = self.step("a", dependency_ids=("b",))
        later_dependency = self.step("b")
        final = self.step("verify", dependency_ids=("a",), final=True)
        definition = PlanDefinition(
            objective="Allow forward dependencies",
            steps=(first_in_definition, later_dependency, final),
        )

        self.assertEqual(
            tuple(step.step_id for step in _topological_steps(definition)),
            ("b", "a", "verify"),
        )

    def test_two_and_three_node_cycles_are_rejected(self) -> None:
        cases = [
            (
                self.step("a", dependency_ids=("b",)),
                self.step("b", dependency_ids=("a",)),
            ),
            (
                self.step("a", dependency_ids=("b",)),
                self.step("b", dependency_ids=("c",)),
                self.step("c", dependency_ids=("a",)),
            ),
        ]

        for steps in cases:
            with self.subTest(size=len(steps)):
                self.assert_plan_error(
                    "dependency_cycle",
                    steps=steps,
                    step_id="a",
                )

    def test_final_step_must_exist_once_and_be_last(self) -> None:
        no_final = (self.step("a"), self.step("b"))
        two_finals = (
            self.step("work"),
            self.step("verify-a", dependency_ids=("work",), final=True),
            self.step("verify-b", dependency_ids=("verify-a",), final=True),
        )
        final_not_last = (
            self.step("verify", dependency_ids=("work",), final=True),
            self.step("work"),
        )

        self.assert_plan_error("invalid_final_count", steps=no_final)
        self.assert_plan_error("invalid_final_count", steps=two_finals)
        self.assert_plan_error(
            "final_not_last",
            steps=final_not_last,
            step_id="verify",
        )

    def test_final_must_depend_on_exactly_all_ordinary_leaves(self) -> None:
        invalid_plans = [
            (
                self.step("a"),
                self.step("b", dependency_ids=("a",)),
                self.step("verify", dependency_ids=("a",), final=True),
            ),
            (
                self.step("root"),
                self.step("left", dependency_ids=("root",)),
                self.step("right", dependency_ids=("root",)),
                self.step("verify", dependency_ids=("left",), final=True),
            ),
            (
                self.step("root"),
                self.step("leaf", dependency_ids=("root",)),
                self.step(
                    "verify",
                    dependency_ids=("root", "leaf"),
                    final=True,
                ),
            ),
        ]

        for steps in invalid_plans:
            with self.subTest(step_ids=[step.step_id for step in steps]):
                self.assert_plan_error(
                    "final_dependencies_mismatch",
                    steps=steps,
                    step_id="verify",
                )

    def test_diamond_plan_is_valid(self) -> None:
        definition = PlanDefinition(
            objective="Complete both branches",
            steps=(
                self.step("root"),
                self.step("left", dependency_ids=("root",)),
                self.step("right", dependency_ids=("root",)),
                self.step("merge", dependency_ids=("left", "right")),
                self.step("verify", dependency_ids=("merge",), final=True),
            ),
        )

        self.assertEqual(
            tuple(step.step_id for step in _topological_steps(definition)),
            ("root", "left", "right", "merge", "verify"),
        )

    def test_ready_ties_keep_definition_order(self) -> None:
        definition = PlanDefinition(
            objective="Keep the chosen display order",
            steps=(
                self.step("z-first"),
                self.step("a-second"),
                self.step(
                    "verify",
                    dependency_ids=("a-second", "z-first"),
                    final=True,
                ),
            ),
        )

        expected = ("z-first", "a-second", "verify")
        for _ in range(3):
            self.assertEqual(
                tuple(step.step_id for step in _topological_steps(definition)),
                expected,
            )


class PlanJsonCodecTests(unittest.TestCase):
    def definition(self) -> PlanDefinition:
        return PlanDefinition(
            objective="Ship the change",
            steps=(
                PlanStepSpec(
                    step_id="work",
                    title="Implement",
                    instruction="Implement the requested behavior.",
                    completion_criteria="The behavior is covered.",
                ),
                PlanStepSpec(
                    step_id="verify",
                    title="Verify",
                    instruction="Run the scoped checks.",
                    completion_criteria="All checks pass.",
                    dependency_ids=("work",),
                    is_final_verification=True,
                ),
            ),
        )

    def test_to_dict_has_exact_json_shape(self) -> None:
        payload = plan_definition_to_dict(self.definition())

        self.assertEqual(
            payload,
            {
                "schema_version": 1,
                "objective": "Ship the change",
                "steps": [
                    {
                        "step_id": "work",
                        "title": "Implement",
                        "instruction": "Implement the requested behavior.",
                        "completion_criteria": "The behavior is covered.",
                        "dependency_ids": [],
                        "is_final_verification": False,
                    },
                    {
                        "step_id": "verify",
                        "title": "Verify",
                        "instruction": "Run the scoped checks.",
                        "completion_criteria": "All checks pass.",
                        "dependency_ids": ["work"],
                        "is_final_verification": True,
                    },
                ],
            },
        )
        json.dumps(payload, ensure_ascii=False, allow_nan=False)

    def test_round_trip_restores_equal_minimum_and_diamond_plans(self) -> None:
        minimum = self.definition()
        diamond = PlanDefinition(
            objective="汇总两个分支",
            steps=(
                PlanStepSpec("root", "根", "开始", "完成"),
                PlanStepSpec("left", "左", "左分支", "完成", ("root",)),
                PlanStepSpec("right", "右", "右分支", "完成", ("root",)),
                PlanStepSpec(
                    "merge",
                    "合并",
                    "合并 `left` 与 `right`",
                    "两个分支已合并",
                    ("left", "right"),
                ),
                PlanStepSpec(
                    "verify",
                    "验证",
                    "最终检查",
                    "全部通过",
                    ("merge",),
                    True,
                ),
            ),
        )

        for definition in (minimum, diamond):
            with self.subTest(objective=definition.objective):
                payload = plan_definition_to_dict(definition)
                self.assertEqual(plan_definition_from_dict(payload), definition)
                self.assertEqual(
                    plan_definition_to_dict(plan_definition_from_dict(payload)),
                    payload,
                )

    def test_output_lists_do_not_mutate_definition(self) -> None:
        definition = self.definition()
        payload = plan_definition_to_dict(definition)
        steps = payload["steps"]
        self.assertIsInstance(steps, list)
        dependencies = steps[1]["dependency_ids"]
        self.assertIsInstance(dependencies, list)

        dependencies.append("changed")
        steps.append(dict(steps[0]))

        self.assertEqual(definition.steps[1].dependency_ids, ("work",))
        self.assertEqual(len(definition.steps), 2)

    def test_top_level_fields_must_match_exactly(self) -> None:
        payload = plan_definition_to_dict(self.definition())

        for field in tuple(payload):
            invalid = dict(payload)
            del invalid[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                plan_definition_from_dict(invalid)

        with self.assertRaises(ValueError):
            plan_definition_from_dict({**payload, "status": "pending"})

    def test_step_fields_must_match_exactly(self) -> None:
        payload = plan_definition_to_dict(self.definition())
        step = payload["steps"][0]

        for field in tuple(step):
            invalid_step = dict(step)
            del invalid_step[field]
            invalid = {**payload, "steps": [invalid_step, payload["steps"][1]]}
            with self.subTest(missing=field), self.assertRaises(ValueError):
                plan_definition_from_dict(invalid)

        invalid_step = {**step, "revision": 1}
        with self.assertRaises(ValueError):
            plan_definition_from_dict(
                {**payload, "steps": [invalid_step, payload["steps"][1]]}
            )

    def test_schema_version_is_strict(self) -> None:
        payload = plan_definition_to_dict(self.definition())
        self.assertEqual(PLAN_DEFINITION_SCHEMA_VERSION, 1)

        for version in (True, "1"):
            with self.subTest(version=version), self.assertRaises(TypeError):
                plan_definition_from_dict({**payload, "schema_version": version})
        for version in (0, 2):
            with self.subTest(version=version), self.assertRaises(ValueError):
                plan_definition_from_dict({**payload, "schema_version": version})

    def test_steps_and_dependency_ids_must_be_json_lists(self) -> None:
        payload = plan_definition_to_dict(self.definition())

        for steps in (tuple(payload["steps"]), {}, "steps"):
            with self.subTest(steps=steps), self.assertRaises(TypeError):
                plan_definition_from_dict({**payload, "steps": steps})

        for dependencies in (("work",), "work", None):
            final_step = {
                **payload["steps"][1],
                "dependency_ids": dependencies,
            }
            with self.subTest(dependencies=dependencies), self.assertRaises(
                TypeError
            ):
                plan_definition_from_dict(
                    {**payload, "steps": [payload["steps"][0], final_step]}
                )

    def test_json_field_types_are_strict(self) -> None:
        payload = plan_definition_to_dict(self.definition())

        with self.assertRaises(TypeError):
            plan_definition_from_dict({**payload, "objective": 1})
        with self.assertRaises(TypeError):
            plan_definition_from_dict(
                {
                    **payload,
                    "steps": [
                        payload["steps"][0],
                        {**payload["steps"][1], "is_final_verification": 1},
                    ],
                }
            )
        with self.assertRaises(TypeError):
            plan_definition_from_dict(
                {
                    **payload,
                    "steps": [
                        payload["steps"][0],
                        {
                            **payload["steps"][1],
                            "dependency_ids": [1],
                        },
                    ],
                }
            )

    def test_restored_values_repeat_domain_validation(self) -> None:
        payload = plan_definition_to_dict(self.definition())
        invalid_final = {
            **payload["steps"][1],
            "dependency_ids": [],
        }

        with self.assertRaises(PlanValidationError) as raised:
            plan_definition_from_dict(
                {**payload, "steps": [payload["steps"][0], invalid_final]}
            )
        self.assertEqual(raised.exception.code, "final_dependencies_mismatch")

    def test_codec_rejects_wrong_root_objects(self) -> None:
        with self.assertRaises(TypeError):
            plan_definition_to_dict(object())
        with self.assertRaises(TypeError):
            plan_definition_from_dict([])


class PlanMarkdownRendererTests(unittest.TestCase):
    def definition(self) -> PlanDefinition:
        return PlanDefinition(
            objective="发布中文 `计划` #1",
            steps=(
                PlanStepSpec(
                    step_id="read",
                    title="阅读 `源码`",
                    instruction="第一行\n第二行 # 保留",
                    completion_criteria="找到真实调用链",
                ),
                PlanStepSpec(
                    step_id="verify",
                    title="最终验证",
                    instruction="运行检查",
                    completion_criteria="中文与 `反引号` 完整保留",
                    dependency_ids=("read",),
                    is_final_verification=True,
                ),
            ),
        )

    def test_renderer_has_exact_stable_shape(self) -> None:
        rendered = render_plan_markdown(self.definition())

        self.assertEqual(
            rendered,
            "# 发布中文 `计划` #1\n\n"
            "## 1. 阅读 `源码` (`read`)\n\n"
            "- 依赖：无\n"
            "- 最终验证：否\n\n"
            "### 要做什么\n\n"
            "第一行\n第二行 # 保留\n\n"
            "### 完成标准\n\n"
            "找到真实调用链\n\n"
            "## 2. 最终验证 (`verify`)\n\n"
            "- 依赖：`read`\n"
            "- 最终验证：是\n\n"
            "### 要做什么\n\n"
            "运行检查\n\n"
            "### 完成标准\n\n"
            "中文与 `反引号` 完整保留\n",
        )

    def test_renderer_keeps_definition_order_and_multiple_dependencies(self) -> None:
        definition = PlanDefinition(
            objective="Forward order",
            steps=(
                PlanStepSpec("a", "A", "A", "A", ("b",)),
                PlanStepSpec("b", "B", "B", "B"),
                PlanStepSpec("c", "C", "C", "C"),
                PlanStepSpec("verify", "V", "V", "V", ("a", "c"), True),
            ),
        )

        rendered = render_plan_markdown(definition)

        self.assertLess(rendered.index("## 1. A"), rendered.index("## 2. B"))
        self.assertIn("- 依赖：`a`, `c`", rendered)

    def test_renderer_is_deterministic_and_has_no_execution_state(self) -> None:
        definition = self.definition()

        first = render_plan_markdown(definition)
        second = render_plan_markdown(definition)

        self.assertEqual(first, second)
        self.assertTrue(first.endswith("\n"))
        self.assertFalse(first.endswith("\n\n"))
        for field in ("status", "revision", "result", "owner_id"):
            self.assertNotIn(field, first)

    def test_renderer_rejects_non_definition(self) -> None:
        with self.assertRaises(TypeError):
            render_plan_markdown(object())


class PlanExecutionTests(unittest.TestCase):
    def definition(self) -> PlanDefinition:
        return PlanDefinition(
            objective="Run the plan",
            steps=(
                PlanStepSpec(
                    "work",
                    "Implement",
                    "private long instruction",
                    "private completion criteria",
                ),
                PlanStepSpec(
                    "verify",
                    "Verify",
                    "Run checks",
                    "All checks pass",
                    ("work",),
                    True,
                ),
            ),
        )

    def forward_definition(self) -> PlanDefinition:
        return PlanDefinition(
            objective="Support forward dependencies",
            steps=(
                PlanStepSpec("a", "A", "A", "A", ("b",)),
                PlanStepSpec("b", "B", "B", "B"),
                PlanStepSpec("c", "C", "C", "C"),
                PlanStepSpec("verify", "V", "V", "V", ("a", "c"), True),
            ),
        )

    def test_materialize_creates_a_fresh_store_with_exact_nodes(self) -> None:
        definition = self.definition()

        first = PlanExecution.materialize(definition, owner_id="agent-1")
        second = PlanExecution.materialize(definition, owner_id="agent-1")

        self.assertIsNot(first.task_store, second.task_store)
        self.assertIs(first.definition, definition)
        self.assertEqual(
            tuple(node.task_id for node in first.task_store.list()),
            ("work", "verify"),
        )
        self.assertEqual(len(first.task_store.list()), len(definition.steps))
        self.assertNotIn(
            "plan_root",
            {node.task_id for node in first.task_store.list()},
        )

    def test_materialized_fields_follow_the_thin_mapping(self) -> None:
        execution = PlanExecution.materialize(
            self.definition(),
            owner_id="agent-1",
        )

        work = execution.task_for_step("work")
        verify = execution.task_for_step("verify")
        self.assertEqual(work.summary, "Implement")
        self.assertEqual(work.task_id, "work")
        self.assertEqual(work.dependency_ids, ())
        self.assertEqual(verify.dependency_ids, ("work",))
        for node in (work, verify):
            self.assertEqual(node.owner_id, "agent-1")
            self.assertIsNone(node.parent_id)
            self.assertFalse(hasattr(node, "instruction"))
            self.assertFalse(hasattr(node, "completion_criteria"))

    def test_materialize_validates_inputs_without_accepting_a_store(self) -> None:
        definition = self.definition()

        with self.assertRaises(TypeError):
            PlanExecution.materialize(object(), owner_id="agent-1")
        cases = (
            (None, TypeError),
            (1, TypeError),
            ("", ValueError),
            ("  ", ValueError),
        )
        for owner_id, error_type in cases:
            with self.subTest(owner_id=owner_id), self.assertRaises(error_type):
                PlanExecution.materialize(definition, owner_id=owner_id)
        with self.assertRaises(TypeError):
            PlanExecution.materialize(
                definition,
                owner_id="agent-1",
                task_store=TaskStore(),
            )

    def test_forward_dependencies_are_created_in_stable_topology(self) -> None:
        execution = PlanExecution.materialize(
            self.forward_definition(),
            owner_id="agent-1",
        )

        self.assertEqual(
            tuple(node.task_id for node in execution.task_store.list()),
            ("b", "a", "c", "verify"),
        )
        self.assertEqual(execution.task_for_step("a").dependency_ids, ("b",))

    def test_step_and_task_lookup_use_the_shared_identifier(self) -> None:
        execution = PlanExecution.materialize(
            self.definition(),
            owner_id="agent-1",
        )

        self.assertEqual(execution.task_for_step("work").task_id, "work")
        self.assertEqual(execution.step_for_task("work").step_id, "work")
        with self.assertRaises(TaskNotFoundError):
            execution.task_for_step("missing")
        with self.assertRaises(TaskNotFoundError):
            execution.step_for_task("missing")

    def test_next_ready_reads_store_state_in_definition_order(self) -> None:
        execution = PlanExecution.materialize(
            self.forward_definition(),
            owner_id="agent-1",
        )
        store = execution.task_store

        self.assertEqual(execution.next_ready().task_id, "b")
        b = store.transition("b", expected_revision=1, to_status=TaskStatus.RUNNING)
        store.transition(
            "b",
            expected_revision=b.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        self.assertEqual(execution.next_ready().task_id, "a")

    def test_execution_reads_latest_status_result_and_revision_from_store(self) -> None:
        execution = PlanExecution.materialize(
            self.definition(),
            owner_id="agent-1",
        )
        store = execution.task_store

        running = store.transition(
            "work",
            expected_revision=1,
            to_status=TaskStatus.RUNNING,
        )
        completed = store.transition(
            "work",
            expected_revision=running.revision,
            to_status=TaskStatus.COMPLETED,
            result={"changed": True},
        )

        current = execution.task_for_step("work")
        self.assertEqual(current, completed)
        self.assertEqual(current.revision, 3)
        self.assertEqual(current.result, {"changed": True})
        self.assertEqual(execution.next_ready().task_id, "verify")
        for field in ("status", "revision", "result", "error"):
            self.assertFalse(hasattr(execution, field))

    def test_store_keeps_cas_and_dependency_failure_rules(self) -> None:
        execution = PlanExecution.materialize(
            self.definition(),
            owner_id="agent-1",
        )
        store = execution.task_store
        running = store.transition(
            "work",
            expected_revision=1,
            to_status=TaskStatus.RUNNING,
        )

        with self.assertRaises(TaskConflictError):
            store.transition(
                "work",
                expected_revision=1,
                to_status=TaskStatus.COMPLETED,
                result="stale",
            )
        store.transition(
            "work",
            expected_revision=running.revision,
            to_status=TaskStatus.FAILED,
            error="failed",
        )

        self.assertEqual(execution.task_for_step("verify").status, TaskStatus.BLOCKED)
        self.assertIsNone(execution.next_ready())

    def test_task_event_json_round_trip_restores_execution_state(self) -> None:
        definition = self.definition()
        original = PlanExecution.materialize(definition, owner_id="agent-1")
        running = original.task_store.transition(
            "work",
            expected_revision=1,
            to_status=TaskStatus.RUNNING,
        )
        original.task_store.transition(
            "work",
            expected_revision=running.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        payload = json.loads(
            json.dumps(
                [task_event_to_dict(event) for event in original.task_store.events()]
            )
        )
        events = (task_event_from_dict(item) for item in payload)

        restored = PlanExecution.from_events(
            definition,
            events,
            owner_id="agent-1",
        )

        self.assertEqual(restored.task_store.list(), original.task_store.list())
        self.assertEqual(restored.task_store.events(), original.task_store.events())
        self.assertEqual(restored.next_ready().task_id, "verify")

    def test_from_events_rejects_missing_and_extra_tasks(self) -> None:
        definition = self.definition()
        original = PlanExecution.materialize(definition, owner_id="agent-1")

        with self.assertRaises(PlanValidationError) as missing:
            PlanExecution.from_events(
                definition,
                original.task_store.events()[:-1],
                owner_id="agent-1",
            )
        self.assertEqual(missing.exception.code, "execution_mismatch")

        original.task_store.create(
            TaskSpec(
                summary="Extra",
                task_id="extra",
                owner_id="agent-1",
            )
        )
        with self.assertRaises(PlanValidationError) as extra:
            PlanExecution.from_events(
                definition,
                original.task_store.events(),
                owner_id="agent-1",
            )
        self.assertEqual(extra.exception.code, "execution_mismatch")

    def test_from_events_rejects_mismatched_node_structure(self) -> None:
        definition = self.definition()
        cases = [
            {
                "work": {"summary": "Wrong", "owner_id": "agent-1"},
                "verify": {
                    "summary": "Verify",
                    "owner_id": "agent-1",
                    "dependency_ids": ("work",),
                },
            },
            {
                "work": {"summary": "Implement", "owner_id": "agent-1"},
                "verify": {"summary": "Verify", "owner_id": "agent-1"},
            },
            {
                "work": {"summary": "Implement", "owner_id": "other"},
                "verify": {
                    "summary": "Verify",
                    "owner_id": "other",
                    "dependency_ids": ("work",),
                },
            },
            {
                "work": {"summary": "Implement", "owner_id": "agent-1"},
                "verify": {
                    "summary": "Verify",
                    "owner_id": "agent-1",
                    "parent_id": "work",
                    "dependency_ids": ("work",),
                },
            },
        ]

        for specs in cases:
            store = TaskStore()
            for task_id, values in specs.items():
                store.create(TaskSpec(task_id=task_id, **values))
            with self.subTest(specs=specs), self.assertRaises(
                PlanValidationError
            ) as raised:
                PlanExecution.from_events(
                    definition,
                    store.events(),
                    owner_id="agent-1",
                )
            self.assertEqual(raised.exception.code, "execution_mismatch")


class PlanningAgentBuilderTests(unittest.TestCase):
    def manifest(self, root: str) -> WorkspaceManifest:
        return WorkspaceManifest(
            root=Path(root),
            default_test_command="python -m pytest tests/test_planning.py",
        )

    def tool_names(self, setup) -> set[str]:
        return {
            spec.name
            for spec in setup.agent.tool_registry.list_specs()
        }

    def test_planning_agent_is_read_only_and_reuses_supplied_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            model = object()
            memory = AgentMemory()
            manifest = self.manifest(root)

            setup = build_planning_agent(
                model=model,
                manifest=manifest,
                memory=memory,
            )

        names = self.tool_names(setup)
        self.assertIs(setup.agent.model, model)
        self.assertIs(setup.agent.memory, memory)
        self.assertEqual(setup.agent.name, "PlanningAgent")
        self.assertIn("complete, verifiable plan", setup.agent.instructions)
        self.assertIn("list_workspace_files", names)
        self.assertIn("read_workspace_file", names)
        self.assertIn("search_workspace_text", names)
        self.assertNotIn("run_shell_command", names)
        self.assertNotIn("run_test_command", names)
        self.assertNotIn("apply_patch", names)
        self.assertIsNone(setup.environment)
        self.assertIs(setup.run_config.context["workspace_manifest"], manifest)

    def test_planning_agent_creates_fresh_run_state_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            model = object()
            manifest = self.manifest(root)
            first = build_planning_agent(model=model, manifest=manifest)
            second = build_planning_agent(model=model, manifest=manifest)

        self.assertIsNot(first.agent, second.agent)
        self.assertIsNot(first.agent.memory, second.agent.memory)
        self.assertIsNot(first.run_config.context, second.run_config.context)

    def test_planning_agent_passes_output_schema_without_write_tools(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            schema = {"type": "object"}
            setup = build_planning_agent(
                model=object(),
                manifest=self.manifest(root),
                output_type=schema,
            )

        self.assertIs(setup.agent.output_type, schema)
        names = self.tool_names(setup)
        self.assertIn("read_workspace_file", names)
        self.assertIn("search_workspace_text", names)
        self.assertNotIn("run_shell_command", names)
        self.assertNotIn("run_test_command", names)
        self.assertNotIn("apply_patch", names)

    def test_plan_step_agent_uses_edit_local_without_task_tools(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            model = object()
            manifest = self.manifest(root)
            first = build_plan_step_agent(model=model, manifest=manifest)
            second = build_plan_step_agent(model=model, manifest=manifest)

        names = self.tool_names(first)
        self.assertIs(first.agent.model, model)
        self.assertEqual(first.agent.name, "PlanStepAgent")
        self.assertIn("single plan step", first.agent.instructions)
        self.assertIn(
            "run_test_command only accepts the manifest test allowlist",
            first.agent.instructions,
        )
        self.assertIn(
            "use run_shell_command and wait for approval",
            first.agent.instructions,
        )
        self.assertIn("read_workspace_file", names)
        self.assertIn("run_shell_command", names)
        self.assertIn("run_test_command", names)
        self.assertIn("apply_patch", names)
        self.assertTrue(
            names.isdisjoint(
                {"create_task", "list_tasks", "get_task", "update_task"}
            )
        )
        self.assertEqual(
            first.run_config.context["workspace_manifest"].default_test_command,
            "python -m pytest tests/test_planning.py",
        )
        self.assertIsNot(first.agent, second.agent)
        self.assertIsNot(first.agent.memory, second.agent.memory)
        self.assertIsNot(first.run_config.context, second.run_config.context)

    def test_plan_step_agent_does_not_accept_shared_memory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(TypeError):
                build_plan_step_agent(
                    model=object(),
                    manifest=self.manifest(root),
                    memory=AgentMemory(),
                )


if __name__ == "__main__":
    unittest.main()
