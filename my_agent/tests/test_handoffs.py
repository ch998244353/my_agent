from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent.contracts import ToolCall  # noqa: E402
from mini_smolagent.handoffs import (  # noqa: E402
    HANDOFF_TOOL_PREFIX,
    handoff_map,
    handoff_target_for,
    handoff_tool_name,
    handoff_tool_specs,
    normalize_handoff_name,
)


@dataclass
class DummyAgent:
    name: str


class HandoffsTestCase(unittest.TestCase):
    def test_normalize_handoff_name_builds_tool_safe_name(self) -> None:
        self.assertEqual(normalize_handoff_name("Math Agent"), "math_agent")
        self.assertEqual(normalize_handoff_name("Research/QA Agent"), "research_qa_agent")
        self.assertEqual(normalize_handoff_name("!!!"), "agent")

    def test_handoff_tool_name_uses_transfer_prefix(self) -> None:
        agent = DummyAgent(name="Math Agent")

        self.assertEqual(
            handoff_tool_name(agent),
            f"{HANDOFF_TOOL_PREFIX}math_agent",
        )

    def test_handoff_tool_specs_describe_each_target_agent(self) -> None:
        math_agent = DummyAgent(name="Math Agent")
        research_agent = DummyAgent(name="Research Agent")

        tool_specs = handoff_tool_specs([math_agent, research_agent])

        self.assertEqual(
            [tool_spec.name for tool_spec in tool_specs],
            ["transfer_to_math_agent", "transfer_to_research_agent"],
        )
        self.assertEqual(tool_specs[0].description, "Hand off control to Math Agent.")
        self.assertEqual(tool_specs[0].arguments[0].name, "task")
        self.assertEqual(tool_specs[0].arguments[0].schema, {"type": "string"})
        self.assertEqual(tool_specs[0].returns, "object")

    def test_handoff_target_for_finds_matching_agent(self) -> None:
        math_agent = DummyAgent(name="Math Agent")
        research_agent = DummyAgent(name="Research Agent")
        action = ToolCall(
            tool_name="transfer_to_research_agent",
            arguments={"task": "Find sources."},
            call_id="call_1",
        )

        self.assertEqual(
            handoff_map([math_agent, research_agent])["transfer_to_math_agent"],
            math_agent,
        )
        self.assertEqual(
            handoff_target_for([math_agent, research_agent], action),
            research_agent,
        )
        self.assertIsNone(
            handoff_target_for(
                [math_agent],
                ToolCall("regular_tool", {}, "call_2"),
            )
        )


if __name__ == "__main__":
    unittest.main()
