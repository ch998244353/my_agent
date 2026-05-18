from __future__ import annotations

import inspect
import unittest
from typing import Any

from mini_smolagent.tool_schema import annotation_to_json_schema, schema_type_label


class ToolSchemaTestCase(unittest.TestCase):
    def test_annotation_to_json_schema_maps_basic_python_types(self) -> None:
        self.assertEqual(annotation_to_json_schema(str), {"type": "string"})
        self.assertEqual(annotation_to_json_schema(int), {"type": "integer"})
        self.assertEqual(annotation_to_json_schema(float), {"type": "number"})
        self.assertEqual(annotation_to_json_schema(bool), {"type": "boolean"})

    def test_annotation_to_json_schema_uses_permissive_schema_for_any_or_empty(self) -> None:
        self.assertEqual(annotation_to_json_schema(Any), {})
        self.assertEqual(annotation_to_json_schema(inspect._empty), {})

    def test_schema_type_label_returns_display_friendly_type_name(self) -> None:
        self.assertEqual(schema_type_label({"type": "string"}), "string")
        self.assertEqual(schema_type_label({"type": "integer"}), "integer")
        self.assertEqual(schema_type_label({}), "any")


if __name__ == "__main__":
    unittest.main()
