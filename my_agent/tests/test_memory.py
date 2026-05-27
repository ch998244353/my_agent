from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    AgentMemory,
    AgentSession,
    ChatMessage,
    CompactionPolicy,
    JsonSession,
    StepRecord,
    ToolCall,
    MemoryCompressor,
    ModelSummarizer,
    RuleBasedSummarizer,
    MemorySummary,
)
import agents.memory as memory_module  # noqa: E402


class AgentMemoryTestCase(unittest.TestCase):
    def test_to_messages_includes_task_and_step_observation(self) -> None:
        memory = AgentMemory()
        memory.add_task("Tell me the weather in Shanghai.")
        memory.add_step(
            StepRecord(
                step_number=1,
                observation="Weather in Shanghai: 18C and cloudy.",
            )
        )

        messages = memory.to_messages()

        self.assertEqual(messages[0].role, "user")
        self.assertEqual(messages[0].content, "Tell me the weather in Shanghai.")
        self.assertEqual(messages[1].role, "tool_response")
        self.assertEqual(
            messages[1].content,
            "Observation:\nWeather in Shanghai: 18C and cloudy.",
        )

    def test_to_messages_preserves_step_messages(self) -> None:
        memory = AgentMemory()
        memory.add_task("Search local docs.")
        memory.add_step(
            StepRecord(
                step_number=1,
                messages=[
                    ChatMessage(
                        role="assistant",
                        content="I should inspect the search results first.",
                    )
                ],
                observation="Found 3 matching files.",
            )
        )

        messages = memory.to_messages()

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[1].role, "assistant")
        self.assertEqual(messages[2].role, "tool_response")

    def test_to_messages_includes_tool_call_before_observation(self) -> None:
        memory = AgentMemory()
        memory.add_task("Calculate 1 + 2.")
        memory.add_step(
            StepRecord(
                step_number=1,
                tool_calls=[
                    ToolCall(
                        tool_name="calculator",
                        arguments={"expression": "1 + 2"},
                        call_id="call_1",
                    )
                ],
                observation="3",
            )
        )

        messages = memory.to_messages()

        self.assertEqual(messages[1].role, "tool_call")
        self.assertEqual(
            messages[1].content,
            "Calling tools:\n- call_1: calculator(expression='1 + 2')",
        )
        self.assertEqual(messages[2].role, "tool_response")
        self.assertEqual(messages[2].content, "Observation:\n3")

    def test_to_messages_includes_error_retry_hint(self) -> None:
        memory = AgentMemory()
        memory.add_task("Use a missing tool.")
        memory.add_step(
            StepRecord(
                step_number=1,
                tool_calls=[
                    ToolCall(
                        tool_name="unknown_tool",
                        arguments={},
                        call_id="call_1",
                    )
                ],
                error="Tool 'unknown_tool' is not registered.",
            )
        )

        messages = memory.to_messages()

        self.assertEqual(messages[2].role, "tool_response")
        self.assertIn("Call id: call_1", messages[2].content)
        self.assertIn("Error:\nTool 'unknown_tool' is not registered.", messages[2].content)
        self.assertIn("Now let's retry", messages[2].content)

    def test_to_messages_can_keep_only_recent_steps(self) -> None:
        memory = AgentMemory()
        memory.add_task("Echo history.")
        memory.add_step(StepRecord(step_number=1, observation="first"))
        memory.add_step(StepRecord(step_number=2, observation="second"))
        memory.add_step(StepRecord(step_number=3, observation="third"))

        messages = memory.to_messages(max_steps=2)

        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[0].content, "Echo history.")
        self.assertEqual(messages[1].content, "Observation:\nsecond")
        self.assertEqual(messages[2].content, "Observation:\nthird")

    def test_reset_steps_keeps_task(self) -> None:
        memory = AgentMemory(task="Original task")
        memory.add_step(StepRecord(step_number=1, observation="step"))

        memory.reset_steps()

        self.assertEqual(memory.task, "Original task")
        self.assertEqual(memory.steps, [])

    def test_return_full_code_concatenates_python_executor_calls(self) -> None:
        memory = AgentMemory(task="Run some code.")
        memory.add_step(
            StepRecord(
                step_number=1,
                tool_calls=[
                    ToolCall(
                        tool_name="python_executor",
                        arguments={"code": 'value = 7\nprint("stored", value)'},
                        call_id="call_1",
                    )
                ],
                observation="Execution logs:\nstored 7\nLast output from code snippet:\nNone",
            )
        )
        memory.add_step(
            StepRecord(
                step_number=2,
                tool_calls=[
                    ToolCall(
                        tool_name="python_executor",
                        arguments={"code": "final_answer(value + 1)"},
                        call_id="call_2",
                    )
                ],
                observation="Execution logs:\nLast output from code snippet:\n8",
                is_final_answer=True,
            )
        )

        full_code = memory.return_full_code()

        self.assertEqual(
            full_code,
            'value = 7\nprint("stored", value)\n\nfinal_answer(value + 1)',
        )


class AgentSessionTestCase(unittest.TestCase):
    def test_session_conversion_helpers_live_in_memory_module(self) -> None:
        class MessageObject:
            role = "assistant"
            content = "object message"

        class ResultLike:
            def to_input_list(self):
                return [
                    ChatMessage(role="user", content="chat message"),
                    {"role": "tool_response", "content": "dict message"},
                    MessageObject(),
                ]

        self.assertTrue(hasattr(memory_module, "session_item_to_message"))
        self.assertTrue(hasattr(memory_module, "session_items_from_result"))

        messages = memory_module.session_items_from_result(ResultLike())

        self.assertEqual([message.role for message in messages], [
            "user",
            "tool_response",
            "assistant",
        ])
        self.assertEqual([message.content for message in messages], [
            "chat message",
            "dict message",
            "object message",
        ])

    def test_session_round_trips_through_dict(self) -> None:
        session = AgentSession(max_steps=5, max_turns=3)
        session.add_task("Use a tool.")
        session.add_step(
            StepRecord(
                step_number=1,
                messages=[ChatMessage(role="assistant", content="I will call a tool.")],
                tool_calls=[
                    ToolCall(
                        tool_name="lookup",
                        arguments={"query": "Ada"},
                        call_id="call_1",
                    )
                ],
                observation="Ada Lovelace",
                is_final_answer=True,
            )
        )
        session.add_task("Handle an error.")
        session.add_step(
            StepRecord(
                step_number=2,
                error="tool failed",
            )
        )

        data = session.to_dict()
        restored = AgentSession.from_dict(data)

        self.assertEqual(restored.max_steps, 5)
        self.assertEqual(restored.max_turns, 3)
        self.assertEqual(
            [message.content for message in restored.replay()],
            [
                "Use a tool.",
                "I will call a tool.",
                "Calling tools:\n- call_1: lookup(query='Ada')",
                "Observation:\nAda Lovelace",
                "Handle an error.",
                "Error:\ntool failed\nNow let's retry: take care not to repeat previous errors!",
            ],
        )
        self.assertTrue(restored.turns[0].steps[0].is_final_answer)
        self.assertEqual(restored.turns[0].steps[0].tool_calls[0].arguments, {"query": "Ada"})
        self.assertEqual(restored.turns[1].steps[0].error, "tool failed")

    def test_json_session_persists_items_on_disk(self) -> None:
        try:
            from agents import JsonSession
        except ImportError as exc:
            self.fail(f"JsonSession should be exported from agents: {exc}")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = JsonSession(path, max_steps=5, max_turns=3)
            session.add_items([
                ChatMessage(role="user", content="Remember Ada."),
                ChatMessage(role="assistant", content="Ada is a mathematician."),
            ])

            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["session"]["turns"][0]["task"], "Remember Ada.")

            restored = JsonSession(path)
            self.assertEqual(
                [message.content for message in restored.get_items()],
                ["Remember Ada.", "Ada is a mathematician."],
            )

            popped = restored.pop_item()

            self.assertEqual(popped.content, "Ada is a mathematician.")
            self.assertEqual(
                [message.content for message in JsonSession(path).get_items()],
                ["Remember Ada."],
            )

            restored.clear_session()
            self.assertEqual(JsonSession(path).get_items(), [])

    def test_json_session_persists_compaction_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = JsonSession(
                path,
                compact_after_turns=3,
                compact_keep_turns=1,
            )
            session.add_items([ChatMessage(role="user", content="Remember Ada.")])

            raw = json.loads(path.read_text(encoding="utf-8"))

        policy = raw["session"]["compaction_policy"]
        self.assertEqual(policy["compact_after_turns"], 3)
        self.assertEqual(policy["keep_recent_turns"], 1)
        self.assertEqual(policy["compact_observation_chars"], 240)

    def test_json_session_restores_compaction_policy_without_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            path.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "session": {
                            "task": None,
                            "max_steps": None,
                            "max_turns": None,
                            "summary": None,
                            "compaction_policy": {
                                "compact_after_turns": 1,
                                "keep_recent_turns": 1,
                                "max_summary_chars": None,
                                "compact_message_chars": 200,
                                "compact_argument_chars": 120,
                                "compact_observation_chars": 240,
                                "compact_error_chars": 200,
                            },
                            "turns": [],
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            session = JsonSession(path)
            session.add_items([
                ChatMessage(role="user", content="Old question?"),
                ChatMessage(role="assistant", content="old answer"),
            ])
            session.add_items([
                ChatMessage(role="user", content="Recent question?"),
                ChatMessage(role="assistant", content="recent answer"),
            ])

            messages = JsonSession(path).get_items()

        self.assertEqual(messages[0].role, "system")
        self.assertIn("Old question?", messages[0].content)
        self.assertEqual(messages[1].content, "Recent question?")

    def test_session_summary_is_replayed_and_serialized(self) -> None:
        summary = MemorySummary(
            content="Earlier turns established that Ada prefers concise answers.",
            source_turn_count=2,
        )
        session = AgentSession(summary=summary)
        session.add_task("What should we do next?")
        session.add_step(StepRecord(step_number=1, observation="Keep the next answer brief."))

        messages = session.replay()

        self.assertEqual(messages[0].role, "system")
        self.assertEqual(
            messages[0].content,
            "Conversation summary:\nEarlier turns established that Ada prefers concise answers.",
        )
        self.assertEqual([message.content for message in messages[1:]], [
            "What should we do next?",
            "Observation:\nKeep the next answer brief.",
        ])

        restored = AgentSession.from_dict(session.to_dict())

        self.assertEqual(restored.summary.source_turn_count, 2)
        self.assertEqual(restored.replay()[0].content, messages[0].content)

    def test_memory_compressor_compacts_turns_without_session(self) -> None:
        turns = [
            memory_module.SessionTurn(
                task=f"Question {index}?",
                steps=[StepRecord(step_number=index, observation=f"Answer {index}.")],
            )
            for index in range(1, 5)
        ]
        compressor = MemoryCompressor(
            CompactionPolicy(compact_after_turns=3, keep_recent_turns=2)
        )

        result = compressor.compact(
            turns,
            summary=MemorySummary(content="Earlier summary.", source_turn_count=1),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual([turn.task for turn in result.turns], [
            "Question 3?",
            "Question 4?",
        ])
        self.assertEqual(result.summary.source_turn_count, 3)
        self.assertIn("Earlier summary.", result.summary.content)
        self.assertIn("User goals:", result.summary.content)
        self.assertIn("Question 1?", result.summary.content)
        self.assertIn("Important facts:", result.summary.content)
        self.assertIn("Answer 2.", result.summary.content)

    def test_memory_compressor_clips_old_turns_before_summary(self) -> None:
        long_assistant = "assistant detail " * 40
        long_argument = "argument detail " * 40
        long_observation = "observation detail " * 40
        long_error = "error detail " * 40
        recent_observation = "recent observation " * 40
        turns = [
            memory_module.SessionTurn(
                task="Analyze a large tool result.",
                steps=[
                    StepRecord(
                        step_number=1,
                        messages=[ChatMessage(role="assistant", content=long_assistant)],
                        tool_calls=[
                            ToolCall(
                                tool_name="lookup",
                                arguments={"query": long_argument},
                                call_id="call_1",
                            )
                        ],
                        observation=long_observation,
                        error=long_error,
                    )
                ],
            ),
            memory_module.SessionTurn(
                task="Keep this recent turn raw.",
                steps=[StepRecord(step_number=2, observation=recent_observation)],
            ),
        ]
        compressor = MemoryCompressor(
            CompactionPolicy(compact_after_turns=1, keep_recent_turns=1)
        )

        result = compressor.compact(turns)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIn("Decisions:", result.summary.content)
        self.assertIn("assistant detail", result.summary.content)
        self.assertIn("Important facts:", result.summary.content)
        self.assertIn("observation detail", result.summary.content)
        self.assertIn("Open tasks:", result.summary.content)
        self.assertIn("error detail", result.summary.content)
        self.assertIn("...", result.summary.content)
        self.assertNotIn(long_assistant, result.summary.content)
        self.assertNotIn(long_argument, result.summary.content)
        self.assertNotIn(long_observation, result.summary.content)
        self.assertNotIn(long_error, result.summary.content)
        self.assertEqual(result.turns[0].steps[0].observation, recent_observation)

    def test_rule_based_summarizer_builds_structured_summary(self) -> None:
        compact_input = "\n".join(
            [
                "Turn 1:",
                "user: Remember Ada's preference for concise answers.",
                "assistant: Decided to keep future answers short.",
                "tool: lookup(query='Ada Lovelace notes')",
                "observation: Ada Lovelace prefers concise technical summaries.",
                "error: follow-up still needs a concrete example.",
            ]
        )
        summarizer = RuleBasedSummarizer()
        compressor = MemoryCompressor(
            CompactionPolicy(compact_after_turns=1, keep_recent_turns=1),
            summarizer=summarizer,
        )
        turns = [
            memory_module.SessionTurn(
                task="Remember Ada's preference for concise answers.",
                steps=[
                    StepRecord(
                        step_number=1,
                        messages=[
                            ChatMessage(
                                role="assistant",
                                content="Decided to keep future answers short.",
                            )
                        ],
                        tool_calls=[
                            ToolCall(
                                tool_name="lookup",
                                arguments={"query": "Ada Lovelace notes"},
                                call_id="call_1",
                            )
                        ],
                        observation="Ada Lovelace prefers concise technical summaries.",
                        error="follow-up still needs a concrete example.",
                    )
                ],
            ),
            memory_module.SessionTurn(task="Recent turn"),
        ]

        result = compressor.compact(turns)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertLess(len(result.summary.content), len(compact_input))
        self.assertIn("User goals:", result.summary.content)
        self.assertIn("Constraints:", result.summary.content)
        self.assertIn("Decisions:", result.summary.content)
        self.assertIn("Important facts:", result.summary.content)
        self.assertIn("Open tasks:", result.summary.content)
        self.assertIn("Ada Lovelace", result.summary.content)
        self.assertIn("concrete example", result.summary.content)

    def test_model_summarizer_uses_model_output(self) -> None:
        class FakeResponses:
            def __init__(self) -> None:
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return {"output_text": "User goals:\n- model summary"}

        class FakeClient:
            def __init__(self) -> None:
                self.responses = FakeResponses()

        client = FakeClient()
        summarizer = ModelSummarizer(model="summary-model", client=client)

        summary = summarizer.summarize(
            "user: preserve Ada preference",
            previous_summary=MemorySummary(content="Earlier concise preference."),
        )

        self.assertEqual(summary.content, "User goals:\n- model summary")
        self.assertEqual(client.responses.kwargs["model"], "summary-model")
        self.assertFalse(client.responses.kwargs["store"])
        self.assertIn("Earlier concise preference.", str(client.responses.kwargs["input"]))
        self.assertIn("user: preserve Ada preference", str(client.responses.kwargs["input"]))

    def test_model_summarizer_falls_back_to_rule_based_summary(self) -> None:
        class FailingResponses:
            def create(self, **kwargs):
                raise RuntimeError("model unavailable")

        class FailingClient:
            responses = FailingResponses()

        summarizer = ModelSummarizer(client=FailingClient())

        summary = summarizer.summarize(
            "\n".join([
                "user: remember Ada",
                "observation: Ada prefers short technical summaries.",
            ])
        )

        self.assertIn("User goals:", summary.content)
        self.assertIn("remember Ada", summary.content)
        self.assertIn("Ada prefers short technical summaries.", summary.content)

    def test_agent_session_uses_configured_summarizer_for_compaction(self) -> None:
        class RecordingSummarizer:
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def summarize(self, compact_input, *, previous_summary=None):
                self.inputs.append(compact_input)
                return MemorySummary(content="custom session summary")

        summarizer = RecordingSummarizer()
        session = AgentSession(
            compact_after_turns=1,
            compact_keep_turns=1,
            summarizer=summarizer,
        )

        session.add_task("Old question?")
        session.add_step(StepRecord(step_number=1, observation="old observation"))
        session.add_task("Recent question?")
        session.add_step(StepRecord(step_number=2, observation="recent observation"))

        self.assertEqual(session.summary.content, "custom session summary")
        self.assertEqual([turn.task for turn in session.turns], ["Recent question?"])
        self.assertIn("old observation", summarizer.inputs[0])
        self.assertNotIn("old observation", [message.content for message in session.replay()])

    def test_json_session_uses_configured_summarizer_for_compaction(self) -> None:
        class RecordingSummarizer:
            def __init__(self) -> None:
                self.inputs: list[str] = []

            def summarize(self, compact_input, *, previous_summary=None):
                self.inputs.append(compact_input)
                return MemorySummary(content="custom json summary")

        summarizer = RecordingSummarizer()
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.json"
            session = JsonSession(
                path,
                compact_after_turns=1,
                compact_keep_turns=1,
                summarizer=summarizer,
            )

            session.add_items([
                ChatMessage(role="user", content="Old question?"),
                ChatMessage(role="assistant", content="old answer"),
            ])
            session.add_items([
                ChatMessage(role="user", content="Recent question?"),
                ChatMessage(role="assistant", content="recent answer"),
            ])

            messages = session.get_items()

        self.assertIn("old answer", summarizer.inputs[0])
        self.assertEqual(messages[0].content, "Conversation summary:\ncustom json summary")
        self.assertEqual(messages[1].content, "Recent question?")

    def test_session_compacts_old_turns_when_threshold_is_exceeded(self) -> None:
        session = AgentSession(compact_after_turns=3, compact_keep_turns=2)
        for index in range(1, 5):
            session.add_task(f"Question {index}?")
            session.add_step(StepRecord(step_number=index, observation=f"Answer {index}."))

        self.assertEqual([turn.task for turn in session.turns], [
            "Question 3?",
            "Question 4?",
        ])
        self.assertIsNotNone(session.summary)
        self.assertEqual(session.summary.source_turn_count, 2)
        self.assertIn("User goals:", session.summary.content)
        self.assertIn("Question 1?", session.summary.content)
        self.assertIn("Important facts:", session.summary.content)
        self.assertIn("Answer 2.", session.summary.content)

        self.assertEqual([message.content for message in session.replay()], [
            "Conversation summary:\n" + session.summary.content,
            "Question 3?",
            "Observation:\nAnswer 3.",
            "Question 4?",
            "Observation:\nAnswer 4.",
        ])

    def test_session_append_trims_to_max_steps(self) -> None:
        session = AgentSession(task="Echo history.", max_steps=2)

        session.append(StepRecord(step_number=1, observation="first"))
        session.append(StepRecord(step_number=2, observation="second"))
        session.append(StepRecord(step_number=3, observation="third"))

        self.assertEqual([step.step_number for step in session.steps], [2, 3])

    def test_session_replay_returns_recent_turns_with_limit(self) -> None:
        session = AgentSession()
        session.add_task("First question.")
        session.append(StepRecord(step_number=1, observation="first answer"))
        session.add_task("Second question.")
        session.append(StepRecord(step_number=1, observation="second answer"))

        messages = session.replay(limit=1)

        self.assertEqual([message.content for message in messages], [
            "Second question.",
            "Observation:\nsecond answer",
        ])

    def test_session_replay_preserves_multiple_user_turns(self) -> None:
        session = AgentSession()
        session.add_task("First question.")
        session.append(StepRecord(step_number=1, observation="first answer"))
        session.add_task("Second question.")
        session.append(StepRecord(step_number=1, observation="second answer"))

        messages = session.replay()

        self.assertEqual(
            [message.content for message in messages],
            [
                "First question.",
                "Observation:\nfirst answer",
                "Second question.",
                "Observation:\nsecond answer",
            ],
        )

    def test_session_max_turns_trims_old_turns(self) -> None:
        session = AgentSession(max_turns=1)
        session.add_task("First question.")
        session.append(StepRecord(step_number=1, observation="first answer"))
        session.add_task("Second question.")
        session.append(StepRecord(step_number=1, observation="second answer"))

        messages = session.replay()

        self.assertEqual(
            [message.content for message in messages],
            [
                "Second question.",
                "Observation:\nsecond answer",
            ],
        )

    def test_session_clear_can_keep_task(self) -> None:
        session = AgentSession(task="Keep this task.")
        session.append(StepRecord(step_number=1, observation="first"))

        session.clear(keep_task=True)

        self.assertEqual(session.task, "Keep this task.")
        self.assertEqual(session.steps, [])


if __name__ == "__main__":
    unittest.main()
