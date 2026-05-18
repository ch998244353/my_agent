from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    Agent,
    AgentMemory,
    RunConfig,
    RunContextWrapper,
    Runner,
    ToolCall,
)


class ScriptedModel:
    def __init__(self, actions) -> None:
        self.actions = list(actions)
        self._index = 0

    def decide(self, messages, tool_specs):
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        return action


class RunContextTestCase(unittest.TestCase):
    def test_run_context_wrapper_keeps_context_usage_and_metadata(self) -> None:
        business_context = {"user_id": "user_123"}

        run_context = RunContextWrapper(
            context=business_context,
            metadata={"request_id": "req_1"},
        )

        run_context.usage["requests"] = 1
        run_context.metadata["phase"] = "test"

        self.assertIs(run_context.context, business_context)
        self.assertEqual(run_context.usage["requests"], 1)
        self.assertEqual(run_context.metadata["request_id"], "req_1")
        self.assertEqual(run_context.metadata["phase"], "test")

    def test_runner_creates_context_wrapper_from_run_config(self) -> None:
        business_context = {"tenant": "acme"}
        metadata = {"run_id": "run_1"}
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [
                    ToolCall("final_answer", {"answer": "done"}, "call_1"),
                ]
            ),
        )

        result = Runner.run_sync(
            agent,
            "Return done.",
            config=RunConfig(context=business_context, metadata=metadata),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertIs(result.context_wrapper.context, business_context)
        self.assertEqual(result.context_wrapper.metadata, metadata)
        self.assertIsNot(result.context_wrapper.metadata, metadata)
        self.assertEqual(result.context_wrapper.usage, {})

    def test_each_run_gets_a_distinct_context_wrapper(self) -> None:
        config = RunConfig(metadata={"shared": "config"})
        first_agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [ToolCall("final_answer", {"answer": "first"}, "call_1")]
            ),
        )
        second_agent = Agent(
            memory=AgentMemory(),
            model=ScriptedModel(
                [ToolCall("final_answer", {"answer": "second"}, "call_2")]
            ),
        )

        first_result = Runner.run_sync(first_agent, "First.", config=config)
        second_result = Runner.run_sync(second_agent, "Second.", config=config)

        self.assertIsNot(first_result.context_wrapper, second_result.context_wrapper)
        self.assertIsNot(
            first_result.context_wrapper.metadata,
            second_result.context_wrapper.metadata,
        )
        self.assertEqual(first_result.context_wrapper.metadata, {"shared": "config"})
        self.assertEqual(second_result.context_wrapper.metadata, {"shared": "config"})


if __name__ == "__main__":
    unittest.main()
