from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    AgentSession,
    CodingAgentProfile,
    FunctionTool,
    GuardrailFunctionOutput,
    ModelResponse,
    RunConfig,
    RunState,
    Runner,
    StepRecord,
    ToolArgument,
    ToolCall,
    ToolGuardrailFunctionOutput,
    ToolRegistry,
    ToolSpec,
    output_guardrail,
    tool_input_guardrail,
)
import agents.run_loop as run_loop_module  # noqa: E402
from agents.coding_agent import build_coding_agent  # noqa: E402
from agents.events import RunCancelledError  # noqa: E402
from agents.runner import SyncRunCollector  # noqa: E402
from agents.tracing import (  # noqa: E402
    BatchTraceProcessor,
    InMemoryTracingExporter,
    set_trace_processors,
)


class ScriptedModel:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self.last_messages = []
        self.message_batches = []
        self.last_tool_specs = []
        self._index = 0

    def decide(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.message_batches.append(list(messages))
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        return action


class ScriptedResponseModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.last_messages = []
        self.last_tool_specs = []
        self._index = 0

    def get_response(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.responses):
            return ModelResponse(
                response_id=None,
                output=[],
                output_text=None,
                tool_calls=[],
            )
        response = self.responses[self._index]
        self._index += 1
        return response


def echo_tool() -> FunctionTool:
    return FunctionTool(
        spec=ToolSpec(
            name="echo_text",
            description="Return the same text.",
            arguments=[
                ToolArgument(
                    name="text",
                    description="Input text.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        ),
        handler=lambda text: text,
    )


class RunnerTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        set_trace_processors([])

    def test_runner_run_sync_executes_agent_loop(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "runner finished"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        result = Runner.run_sync(agent, "Finish through Runner.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "runner finished")
        self.assertEqual(result.steps_taken, 1)
        self.assertEqual(result.input, "Finish through Runner.")
        self.assertIs(result.last_agent, agent)
        self.assertEqual(model.last_messages[0].content, "Finish through Runner.")

    def test_runner_resume_stream_is_lazy_and_uses_original_input(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "resumed"},
                    call_id="call_resume",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)
        run_state = RunState(input="Resume this run.")

        stream = Runner.resume_stream(agent, run_state)

        self.assertEqual(model._index, 0)
        result = asyncio.run(SyncRunCollector().collect(stream))
        self.assertEqual(model._index, 1)
        self.assertEqual(result.input, "Resume this run.")
        self.assertEqual(result.final_answer, "resumed")

    def test_runner_resume_async_and_sync_return_original_results(self) -> None:
        async_model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "async resumed"}, "call_async")]
        )
        sync_model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "sync resumed"}, "call_sync")]
        )

        async_result = asyncio.run(
            Runner.resume_async(
                Agent(memory=AgentMemory(), model=async_model),
                RunState(input="Resume asynchronously."),
            )
        )
        sync_result = Runner.resume_sync(
            Agent(memory=AgentMemory(), model=sync_model),
            RunState(input="Resume synchronously."),
        )

        self.assertEqual(async_result.final_answer, "async resumed")
        self.assertEqual(sync_result.final_answer, "sync resumed")

    def test_runner_resume_sync_rejects_a_running_event_loop(self) -> None:
        async def scenario() -> None:
            with self.assertRaisesRegex(RuntimeError, "resume_async"):
                Runner.resume_sync(
                    Agent(memory=AgentMemory(), model=ScriptedResponseModel([])),
                    RunState(input="Resume."),
                )

        asyncio.run(scenario())

    def test_runner_resume_stream_rejects_invalid_state_immediately(self) -> None:
        agent = Agent(memory=AgentMemory(), model=ScriptedResponseModel([]))

        with self.assertRaisesRegex(TypeError, "run_state must be a RunState"):
            Runner.resume_stream(agent, object())  # type: ignore[arg-type]

    def test_runner_approval_resume_executes_the_pending_tool_once(self) -> None:
        calls: list[str] = []

        def delete_file(path: str) -> str:
            calls.append(path)
            return f"deleted {path}"

        registry = ToolRegistry()
        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="delete_file",
                    description="Delete a file.",
                    arguments=[
                        ToolArgument(
                            name="path",
                            description="File path.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="string",
                ),
                handler=delete_file,
                needs_approval=True,
            )
        )
        model = ScriptedModel(
            [
                ToolCall("delete_file", {"path": "notes.txt"}, "call_delete"),
                ToolCall("final_answer", {"answer": "deleted"}, "call_done"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)
        first_result = Runner.run_sync(agent, "Delete notes.txt.")
        run_state = RunState.from_snapshot(first_result.to_state(), agent=agent)
        run_state.approve_tool_call("delete_file", "call_delete")

        resumed_result = Runner.resume_sync(agent, run_state)

        self.assertEqual(calls, ["notes.txt"])
        self.assertEqual(model._index, 2)
        self.assertTrue(resumed_result.reached_final_answer)
        self.assertEqual(resumed_result.final_answer, "deleted")

    def test_sync_run_collector_returns_the_producer_result(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "collected"},
                    call_id="call_collect",
                )
            ]
        )
        stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=model),
            "Collect this run.",
        )
        collector = SyncRunCollector()

        result = asyncio.run(collector.collect(stream))

        self.assertEqual(vars(collector), {})
        self.assertIsNotNone(stream._producer_task)
        self.assertIs(result, stream._producer_task.result())
        self.assertEqual(result.final_answer, "collected")
        self.assertEqual(model._index, 1)

    def test_runner_run_async_delegates_to_sync_run_collector_once(self) -> None:
        model = ScriptedResponseModel([])
        agent = Agent(memory=AgentMemory(), model=model)
        config = RunConfig(max_turns=0)
        stream = Runner.run_stream(agent, "Delegate.", config)
        expected = object()
        collected = []

        async def collect_once(self, received_stream):
            collected.append(received_stream)
            return expected

        with patch.object(
            Runner,
            "run_stream",
            classmethod(lambda cls, agent, task, config=None: stream),
        ), patch.object(SyncRunCollector, "collect", collect_once):
            result = asyncio.run(Runner.run_async(agent, "Delegate.", config))

        self.assertIs(result, expected)
        self.assertEqual(collected, [stream])
        self.assertEqual(model._index, 0)

    def test_sync_run_collector_preserves_terminal_results(self) -> None:
        class FailingModel:
            def get_response(self, messages, tool_specs):
                raise ValueError("bad response")

        approval_registry = ToolRegistry()
        approval_registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="delete_file",
                    description="Delete a file.",
                    arguments=[
                        ToolArgument(
                            name="path",
                            description="File path.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="string",
                ),
                handler=lambda path: path,
                needs_approval=True,
            )
        )

        async def scenario():
            collector = SyncRunCollector()
            stopped = await collector.collect(
                Runner.run_stream(
                    Agent(memory=AgentMemory(), model=ScriptedResponseModel([])),
                    "Stop.",
                    RunConfig(max_turns=0),
                )
            )
            failed = await collector.collect(
                Runner.run_stream(
                    Agent(memory=AgentMemory(), model=FailingModel()),
                    "Fail.",
                )
            )
            approval = await collector.collect(
                Runner.run_stream(
                    Agent(
                        memory=AgentMemory(),
                        model=ScriptedModel(
                            [
                                ToolCall(
                                    tool_name="delete_file",
                                    arguments={"path": "notes.txt"},
                                    call_id="call_approval",
                                )
                            ]
                        ),
                        tool_registry=approval_registry,
                    ),
                    "Delete notes.",
                )
            )
            return stopped, failed, approval

        stopped, failed, approval = asyncio.run(scenario())

        self.assertEqual(stopped.new_items[-1].item_type, "run_stopped")
        self.assertEqual(failed.new_items[-1].item_type, "model_error")
        self.assertTrue(approval.has_pending_approvals)

    def test_sync_run_collector_preserves_stream_errors_and_cancellation(self) -> None:
        claimed_stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=ScriptedResponseModel([])),
            "Already claimed.",
            RunConfig(max_turns=0),
        )
        claimed_stream.__aiter__()

        with self.assertRaisesRegex(RuntimeError, "only be iterated once"):
            asyncio.run(SyncRunCollector().collect(claimed_stream))

        def fail_loop(*args, **kwargs):
            raise RuntimeError("producer failed")

        with patch.object(run_loop_module, "run_agent_loop", fail_loop):
            with self.assertRaisesRegex(RuntimeError, "producer failed"):
                asyncio.run(
                    SyncRunCollector().collect(
                        Runner.run_stream(
                            Agent(
                                memory=AgentMemory(),
                                model=ScriptedResponseModel([]),
                            ),
                            "Fail unexpectedly.",
                        )
                    )
                )

        cancelled_stream = Runner.run_stream(
            Agent(memory=AgentMemory(), model=ScriptedResponseModel([])),
            "Cancel.",
        )
        self.assertTrue(cancelled_stream.controller.cancel("collector cancel"))

        with self.assertRaisesRegex(RunCancelledError, "collector cancel"):
            asyncio.run(SyncRunCollector().collect(cancelled_stream))

    def test_runner_entries_preserve_sync_model_response(self) -> None:
        response = ModelResponse("response-positional", [], "done", [])
        sync_model = ScriptedResponseModel([response])
        async_model = ScriptedResponseModel([response])
        stream_model = ScriptedResponseModel([response])

        sync_result = Runner.run_sync(
            Agent(memory=AgentMemory(), model=sync_model),
            "Sync.",
        )

        async def scenario():
            async_result = await Runner.run_async(
                Agent(memory=AgentMemory(), model=async_model),
                "Async.",
            )
            stream = Runner.run_stream(
                Agent(memory=AgentMemory(), model=stream_model),
                "Stream.",
            )
            _ = [event async for event in stream]
            return async_result, await stream._wait_result()

        async_result, stream_result = asyncio.run(scenario())

        for result in (sync_result, async_result, stream_result):
            self.assertEqual(result.final_answer, "done")
            self.assertEqual(result.raw_responses, (response,))
            self.assertEqual(
                [item.item_type for item in result.new_items].count("model_response"),
                1,
            )
            self.assertEqual(len(result.model_attempts), 1)
            self.assertEqual(result.model_attempts[0].mode, "non_stream")
        self.assertEqual(
            [sync_model._index, async_model._index, stream_model._index],
            [1, 1, 1],
        )

    def test_coding_agent_builds_repo_context_before_first_model_turn(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        workspace_path = Path(temp_dir.name)
        (workspace_path / "src" / "agents").mkdir(parents=True)
        (workspace_path / "src" / "agents" / "context_chunks.py").write_text(
            "def build_turn_context():\n    return []\n",
            encoding="utf-8",
        )
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "done"},
                    call_id="call_1",
                )
            ]
        )
        setup = build_coding_agent(
            model=model,
            workspace=workspace_path,
            profile=CodingAgentProfile.read_only(),
        )

        Runner.run_sync(
            setup.agent,
            "修改 `src/agents/context_chunks.py` 并查看 build_turn_context",
            config=setup.run_config,
        )

        rendered_messages = "\n\n".join(message.content for message in model.last_messages)
        self.assertIn("Repo context:", rendered_messages)
        self.assertIn("## Workspace inventory", rendered_messages)
        self.assertIn("## Selected files", rendered_messages)
        self.assertIn("src/agents/context_chunks.py", rendered_messages)
        self.assertIn("Mentioned symbols: build_turn_context", rendered_messages)
        self.assertIn("## Workspace code matches", rendered_messages)
        self.assertIn("build_turn_context: src/agents/context_chunks.py:1", rendered_messages)

    def test_runner_run_sync_uses_runtime_loop_not_agent_private_run(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "runtime loop finished"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        def fail_if_called(task: str, config=None):
            raise AssertionError("Runner should not call Agent._run()")

        agent._run = fail_if_called

        result = Runner.run_sync(agent, "Finish through runtime loop.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "runtime loop finished")
        self.assertEqual(result.steps_taken, 1)

    def test_agent_run_keeps_backward_compatible_entrypoint(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "agent run still works"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        result = agent.run("Finish through Agent.run.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "agent run still works")
        self.assertEqual(result.steps_taken, 1)

    def test_runner_run_sync_accepts_run_config_max_steps(self) -> None:
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "should not run"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, max_steps=5)

        result = Runner.run_sync(
            agent,
            "Stop before tool execution.",
            config=RunConfig(max_steps=0),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.steps_taken, 0)
        self.assertEqual(result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(result.new_items[-1].payload, "max_steps_reached")

    def test_runner_run_sync_accepts_run_config_max_turns(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "first"}, "call_1"),
                ToolCall("final_answer", {"answer": "should not run"}, "call_2"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)

        result = Runner.run_sync(
            agent,
            "Echo once, then stop before the second model call.",
            config=RunConfig(max_turns=1),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.current_turn, 1)
        self.assertEqual(result.max_turns, 1)
        self.assertEqual(result.steps_taken, 1)
        self.assertEqual(model._index, 1)
        self.assertEqual(result.new_items[-1].item_type, "run_stopped")
        self.assertEqual(result.new_items[-1].payload, "max_turns_reached")

    def test_runner_run_sync_prepends_run_config_session_history(self) -> None:
        session = AgentSession()
        session.add_task("My name is Ada.")
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "Your name is Ada."},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model)

        Runner.run_sync(
            agent,
            "What is my name?",
            config=RunConfig(session=session),
        )

        self.assertEqual(
            [message.content for message in model.last_messages],
            ["My name is Ada.", "What is my name?"],
        )
        self.assertEqual(
            [message.role for message in session.replay()],
            ["user", "user", "tool_call", "tool_response", "assistant"],
        )
        self.assertEqual(
            [message.content for message in session.replay()],
            [
                "My name is Ada.",
                "What is my name?",
                "call_1: final_answer(answer='Your name is Ada.')",
                "Your name is Ada.",
                "Your name is Ada.",
            ],
        )

    def test_state_machine_session_writeback_preserves_tool_and_final_messages(self) -> None:
        session = AgentSession()
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "memory ok"}, "call_1"),
                ToolCall("final_answer", {"answer": "done"}, "call_2"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)

        result = Runner.run_sync(
            agent,
            "Echo then answer.",
            config=RunConfig(session=session),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(
            [message.role for message in session.replay()],
            ["user", "tool_call", "tool_response", "tool_call", "tool_response", "assistant"],
        )
        self.assertEqual(session.replay()[-1].content, "done")

    def test_state_machine_output_guardrail_block_does_not_save_assistant_message(self) -> None:
        @output_guardrail(name="block_unsafe_output")
        def block_unsafe_output(context, agent, output):
            return GuardrailFunctionOutput(
                output_info={"checked_output": output},
                tripwire_triggered=output == "unsafe",
            )

        session = AgentSession()
        model = ScriptedModel([ToolCall("final_answer", {"answer": "unsafe"}, "call_1")])
        agent = Agent(memory=AgentMemory(), model=model)

        result = Runner.run_sync(
            agent,
            "Return unsafe.",
            config=RunConfig(
                session=session,
                output_guardrails=[block_unsafe_output],
            ),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertNotIn("final_output", [item.item_type for item in result.new_items])
        self.assertEqual(
            [message.role for message in session.replay()],
            ["user", "tool_call", "tool_response"],
        )

    def test_state_machine_tool_guardrail_rejection_runs_again_and_writes_session(self) -> None:
        called_with = []

        @tool_input_guardrail(name="block_echo")
        def block_echo(context, agent, tool_call):
            return ToolGuardrailFunctionOutput.reject_content("blocked by policy")

        guarded_echo = FunctionTool(
            spec=echo_tool().spec,
            handler=lambda text: called_with.append(text) or text,
            tool_input_guardrails=[block_echo],
        )
        registry = ToolRegistry()
        registry.register(guarded_echo)
        session = AgentSession()
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "secret"}, "call_1"),
                ToolCall("final_answer", {"answer": "handled rejection"}, "call_2"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)

        result = Runner.run_sync(
            agent,
            "Try guarded echo.",
            config=RunConfig(session=session),
        )

        self.assertEqual(called_with, [])
        self.assertTrue(result.reached_final_answer)
        self.assertIn("tool_input_guardrail", [item.item_type for item in result.new_items])
        self.assertIn("blocked by policy", [message.content for message in session.replay()])
        self.assertEqual(session.replay()[-1].content, "handled rejection")

    def test_state_machine_handoff_final_output_updates_last_agent_and_session(self) -> None:
        helper = Agent(
            memory=AgentMemory(),
            model=ScriptedModel([ToolCall("final_answer", {"answer": "helper done"}, "call_2")]),
            name="Helper",
        )
        parent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [ToolCall("transfer_to_helper", {"task": "finish it"}, "call_1")]
            ),
            handoffs=[helper],
        )
        session = AgentSession()

        result = Runner.run_sync(
            parent,
            "Delegate this.",
            config=RunConfig(session=session),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "helper done")
        self.assertIs(result.last_agent, helper)
        self.assertIn("handoff", [item.item_type for item in result.new_items])
        self.assertEqual(session.replay()[-1].content, "helper done")

    def test_runner_uses_compacted_session_context_once_per_run(self) -> None:
        class CountingSession:
            def __init__(self, inner: AgentSession) -> None:
                self.inner = inner
                self.get_items_count = 0

            def get_items(self, limit=None):
                self.get_items_count += 1
                return self.inner.get_items(limit=limit)

            def add_items(self, items):
                self.inner.add_items(items)

            def pop_item(self):
                return self.inner.pop_item()

            def clear_session(self) -> None:
                self.inner.clear_session()

        long_observation = "old raw observation detail " * 40
        inner = AgentSession(compact_after_turns=1, compact_keep_turns=1)
        inner.add_task("Old raw project question?")
        inner.add_step(StepRecord(step_number=1, observation=long_observation))
        inner.add_task("Recent project question?")
        inner.add_step(StepRecord(step_number=2, observation="recent project answer"))
        session = CountingSession(inner)
        registry = ToolRegistry()
        registry.register(echo_tool())
        model = ScriptedModel(
            [
                ToolCall("echo_text", {"text": "tool round"}, "call_1"),
                ToolCall("final_answer", {"answer": "done"}, "call_2"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry)

        Runner.run_sync(
            agent,
            "Use the remembered context.",
            config=RunConfig(session=session),
        )

        first_batch = model.message_batches[0]
        first_contents = [message.content for message in first_batch]
        self.assertEqual(session.get_items_count, 1)
        self.assertEqual(first_batch[0].role, "system")
        self.assertIn("Conversation summary:", first_batch[0].content)
        self.assertIn("Recent project question?", first_contents)
        self.assertEqual(first_batch[-1].content, "Use the remembered context.")
        self.assertNotIn(long_observation, "\n".join(first_contents))
        self.assertEqual(session.inner.replay()[0].role, "system")

    def test_current_turn_counts_one_model_response_with_multiple_tools_once(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[
                ToolCall("echo_text", {"text": "first"}, "call_1"),
                ToolCall("echo_text", {"text": "second"}, "call_2"),
            ],
        )
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel([model_response]),
            tool_registry=registry,
            max_steps=5,
        )

        result = Runner.run_sync(
            agent,
            "Echo two values.",
            config=RunConfig(max_turns=1),
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.current_turn, 1)
        self.assertEqual(result.steps_taken, 2)
        self.assertEqual(result.step_results, ["first", "second"])
        self.assertEqual(result.new_items[-1].payload, "max_turns_reached")

    def test_run_config_tool_use_behavior_overrides_agent_default(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall(
                        tool_name="echo_text",
                        arguments={"text": "direct"},
                        call_id="call_1",
                    )
                ]
            ),
            tool_registry=registry,
            tool_use_behavior="run_llm_again",
        )

        result = agent.run(
            "Return direct tool output.",
            config=RunConfig(tool_use_behavior="stop_on_first_tool"),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "direct")
        self.assertEqual(result.steps_taken, 1)

    def test_runner_run_sync_creates_trace_and_agent_span(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "traced"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        result = Runner.run_sync(
            agent,
            "Trace this run.",
            config=RunConfig(metadata={"course": "tracing"}),
        )
        processor.force_flush()
        exported = exporter.items()

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(
            [item["object"] for item in exported],
            [
                "trace.span",
                "trace.span",
                "trace.span",
                "trace.span",
                "trace.span",
                "trace.span",
                "trace",
            ],
        )
        self.assertEqual(exported[0]["span_data"]["type"], "generation")
        self.assertEqual(exported[0]["span_data"]["model"], "ScriptedModel")
        self.assertEqual(exported[0]["span_data"]["agent"], "Planner")
        self.assertEqual(exported[0]["span_data"]["model_config"]["message_count"], 1)
        self.assertEqual(exported[0]["span_data"]["model_config"]["tool_count"], 1)
        self.assertIsNotNone(exported[0]["span_data"]["request_id"])
        self.assertIsNotNone(exported[0]["span_data"]["final_attempt_id"])
        self.assertEqual(exported[0]["span_data"]["attempt_count"], 1)
        self.assertEqual(exported[0]["span_data"]["retry_count"], 0)
        self.assertEqual(exported[0]["span_data"]["final_model"], "ScriptedModel")
        self.assertEqual(exported[0]["span_data"]["final_mode"], "non_stream")
        self.assertEqual(
            exported[0]["span_data"]["response_id"],
            exported[0]["span_data"]["request_id"],
        )
        self.assertIsNone(exported[0]["span_data"]["usage"])
        self.assertIsNone(exported[0]["span_data"]["ttft_seconds"])
        self.assertEqual(
            len(exported[0]["span_data"]["attempt_duration_seconds"]),
            1,
        )
        self.assertEqual(exported[0]["span_data"]["retry_delay_seconds"], [])
        self.assertFalse(exported[0]["span_data"]["non_stream_fallback_used"])
        self.assertFalse(exported[0]["span_data"]["model_fallback_used"])
        self.assertEqual(exported[0]["span_data"]["terminal_reason"], "completed")
        self.assertEqual(exported[1]["span_data"]["type"], "function")
        self.assertEqual(exported[1]["span_data"]["name"], "final_answer")
        self.assertEqual(exported[1]["span_data"]["agent"], "Planner")
        self.assertEqual(exported[1]["span_data"]["input"], {"answer": "traced"})
        self.assertEqual(exported[1]["span_data"]["arguments"], {"answer": "traced"})
        self.assertEqual(exported[1]["span_data"]["output"], "traced")
        self.assertEqual(exported[2]["span_data"]["name"], "turn")
        self.assertEqual(exported[2]["span_data"]["data"]["turn"], 1)
        self.assertEqual(exported[2]["span_data"]["data"]["agent_name"], "Planner")
        self.assertEqual(exported[3]["span_data"]["type"], "agent")
        self.assertEqual(exported[3]["span_data"]["name"], "Planner")
        self.assertEqual(exported[3]["span_data"]["task"], "Trace this run.")
        self.assertEqual(exported[4]["span_data"]["name"], "task")
        self.assertEqual(exported[4]["span_data"]["data"]["name"], "Trace this run.")
        self.assertEqual(exported[5]["span_data"]["name"], "event_stream")
        self.assertEqual(exported[6]["workflow_name"], "Planner")
        self.assertEqual(exported[6]["metadata"]["course"], "tracing")
        self.assertNotIn("spans", exported[6])

    def test_run_config_can_customize_trace_workflow_name(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "named trace"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        Runner.run_sync(
            agent,
            "Trace with a logical workflow name.",
            config=RunConfig(workflow_name="Course workflow"),
        )
        processor.force_flush()
        exported = exporter.items()

        self.assertEqual(exported[-1]["object"], "trace")
        self.assertEqual(exported[-1]["workflow_name"], "Course workflow")
        self.assertEqual(exported[-4]["span_data"]["type"], "agent")
        self.assertEqual(exported[-4]["span_data"]["name"], "Planner")
        self.assertEqual(exported[-3]["span_data"]["name"], "task")
        self.assertEqual(exported[-2]["span_data"]["name"], "event_stream")

    def test_run_config_can_disable_tracing_for_single_run(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "no trace"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        result = Runner.run_sync(
            agent,
            "Run without tracing.",
            config=RunConfig(tracing_disabled=True),
        )
        processor.force_flush()

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(exporter.items(), [])

    def test_run_config_can_exclude_sensitive_trace_data(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "secret"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        Runner.run_sync(
            agent,
            "User phone is 555-0100.",
            config=RunConfig(trace_include_sensitive_data=False),
        )
        processor.force_flush()
        exported = exporter.items()

        self.assertIsNone(exported[0]["span_data"]["input"])
        self.assertIsNone(exported[1]["span_data"]["input"])
        self.assertIsNone(exported[1]["span_data"]["output"])
        self.assertNotIn("arguments", exported[1]["span_data"])
        self.assertNotIn("555-0100", str(exported[0]["span_data"]))
        self.assertNotIn("secret", str(exported[0]["span_data"]))

    def test_run_config_can_set_trace_identity_and_metadata(self) -> None:
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [ToolCall("final_answer", {"answer": "identified trace"}, "call_1")]
        )
        agent = Agent(memory=AgentMemory(), model=model, name="Planner")

        Runner.run_sync(
            agent,
            "Trace with identity.",
            config=RunConfig(
                metadata={"run": "context"},
                trace_metadata={"trace": "metadata"},
                trace_id="trace_course_1",
                group_id="group_course",
            ),
        )
        processor.force_flush()
        exported_trace = exporter.items()[-1]

        self.assertEqual(exported_trace["object"], "trace")
        self.assertEqual(exported_trace["id"], "trace_course_1")
        self.assertEqual(exported_trace["group_id"], "group_course")
        self.assertEqual(exported_trace["metadata"], {"trace": "metadata"})

    def test_tool_span_records_structured_error_type(self) -> None:
        def failing_tool() -> str:
            raise ValueError("boom")

        registry = ToolRegistry()
        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="fail_tool",
                    description="Always fails.",
                    arguments=[],
                    returns="string",
                ),
                handler=failing_tool,
            )
        )
        exporter = InMemoryTracingExporter()
        processor = BatchTraceProcessor(exporter)
        set_trace_processors([processor])
        model = ScriptedModel(
            [
                ToolCall("fail_tool", {}, "call_fail"),
                ToolCall("final_answer", {"answer": "recovered"}, "call_done"),
            ]
        )
        agent = Agent(memory=AgentMemory(), model=model, tool_registry=registry, name="Repair")

        result = Runner.run_sync(agent, "Recover from a tool error.")
        processor.force_flush()
        tool_spans = [
            item for item in exporter.items()
            if item["object"] == "trace.span" and item["span_data"]["type"] == "function"
        ]

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(tool_spans[0]["span_data"]["name"], "fail_tool")
        self.assertEqual(tool_spans[0]["span_data"]["error_type"], "ToolExecutionError")
        self.assertEqual(tool_spans[0]["span_data"]["error_cause_type"], "ValueError")
        self.assertIn("boom", tool_spans[0]["error"]["message"])
        self.assertEqual(
            tool_spans[0]["error"]["data"],
            {
                "error_type": "ToolExecutionError",
                "error_cause_type": "ValueError",
            },
        )


class RunConfigEventStreamTestCase(unittest.TestCase):
    def test_event_stream_defaults_and_custom_values(self) -> None:
        defaults = RunConfig()
        custom = RunConfig(event_queue_size=8, cancellation_grace_period=0.5)

        self.assertEqual(defaults.event_queue_size, 100)
        self.assertEqual(defaults.cancellation_grace_period, 0.1)
        self.assertEqual(custom.event_queue_size, 8)
        self.assertEqual(custom.cancellation_grace_period, 0.5)

    def test_rejects_invalid_event_queue_size(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RunConfig(event_queue_size=value)  # type: ignore[arg-type]

    def test_rejects_invalid_cancellation_grace_period(self) -> None:
        for value in (-1, True, float("nan"), float("inf"), "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                RunConfig(cancellation_grace_period=value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
