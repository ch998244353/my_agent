from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    AgentMemory,
    AgentSession,
    ChatMessage,
    StepRecord,
    ToolCall,
)


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
