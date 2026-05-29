from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents  # noqa: E402
from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    AgentSession,
    ModelResponse,
    RunConfig,
)


class TextResponseModel:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.last_messages = []

    def get_response(self, messages, tool_specs):
        self.last_messages = list(messages)
        return ModelResponse(
            response_id="resp_text",
            output=[],
            output_text=self.output_text,
            tool_calls=[],
        )


class ChatEntrypointTestCase(unittest.TestCase):
    def test_run_chat_turn_is_public_and_returns_text_final_output(self) -> None:
        run_chat_turn = getattr(agents, "run_chat_turn", None)
        self.assertIsNotNone(run_chat_turn)
        assert run_chat_turn is not None
        self.assertIn("run_chat_turn", agents.__all__)
        agent = Agent(
            memory=AgentMemory(),
            model=TextResponseModel("Hello from chat."),
        )

        result = run_chat_turn(agent, "Say hello.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "Hello from chat.")
        self.assertEqual(result.input, "Say hello.")

    def test_run_chat_turn_can_attach_session_without_losing_config(self) -> None:
        run_chat_turn = getattr(agents, "run_chat_turn", None)
        self.assertIsNotNone(run_chat_turn)
        assert run_chat_turn is not None
        session = AgentSession()
        session.add_task("My name is Ada.")
        model = TextResponseModel("Your name is Ada.")
        agent = Agent(memory=AgentMemory(), model=model)

        result = run_chat_turn(
            agent,
            "What is my name?",
            session=session,
            config=RunConfig(max_turns=1),
        )

        self.assertEqual(result.max_turns, 1)
        self.assertEqual(
            [message.content for message in model.last_messages],
            ["My name is Ada.", "What is my name?"],
        )
        self.assertEqual(session.replay()[-1].role, "assistant")
        self.assertEqual(session.replay()[-1].content, "Your name is Ada.")

    def test_run_chat_turn_exposes_max_turns_stop_reason(self) -> None:
        run_chat_turn = getattr(agents, "run_chat_turn", None)
        chat_stop_reason = getattr(agents, "chat_stop_reason", None)
        self.assertIsNotNone(run_chat_turn)
        self.assertIsNotNone(chat_stop_reason)
        assert run_chat_turn is not None
        assert chat_stop_reason is not None
        self.assertIn("chat_stop_reason", agents.__all__)
        agent = Agent(
            memory=AgentMemory(),
            model=TextResponseModel("should not be called"),
        )

        result = run_chat_turn(agent, "Stop before model.", max_turns=0)

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.current_turn, 0)
        self.assertEqual(result.max_turns, 0)
        self.assertEqual(chat_stop_reason(result), "max_turns_reached")


if __name__ == "__main__":
    unittest.main()
