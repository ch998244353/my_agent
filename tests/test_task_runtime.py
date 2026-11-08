from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents.run_loop as run_loop_module  # noqa: E402
from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    ModelResponse,
    RunConfig,
    RunState,
    TaskNotFoundError,
    TaskSpec,
    TaskStatus,
    TaskStore,
    ToolCall,
    ToolRegistry,
    create_task_tools,
    function_tool,
)
from agents.lifecycle import LifecycleHooks  # noqa: E402
from agents.runner import Runner, SyncRunCollector  # noqa: E402


class CountingModel:
    def __init__(self) -> None:
        self.calls = 0

    def get_response(self, messages, tool_specs):
        self.calls += 1
        return ModelResponse(
            response_id="response-1",
            output=[],
            output_text="done",
            tool_calls=[],
        )


class SequenceModel:
    def __init__(self, responses) -> None:
        self.responses = iter(responses)
        self.tool_outputs = []

    def get_response(self, messages, tool_specs):
        return next(self.responses)

    def record_tool_output(self, tool_call, output) -> None:
        self.tool_outputs.append((tool_call, output))


class FailingModel:
    def get_response(self, messages, tool_specs):
        raise RuntimeError("model failed")


class CountingHooks(LifecycleHooks):
    def __init__(self) -> None:
        self.starts = 0

    def on_agent_start(self, context, agent) -> None:
        self.starts += 1


class TransitioningHooks(CountingHooks):
    def __init__(self, store: TaskStore, task_id: str, revision: int) -> None:
        super().__init__()
        self.store = store
        self.task_id = task_id
        self.revision = revision

    def on_agent_start(self, context, agent) -> None:
        super().on_agent_start(context, agent)
        self.store.transition(
            self.task_id,
            expected_revision=self.revision,
            to_status=TaskStatus.RUNNING,
        )


class CountingSession:
    def __init__(self) -> None:
        self.get_calls = 0
        self.add_calls = 0

    def get_items(self, limit=None):
        self.get_calls += 1
        return []

    def add_items(self, items) -> None:
        self.add_calls += 1

    def pop_item(self):
        return None

    def clear_session(self) -> None:
        pass


class TaskRuntimeTestCase(unittest.TestCase):
    def test_runner_entries_preserve_bound_and_unbound_results(self) -> None:
        for bound in (False, True):
            store = TaskStore() if bound else None
            node = (
                store.create(TaskSpec(summary="Runner entry", task_id="task-1"))
                if store is not None
                else None
            )
            config = (
                RunConfig(task_store=store, task_id=node.task_id)
                if store is not None and node is not None
                else None
            )
            expected_link = (
                (node.task_id, node.revision)
                if node is not None
                else (None, None)
            )

            for entry in ("sync", "async", "stream"):
                with self.subTest(bound=bound, entry=entry):
                    model = CountingModel()
                    agent = Agent(memory=AgentMemory(), model=model)
                    if entry == "sync":
                        result = Runner.run_sync(agent, "Run entry.", config)
                    elif entry == "async":
                        result = asyncio.run(
                            Runner.run_async(agent, "Run entry.", config)
                        )
                    else:
                        result = asyncio.run(
                            SyncRunCollector().collect(
                                Runner.run_stream(agent, "Run entry.", config)
                            )
                        )

                    self.assertEqual(
                        (result.task_id, result.task_revision),
                        expected_link,
                    )
                    self.assertEqual(model.calls, 1)

    def test_collector_preserves_bound_result_identity_and_link(self) -> None:
        store = TaskStore()
        node = store.create(TaskSpec(summary="Collected task", task_id="task-1"))
        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=CountingModel()),
            "Collect bound run.",
            RunConfig(task_store=store, task_id=node.task_id),
        )

        result = asyncio.run(SyncRunCollector().collect(stream))

        self.assertIsNotNone(stream._producer_task)
        self.assertIs(result, stream._producer_task.result())
        self.assertEqual(result.task_id, node.task_id)
        self.assertEqual(result.task_revision, node.revision)

    def test_bound_v3_parse_does_not_require_store_but_resume_does(self) -> None:
        restored = RunState.from_snapshot(
            {
                "schema_version": 3,
                "input": "Resume bound snapshot",
                "last_agent_name": None,
                "last_response_id": None,
                "current_turn": 0,
                "steps_taken": 0,
                "max_turns": 0,
                "max_steps": None,
                "tool_approvals": [],
                "model_responses": [],
                "new_items": [],
                "task_id": "task-1",
                "task_revision": 2,
            }
        )

        self.assertEqual(restored.task_id, "task-1")
        self.assertEqual(restored.task_revision, 2)
        with self.assertRaisesRegex(ValueError, "required to resume"):
            run_loop_module.resume_agent_loop(
                Agent(memory=AgentMemory(), model=CountingModel()),
                restored,
            )

    def test_all_terminal_results_use_the_latest_task_revision(self) -> None:
        for terminal in ("completed", "stopped", "model_failure", "approval_pause"):
            with self.subTest(terminal=terminal):
                store = TaskStore()
                node = store.create(
                    TaskSpec(summary=terminal, task_id="task-1")
                )
                hooks = TransitioningHooks(store, node.task_id, node.revision)
                config = RunConfig(
                    task_store=store,
                    task_id=node.task_id,
                    hooks=hooks,
                    max_turns=0 if terminal == "stopped" else None,
                )
                if terminal == "model_failure":
                    agent = Agent(memory=AgentMemory(), model=FailingModel())
                elif terminal == "approval_pause":
                    model = SequenceModel(
                        [
                            ModelResponse(
                                response_id="response-approval",
                                output=[],
                                output_text=None,
                                tool_calls=[
                                    ToolCall(
                                        "delete_file",
                                        {"path": "notes.txt"},
                                        "call-approval",
                                    )
                                ],
                            )
                        ]
                    )

                    def delete_file(path: str) -> str:
                        return f"deleted {path}"

                    agent = Agent(memory=AgentMemory(), model=model)
                    agent.tool_registry.register(
                        function_tool(delete_file, needs_approval=True)
                    )
                else:
                    agent = Agent(memory=AgentMemory(), model=CountingModel())

                result = run_loop_module.run_agent_loop(
                    agent,
                    f"Run until {terminal}",
                    config,
                )
                current = store.get(node.task_id)

                self.assertEqual(result.task_id, current.task_id)
                self.assertEqual(result.task_revision, current.revision)
                later = store.transition(
                    current.task_id,
                    expected_revision=current.revision,
                    to_status=TaskStatus.COMPLETED,
                    result="later",
                )
                self.assertNotEqual(result.task_revision, later.revision)

    def test_bound_planning_result_snapshot_and_resume_share_one_store(self) -> None:
        store = TaskStore()
        node = store.create(
            TaskSpec(
                summary="Bound planning task",
                task_id="task-1",
                owner_id="planner",
            )
        )
        model = SequenceModel(
            [
                ModelResponse(
                    response_id="response-update",
                    output=[],
                    output_text=None,
                    tool_calls=[
                        ToolCall(
                            "update_task",
                            {
                                "task_id": node.task_id,
                                "expected_revision": node.revision,
                                "status": "running",
                            },
                            "call-update",
                        )
                    ],
                ),
                ModelResponse(
                    response_id="response-final",
                    output=[],
                    output_text="done",
                    tool_calls=[],
                ),
            ]
        )
        registry = ToolRegistry()
        for tool in create_task_tools(
            store,
            root_task_id=node.task_id,
            owner_id="planner",
        ):
            registry.register(tool)

        result = run_loop_module.run_agent_loop(
            Agent(memory=AgentMemory(), model=model, tool_registry=registry),
            "Update the bound task",
            RunConfig(task_store=store, task_id=node.task_id),
        )
        current = store.get(node.task_id)

        self.assertEqual(current.status, TaskStatus.RUNNING)
        self.assertEqual(result.task_id, current.task_id)
        self.assertEqual(result.task_revision, current.revision)

        snapshot = result.to_state()
        restored = RunState.from_snapshot(snapshot)
        completed = store.transition(
            current.task_id,
            expected_revision=current.revision,
            to_status=TaskStatus.COMPLETED,
            result="done",
        )
        resume_model = CountingModel()
        resumed_result = run_loop_module.resume_agent_loop(
            Agent(memory=AgentMemory(), model=resume_model),
            restored,
            RunConfig(
                task_store=store,
                task_id=current.task_id,
                max_turns=restored.current_turn,
            ),
        )

        self.assertEqual(snapshot["schema_version"], 4)
        self.assertEqual(snapshot["task_id"], current.task_id)
        self.assertEqual(snapshot["task_revision"], current.revision)
        self.assertEqual(restored.task_id, completed.task_id)
        self.assertEqual(restored.task_revision, completed.revision)
        self.assertEqual(resumed_result.task_id, completed.task_id)
        self.assertEqual(resumed_result.task_revision, completed.revision)
        self.assertEqual(resume_model.calls, 0)

    def test_final_refresh_failure_does_not_save_session(self) -> None:
        store = TaskStore()
        node = store.create(TaskSpec(summary="Vanishing", task_id="task-1"))

        class VanishingStore:
            def __init__(self) -> None:
                self.calls = 0

            def get(self, task_id):
                self.calls += 1
                if self.calls == 1:
                    return store.get(task_id)
                raise TaskNotFoundError(task_id)

        vanishing_store = VanishingStore()
        session = CountingSession()

        with self.assertRaises(TaskNotFoundError):
            run_loop_module.run_agent_loop(
                Agent(memory=AgentMemory(), model=CountingModel()),
                "Finish after the node disappears",
                RunConfig(
                    task_store=vanishing_store,
                    task_id=node.task_id,
                    session=session,
                ),
            )

        self.assertEqual(vanishing_store.calls, 2)
        self.assertEqual(session.add_calls, 0)

    def test_fresh_runs_create_unbound_or_bound_run_state(self) -> None:
        store = TaskStore()
        node = store.create(TaskSpec(summary="Bound run", task_id="task-1"))
        captured_links: list[tuple[str | None, int | None]] = []
        build_run_result = run_loop_module.build_run_result

        def capture_link(run_state: RunState):
            captured_links.append((run_state.task_id, run_state.task_revision))
            return build_run_result(run_state)

        with patch.object(run_loop_module, "build_run_result", side_effect=capture_link):
            run_loop_module.run_agent_loop(
                Agent(memory=AgentMemory(), model=CountingModel()),
                "Unbound run",
                RunConfig(max_turns=0),
            )
            run_loop_module.run_agent_loop(
                Agent(memory=AgentMemory(), model=CountingModel()),
                "Bound run",
                RunConfig(
                    max_turns=0,
                    task_store=store,
                    task_id=node.task_id,
                ),
            )

        self.assertEqual(captured_links, [(None, None), (node.task_id, node.revision)])

    def test_unknown_fresh_task_fails_before_business_side_effects(self) -> None:
        memory = AgentMemory()
        model = CountingModel()
        hooks = CountingHooks()
        session = CountingSession()
        config = RunConfig(
            task_store=TaskStore(),
            task_id="missing",
            hooks=hooks,
            session=session,
        )

        with self.assertRaises(TaskNotFoundError):
            run_loop_module.run_agent_loop(
                Agent(memory=memory, model=model),
                "Must not start",
                config,
            )

        self.assertIsNone(memory.task)
        self.assertEqual(model.calls, 0)
        self.assertEqual(hooks.starts, 0)
        self.assertEqual(session.get_calls, 0)
        self.assertEqual(session.add_calls, 0)

    def test_bound_resume_requires_matching_store_and_task_id(self) -> None:
        store = TaskStore()
        expected = store.create(TaskSpec(summary="Expected", task_id="expected"))
        other = store.create(TaskSpec(summary="Other", task_id="other"))
        run_state = RunState(
            input="Resume",
            max_turns=0,
            task_id=expected.task_id,
            task_revision=expected.revision,
        )
        model = CountingModel()
        hooks = CountingHooks()
        session = CountingSession()

        with self.subTest("missing store"):
            with self.assertRaisesRegex(ValueError, "required to resume"):
                run_loop_module.resume_agent_loop(
                    Agent(memory=AgentMemory(), model=model),
                    run_state,
                    RunConfig(hooks=hooks, session=session),
                )
        with self.subTest("different task id"):
            with self.assertRaisesRegex(ValueError, "must match"):
                run_loop_module.resume_agent_loop(
                    Agent(memory=AgentMemory(), model=model),
                    run_state,
                    RunConfig(
                        task_store=store,
                        task_id=other.task_id,
                        hooks=hooks,
                        session=session,
                    ),
                )
        with self.subTest("unknown task"):
            with self.assertRaises(TaskNotFoundError):
                run_loop_module.resume_agent_loop(
                    Agent(memory=AgentMemory(), model=model),
                    run_state,
                    RunConfig(
                        task_store=TaskStore(),
                        task_id=expected.task_id,
                        hooks=hooks,
                        session=session,
                    ),
                )

        self.assertEqual(model.calls, 0)
        self.assertEqual(hooks.starts, 0)
        self.assertEqual(session.get_calls, 0)
        self.assertEqual(session.add_calls, 0)

    def test_bound_resume_refreshes_task_revision(self) -> None:
        store = TaskStore()
        node = store.create(TaskSpec(summary="Resume", task_id="task-1"))
        run_state = RunState(
            input="Resume",
            max_turns=0,
            task_id=node.task_id,
            task_revision=node.revision,
        )
        updated = store.transition(
            node.task_id,
            expected_revision=node.revision,
            to_status=TaskStatus.RUNNING,
        )

        run_loop_module.resume_agent_loop(
            Agent(memory=AgentMemory(), model=CountingModel()),
            run_state,
            RunConfig(task_store=store, task_id=node.task_id),
        )

        self.assertEqual(run_state.task_id, updated.task_id)
        self.assertEqual(run_state.task_revision, updated.revision)

    def test_runner_resume_keeps_task_binding_checks_and_current_revision(self) -> None:
        store = TaskStore()
        expected = store.create(TaskSpec(summary="Expected", task_id="expected"))
        other = store.create(TaskSpec(summary="Other", task_id="other"))
        run_state = RunState(
            input="Resume",
            max_turns=0,
            task_id=expected.task_id,
            task_revision=expected.revision,
        )
        model = CountingModel()

        with self.assertRaisesRegex(ValueError, "must match"):
            Runner.resume_sync(
                Agent(memory=AgentMemory(), model=model),
                run_state,
                RunConfig(task_store=store, task_id=other.task_id),
            )

        updated = store.transition(
            expected.task_id,
            expected_revision=expected.revision,
            to_status=TaskStatus.RUNNING,
        )
        result = Runner.resume_sync(
            Agent(memory=AgentMemory(), model=model),
            run_state,
            RunConfig(task_store=store, task_id=expected.task_id),
        )

        self.assertEqual(model.calls, 0)
        self.assertEqual(result.task_id, updated.task_id)
        self.assertEqual(result.task_revision, updated.revision)

    def test_unbound_resume_can_stay_unbound_or_start_binding(self) -> None:
        unbound_state = RunState(input="Resume", max_turns=0)
        run_loop_module.resume_agent_loop(
            Agent(memory=AgentMemory(), model=CountingModel()),
            unbound_state,
        )
        self.assertIsNone(unbound_state.task_id)
        self.assertIsNone(unbound_state.task_revision)

        store = TaskStore()
        node = store.create(TaskSpec(summary="Bind later", task_id="task-1"))
        bound_state = RunState(input="Resume", max_turns=0)
        run_loop_module.resume_agent_loop(
            Agent(memory=AgentMemory(), model=CountingModel()),
            bound_state,
            RunConfig(task_store=store, task_id=node.task_id),
        )
        self.assertEqual(bound_state.task_id, node.task_id)
        self.assertEqual(bound_state.task_revision, node.revision)


if __name__ == "__main__":
    unittest.main()
