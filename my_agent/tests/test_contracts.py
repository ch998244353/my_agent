from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import get_args, get_type_hints

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    AgentRunResult,
    ChatMessage,
    ModelResponse,
    RunItem,
    StepRecord,
    ToolArgument,
    ToolCall,
    ToolSpec,
    render_tool_signature,
    tool_to_prompt_text,
)
from agents.contracts import ToolApprovalRequest  # noqa: E402


class ContractsTestCase(unittest.TestCase):
    def test_run_result_uses_run_items_without_trace(self) -> None:
        run_result = AgentRunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=0,
            new_items=(RunItem("run_stopped", 1, "max_steps_reached"),),
        )

        self.assertFalse(hasattr(run_result, "trace"))

    def test_tool_spec_keeps_argument_names(self) -> None:
        tool_spec = ToolSpec(
            name="search_docs",
            description="Search local documents.",
            arguments=[
                ToolArgument(
                    name="query",
                    description="Search query text.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="top_k",
                    description="How many results to return.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="array",
        )

        self.assertEqual(tool_spec.argument_names(), ["query", "top_k"])

    def test_tool_argument_uses_schema_without_legacy_type(self) -> None:
        argument = ToolArgument(
            name="query",
            description="Search query text.",
            schema={"type": "string"},
        )

        self.assertEqual(argument.schema, {"type": "string"})
        self.assertFalse(hasattr(argument, "type"))

    def test_model_response_holds_raw_response_and_tool_calls(self) -> None:
        tool_call = ToolCall(
            tool_name="get_weather",
            arguments={"city": "Shanghai"},
            call_id="call_1",
        )
        raw_response = {"id": "resp_1"}

        model_response = ModelResponse(
            response_id="resp_1",
            output=[{"type": "function_call"}],
            output_text=None,
            tool_calls=[tool_call],
            raw=raw_response,
        )

        self.assertEqual(model_response.response_id, "resp_1")
        self.assertEqual(model_response.tool_calls, [tool_call])
        self.assertIs(model_response.raw, raw_response)

    def test_run_item_records_one_agent_event(self) -> None:
        tool_call = ToolCall("echo_text", {"text": "hello"}, "call_1")

        run_item = RunItem(
            item_type="tool_call",
            step_number=1,
            payload=tool_call,
        )

        self.assertEqual(run_item.item_type, "tool_call")
        self.assertEqual(run_item.step_number, 1)
        self.assertIs(run_item.payload, tool_call)

    def test_tool_approval_request_captures_pending_tool_call(self) -> None:
        request = ToolApprovalRequest(
            tool_name="delete_file",
            call_id="call_123",
            arguments={"path": "notes.txt"},
            reason="User approval is required before running this tool.",
        )

        self.assertEqual(request.tool_name, "delete_file")
        self.assertEqual(request.call_id, "call_123")
        self.assertEqual(request.arguments, {"path": "notes.txt"})
        self.assertEqual(
            request.reason,
            "User approval is required before running this tool.",
        )

    def test_run_item_type_includes_tool_approval_required(self) -> None:
        item_type = get_type_hints(RunItem)["item_type"]

        self.assertIn("tool_approval_required", get_args(item_type))

    def test_render_tool_signature(self) -> None:
        tool_spec = ToolSpec(
            name="calculator",
            description="Evaluate an arithmetic expression.",
            arguments=[
                ToolArgument(
                    name="expression",
                    description="Arithmetic expression.",
                    schema={"type": "string"},
                )
            ],
            returns="number",
        )

        self.assertEqual(
            render_tool_signature(tool_spec),
            "calculator(expression: string) -> number",
        )

    def test_render_tool_signature_uses_argument_schema_label(self) -> None:
        tool_spec = ToolSpec(
            name="collect_tags",
            description="Collect tags.",
            arguments=[
                ToolArgument(
                    name="tags",
                    description="Tags to collect.",
                    schema={"type": "array", "items": {"type": "string"}},
                )
            ],
            returns="array",
        )

        self.assertEqual(
            render_tool_signature(tool_spec),
            "collect_tags(tags: array[string]) -> array",
        )

    def test_tool_to_prompt_text_contains_core_fields(self) -> None:
        tool_spec = ToolSpec(
            name="translate_text",
            description="Translate input text to another language.",
            arguments=[
                ToolArgument(
                    name="text",
                    description="Input text.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="target_language",
                    description="Target language name.",
                    schema={"type": "string"},
                ),
            ],
            returns="string",
        )

        prompt_text = tool_to_prompt_text(tool_spec)

        self.assertIn("translate_text", prompt_text)
        self.assertIn("target_language(string)", prompt_text)
        self.assertIn("returns: string", prompt_text)

    def test_tool_to_prompt_text_uses_argument_schema_label(self) -> None:
        tool_spec = ToolSpec(
            name="filter_docs",
            description="Filter documents.",
            arguments=[
                ToolArgument(
                    name="tags",
                    description="Tags to match.",
                    schema={"type": "array", "items": {"type": "string"}},
                )
            ],
            returns="array",
        )

        prompt_text = tool_to_prompt_text(tool_spec)

        self.assertIn("tags(array[string])", prompt_text)

    def test_step_record_can_hold_messages_and_calls(self) -> None:
        step_record = StepRecord(
            step_number=2,
            messages=[ChatMessage(role="user", content="Search the docs.")],
            tool_calls=[
                ToolCall(
                    tool_name="search_docs",
                    arguments={"query": "memory", "top_k": 3},
                    call_id="call_2",
                )
            ],
            observation="Found 3 results.",
        )

        self.assertEqual(step_record.step_number, 2)
        self.assertEqual(step_record.messages[0].role, "user")
        self.assertEqual(step_record.tool_calls[0].arguments["top_k"], 3)
        self.assertEqual(step_record.observation, "Found 3 results.")


if __name__ == "__main__":
    unittest.main()
