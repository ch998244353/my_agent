from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    ChatMessage,
    FunctionTool,
    ModelResponse,
    ModelSettings,
    RunConfig,
    TaskStore,
    ToolCall,
)
from agents.context_chunks import ContextChunk  # noqa: E402
import agents.agent_tools as agent_tools_module  # noqa: E402
import agents.model_settings as model_settings_module  # noqa: E402
import agents.run_config as run_config_module  # noqa: E402


class RecordingModel:
    def __init__(self, actions=None) -> None:
        self.actions = list(actions or [])
        self.last_messages: list[ChatMessage] = []
        self.last_tool_specs = []
        self._index = 0

    def decide(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        return action


class SchemaAwareModel(RecordingModel):
    def __init__(self) -> None:
        super().__init__()
        self.response_schema = None
        self.response_schema_name = "final_response"
        self.response_schema_strict = True

    def get_response(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        return ModelResponse(
            response_id="resp_1",
            output=[],
            output_text='{"answer": "done"}',
            tool_calls=[],
        )


class ListSession:
    def __init__(self) -> None:
        self.items: list[ChatMessage] = []

    def get_items(self, limit: int | None = None) -> list[ChatMessage]:
        if limit is None:
            return list(self.items)
        return self.items[-limit:]

    def add_items(self, items: list[ChatMessage]) -> None:
        self.items.extend(items)

    def pop_item(self) -> ChatMessage | None:
        return self.items.pop() if self.items else None

    def clear_session(self) -> None:
        self.items.clear()


class AgentConfigurationTestCase(unittest.TestCase):
    def test_run_config_accepts_session_entry(self) -> None:
        self.assertIn("session", RunConfig.__dataclass_fields__)
        self.assertTrue(hasattr(run_config_module, "SessionLike"))
        for method_name in ("get_items", "add_items", "pop_item", "clear_session"):
            self.assertTrue(hasattr(run_config_module.SessionLike, method_name))

        session = ListSession()

        config = RunConfig(session=session)

        self.assertIs(config.session, session)
        self.assertIsNone(RunConfig().session)

    def test_run_config_accepts_only_non_empty_context_chunk_tuples(self) -> None:
        chunk = ContextChunk("memory", "user", "saved fact", 47, "test")

        config = RunConfig(context_chunks=(chunk,))

        self.assertIs(config.context_chunks[0], chunk)
        self.assertEqual(RunConfig().context_chunks, ())
        for invalid in ([chunk], (None,), ({"content": "fake"},)):
            with self.subTest(invalid=invalid):
                with self.assertRaises(TypeError):
                    RunConfig(context_chunks=invalid)
        with self.assertRaises(ValueError):
            RunConfig(
                context_chunks=(
                    ContextChunk("empty", "user", "   ", 47, "test"),
                )
            )

    def test_run_config_task_binding_defaults_to_unbound(self) -> None:
        config = RunConfig()

        self.assertIsNone(config.task_store)
        self.assertIsNone(config.task_id)

    def test_run_config_accepts_and_preserves_complete_task_binding(self) -> None:
        store = TaskStore()
        config = RunConfig(task_store=store, task_id="missing")

        updated = replace(config, max_turns=2)

        self.assertIs(updated.task_store, store)
        self.assertEqual(updated.task_id, "missing")

    def test_agent_tool_config_merge_preserves_task_binding(self) -> None:
        store = TaskStore()
        config = RunConfig(task_store=store, task_id="task-1")

        updated = agent_tools_module._resolve_run_config(config, 2, 3)

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertIs(updated.task_store, store)
        self.assertEqual(updated.task_id, "task-1")
        self.assertEqual(updated.max_steps, 2)
        self.assertEqual(updated.max_turns, 3)

    def test_run_config_rejects_partial_task_binding(self) -> None:
        with self.subTest("store only"):
            with self.assertRaisesRegex(ValueError, "must be provided together"):
                RunConfig(task_store=TaskStore())
        with self.subTest("id only"):
            with self.assertRaisesRegex(ValueError, "must be provided together"):
                RunConfig(task_id="task-1")

    def test_run_config_rejects_invalid_task_id(self) -> None:
        for task_id in (42, True):
            with self.subTest(task_id=task_id):
                with self.assertRaisesRegex(TypeError, "must be a string"):
                    RunConfig(task_store=TaskStore(), task_id=task_id)
        for task_id in ("", "   "):
            with self.subTest(task_id=task_id):
                with self.assertRaisesRegex(ValueError, "must be a non-empty string"):
                    RunConfig(task_store=TaskStore(), task_id=task_id)

    def test_agent_has_sdk_style_default_configuration(self) -> None:
        agent = Agent(memory=AgentMemory(), model=RecordingModel())

        self.assertEqual(agent.name, "Agent")
        self.assertIsNone(agent.instructions)
        self.assertEqual(agent.model_settings, ModelSettings())
        self.assertIsNone(agent.output_type)
        self.assertEqual(agent.tool_use_behavior, "run_llm_again")

    def test_agent_clone_replaces_selected_fields_and_copies_lists(self) -> None:
        original_memory = AgentMemory()
        replacement_memory = AgentMemory()
        agent = Agent(memory=original_memory, model=RecordingModel(), name="Planner")

        clone = agent.clone(memory=replacement_memory, name="Planner copy")

        self.assertIsNot(clone, agent)
        self.assertIs(clone.memory, replacement_memory)
        self.assertIs(agent.memory, original_memory)
        self.assertEqual(clone.name, "Planner copy")
        self.assertIs(clone.model, agent.model)
        self.assertIs(clone.tool_registry, agent.tool_registry)
        self.assertIsNot(clone.handoffs, agent.handoffs)
        self.assertIsNot(clone.input_guardrails, agent.input_guardrails)
        self.assertIsNot(clone.output_guardrails, agent.output_guardrails)

    def test_agent_as_tool_wraps_agent_as_function_tool(self) -> None:
        agent = Agent(memory=AgentMemory(), model=RecordingModel(), name="Research Agent")

        tool = agent.as_tool(
            tool_name="research",
            tool_description="Run research subtasks.",
        )

        self.assertIsInstance(tool, FunctionTool)
        self.assertEqual(tool.name, "research")
        self.assertEqual(tool.spec.description, "Run research subtasks.")
        self.assertEqual(tool.spec.arguments[0].name, "input")

    def test_agent_as_tool_accepts_enabled_flag(self) -> None:
        agent = Agent(memory=AgentMemory(), model=RecordingModel(), name="Research Agent")

        tool = agent.as_tool(is_enabled=False)

        self.assertFalse(tool.is_enabled_for(None, agent))

    def test_agent_accepts_identity_prompt_and_output_configuration(self) -> None:
        settings = ModelSettings(temperature=0.2, top_p=0.9, tool_choice="auto")

        agent = Agent(
            name="Weather bot",
            instructions="You answer with compact weather facts.",
            memory=AgentMemory(),
            model=RecordingModel(),
            model_settings=settings,
            output_type=dict,
            tool_use_behavior="stop_on_first_tool",
        )

        self.assertEqual(agent.name, "Weather bot")
        self.assertEqual(agent.instructions, "You answer with compact weather facts.")
        self.assertIs(agent.model_settings, settings)
        self.assertIs(agent.output_type, dict)
        self.assertEqual(agent.tool_use_behavior, "stop_on_first_tool")

    def test_model_settings_resolve_inherits_extended_fields(self) -> None:
        base = ModelSettings(
            parallel_tool_calls=True,
            verbosity="low",
            reasoning={"effort": "low"},
            max_attempts=5,
            retry_budget=20,
        )
        override = ModelSettings(verbosity="high")

        resolved = base.resolve(override)

        self.assertTrue(resolved.parallel_tool_calls)
        self.assertEqual(resolved.verbosity, "high")
        self.assertEqual(resolved.reasoning, {"effort": "low"})
        self.assertEqual(resolved.max_attempts, 5)
        self.assertEqual(resolved.retry_budget, 20)

    def test_model_settings_retry_defaults_are_explicit_but_sparse(self) -> None:
        settings = ModelSettings()

        self.assertIsNone(settings.connect_timeout)
        self.assertIsNone(settings.max_attempts)
        self.assertIsNone(settings.allow_non_stream_fallback)
        self.assertIsNone(settings.fallback_model)
        self.assertEqual(model_settings_module.DEFAULT_CONNECT_TIMEOUT, 10.0)
        self.assertEqual(model_settings_module.DEFAULT_READ_TIMEOUT, 60.0)
        self.assertEqual(model_settings_module.DEFAULT_TOTAL_TIMEOUT, 120.0)
        self.assertEqual(model_settings_module.DEFAULT_MAX_ATTEMPTS, 3)
        self.assertEqual(model_settings_module.DEFAULT_BACKOFF_INITIAL, 0.5)
        self.assertEqual(model_settings_module.DEFAULT_BACKOFF_MAX, 8.0)
        self.assertEqual(model_settings_module.DEFAULT_JITTER_RATIO, 0.2)
        self.assertEqual(model_settings_module.DEFAULT_RETRY_BUDGET, 30.0)
        self.assertEqual(
            model_settings_module.DEFAULT_MAX_STREAM_BUFFER_CHARS,
            1_000_000,
        )
        self.assertFalse(
            model_settings_module.DEFAULT_ALLOW_NON_STREAM_FALLBACK
        )
        self.assertIsNone(model_settings_module.DEFAULT_FALLBACK_MODEL)

    def test_model_settings_accepts_retry_configuration(self) -> None:
        settings = ModelSettings(
            connect_timeout=5,
            read_timeout=30.0,
            total_timeout=90,
            max_attempts=4,
            backoff_initial=1,
            backoff_max=6,
            jitter_ratio=0.25,
            retry_budget=20,
            max_stream_buffer_chars=2_000,
            allow_non_stream_fallback=True,
            fallback_model="gpt-fallback",
        )

        self.assertEqual(settings.max_attempts, 4)
        self.assertEqual(settings.total_timeout, 90)
        self.assertTrue(settings.allow_non_stream_fallback)
        self.assertEqual(settings.fallback_model, "gpt-fallback")

    def test_model_settings_rejects_invalid_retry_field_types(self) -> None:
        invalid_settings = (
            {"connect_timeout": True},
            {"read_timeout": "60"},
            {"total_timeout": object()},
            {"max_attempts": True},
            {"backoff_initial": False},
            {"backoff_max": "8"},
            {"jitter_ratio": "0.2"},
            {"retry_budget": object()},
            {"max_stream_buffer_chars": 1.5},
            {"allow_non_stream_fallback": 1},
            {"fallback_model": 42},
        )

        for values in invalid_settings:
            with self.subTest(values=values):
                with self.assertRaises(TypeError):
                    ModelSettings(**values)

    def test_model_settings_rejects_invalid_retry_field_values(self) -> None:
        invalid_settings = (
            {"connect_timeout": 0},
            {"read_timeout": float("inf")},
            {"total_timeout": float("nan")},
            {"max_attempts": 0},
            {"backoff_initial": -0.1},
            {"backoff_max": -0.1},
            {"jitter_ratio": -0.1},
            {"jitter_ratio": 1.1},
            {"retry_budget": -0.1},
            {"max_stream_buffer_chars": 0},
            {"fallback_model": "   "},
            {"backoff_initial": 9, "backoff_max": 8},
            {"retry_budget": 31, "total_timeout": 30},
        )

        for values in invalid_settings:
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    ModelSettings(**values)

    def test_agent_configures_model_with_output_schema(self) -> None:
        output_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        }
        model = SchemaAwareModel()

        Agent(
            memory=AgentMemory(),
            model=model,
            output_type=output_schema,
        )

        self.assertIs(model.response_schema, output_schema)
        self.assertEqual(model.response_schema_name, "agent_output")
        self.assertTrue(model.response_schema_strict)

    def test_instructions_are_sent_as_first_model_message(self) -> None:
        model = RecordingModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "done"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(
            name="Concise assistant",
            instructions="Always answer in one sentence.",
            memory=AgentMemory(),
            model=model,
        )

        agent.run("Say done.")

        self.assertEqual(model.last_messages[0].role, "system")
        self.assertEqual(model.last_messages[0].content, "Always answer in one sentence.")
        self.assertEqual(model.last_messages[1].role, "user")
        self.assertEqual(model.last_messages[1].content, "Say done.")

    def test_empty_instructions_do_not_add_system_message(self) -> None:
        model = RecordingModel()
        agent = Agent(memory=AgentMemory(), model=model, instructions="")

        agent.run("Stop immediately.")

        self.assertEqual(model.last_messages[0].role, "user")
        self.assertEqual(model.last_messages[0].content, "Stop immediately.")


if __name__ == "__main__":
    unittest.main()
