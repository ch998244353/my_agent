from __future__ import annotations

import inspect
import unittest
from typing import Any, Literal, Optional

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

    def test_annotation_to_json_schema_maps_list_items(self) -> None:
        self.assertEqual(
            annotation_to_json_schema(list[str]),
            {"type": "array", "items": {"type": "string"}},
        )

    def test_annotation_to_json_schema_maps_dict_values(self) -> None:
        self.assertEqual(
            annotation_to_json_schema(dict[str, int]),
            {"type": "object", "additionalProperties": {"type": "integer"}},
        )
        self.assertEqual(annotation_to_json_schema(dict[str, Any]), {"type": "object"})

    def test_annotation_to_json_schema_maps_optional_values(self) -> None:
        self.assertEqual(
            annotation_to_json_schema(Optional[str]),
            {"type": ["string", "null"]},
        )
        self.assertEqual(
            annotation_to_json_schema(str | None),
            {"type": ["string", "null"]},
        )

    def test_schema_type_label_formats_container_and_nullable_types(self) -> None:
        self.assertEqual(
            schema_type_label({"type": "array", "items": {"type": "string"}}),
            "array[string]",
        )
        self.assertEqual(schema_type_label({"type": ["string", "null"]}), "string|null")

    def test_annotation_to_json_schema_maps_literal_values_to_enum(self) -> None:
        self.assertEqual(
            annotation_to_json_schema(Literal["web", "local"]),
            {"type": "string", "enum": ["web", "local"]},
        )
        self.assertEqual(
            annotation_to_json_schema(Literal[1, 2]),
            {"type": "integer", "enum": [1, 2]},
        )

    def test_annotation_to_json_schema_keeps_mixed_literal_values_as_enum_only(self) -> None:
        self.assertEqual(
            annotation_to_json_schema(Literal["web", 1]),
            {"enum": ["web", 1]},
        )


if __name__ == "__main__":
    unittest.main()
