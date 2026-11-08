from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.output import (  # noqa: E402
    StructuredOutputError,
    StructuredOutputRefusalError,
    output_schema_from_output_type,
    parse_structured_output,
    set_structured_final_answer,
)
from agents.contracts import ModelResponse  # noqa: E402
from agents.run_state import RunState  # noqa: E402


class OutputTestCase(unittest.TestCase):
    def test_output_schema_from_output_type_accepts_json_schema_dict(self) -> None:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
        }

        self.assertIs(output_schema_from_output_type(schema), schema)
        self.assertIsNone(output_schema_from_output_type(None))

    def test_parse_structured_output_validates_json_schema_shape(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
                "confidence": {"type": "number"},
                "count": {"type": "integer"},
                "safe": {"type": "boolean"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["answer", "confidence"],
            "additionalProperties": False,
        }

        parsed = parse_structured_output(
            (
                '{"answer": "done", "confidence": 0.9, "count": 2, '
                '"safe": true, "tags": ["final"], "metadata": {}}'
            ),
            schema,
        )

        self.assertEqual(
            parsed,
            {
                "answer": "done",
                "confidence": 0.9,
                "count": 2,
                "safe": True,
                "tags": ["final"],
                "metadata": {},
            },
        )

    def test_parse_structured_output_rejects_invalid_json(self) -> None:
        with self.assertRaises(StructuredOutputError) as context:
            parse_structured_output("{bad json", {"type": "object"})

        self.assertIn("valid JSON", str(context.exception))

    def test_parse_structured_output_rejects_missing_required_property(self) -> None:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }

        with self.assertRaises(StructuredOutputError) as context:
            parse_structured_output("{}", schema)

        self.assertIn("$.answer", str(context.exception))

    def test_parse_structured_output_rejects_unexpected_property(self) -> None:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "additionalProperties": False,
        }

        with self.assertRaises(StructuredOutputError) as context:
            parse_structured_output('{"answer": "done", "extra": true}', schema)

        self.assertIn("$.extra", str(context.exception))

    def test_parse_structured_output_rejects_type_mismatch(self) -> None:
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }

        with self.assertRaises(StructuredOutputError) as context:
            parse_structured_output('{"answer": 42}', schema)

        self.assertIn("Expected $.answer to be string", str(context.exception))

    def test_set_structured_final_answer_rejects_refusal_before_json_parse(self) -> None:
        run_state = RunState()
        model_response = ModelResponse(
            response_id="resp_refusal",
            output=[],
            output_text="I'm sorry, I cannot help with that.",
            tool_calls=[],
            refusal="I'm sorry, I cannot help with that.",
        )

        with self.assertRaises(StructuredOutputRefusalError) as context:
            set_structured_final_answer(
                model_response,
                {"type": "object"},
                run_state,
                step_number=1,
            )

        self.assertEqual(
            context.exception.refusal,
            "I'm sorry, I cannot help with that.",
        )
        self.assertIn("refused", str(context.exception))
        self.assertFalse(run_state.reached_final_answer)
        self.assertEqual(run_state.new_items, [])


if __name__ == "__main__":
    unittest.main()
