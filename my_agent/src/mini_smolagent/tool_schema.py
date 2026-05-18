from __future__ import annotations

import inspect
from typing import Any


JSONSchema = dict[str, Any]


def annotation_to_json_schema(annotation: Any) -> JSONSchema:
    if annotation is inspect._empty or annotation is Any:
        return {}

    basic_type_map: dict[Any, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }
    schema_type = basic_type_map.get(annotation)
    if schema_type is None:
        return {}
    return {"type": schema_type}


def schema_type_label(schema: JSONSchema) -> str:
    schema_type = schema.get("type")
    if isinstance(schema_type, str):
        return schema_type
    return "any"
