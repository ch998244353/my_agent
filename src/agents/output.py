from __future__ import annotations

import json
from typing import Any

from .contracts import ModelResponse, RunItem
from .run_state import RunState

# 结构化输出错误
class StructuredOutputError(ValueError):
    """Raised when a model response does not match the configured output schema."""


class StructuredOutputRefusalError(StructuredOutputError):
    """Raised when a model refusal prevents structured output parsing."""

    def __init__(self, refusal: str) -> None:
        self.refusal = refusal
        super().__init__(f"Model refused to provide structured output: {refusal}")


def output_schema_from_output_type(
    output_type: type[Any] | dict[str, Any] | None,
) -> dict[str, Any] | None:
    if isinstance(output_type, dict):
        return output_type
    return None

# 把模型文本解析成 JSON，并交给 schema 校验
def parse_structured_output(output_text: str, schema: dict[str, Any]) -> Any:
    try:
        parsed = json.loads(output_text)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError(f"Model output is not valid JSON: {exc}") from exc

    _validate_value(parsed, schema, "$")
    return parsed

# 当模型没有工具调用、并且配置了 output_type 时，把合法结构化输出写入 RunState.final_answer 和 final_output
def set_structured_final_answer(
    model_response: ModelResponse,
    output_type: type[Any] | dict[str, Any] | None,
    run_state: RunState,
    step_number: int,
) -> bool:
    if model_response.tool_calls:
        return False
    schema = output_schema_from_output_type(output_type)
    if schema is None:
        return False
    if model_response.refusal is not None and model_response.refusal.strip():
        raise StructuredOutputRefusalError(model_response.refusal)
    if model_response.output_text is None:
        return False

    final_answer = parse_structured_output(model_response.output_text, schema)
    run_state.final_answer = final_answer
    run_state.reached_final_answer = True
    run_state.new_items.append(
        RunItem(
            item_type="final_output",
            step_number=step_number,
            payload=final_answer,
        )
    )
    return True

#  JSON Schema 校验层
def _validate_value(value: Any, schema: dict[str, Any], path: str) -> None:
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        if not any(_matches_type(value, item_type) for item_type in schema_type):
            expected = " or ".join(str(item_type) for item_type in schema_type)
            raise StructuredOutputError(f"Expected {path} to be {expected}")
    elif schema_type is not None:
        _validate_type(value, schema_type, path)

    if schema_type == "object":
        _validate_object(value, schema, path)
    elif schema_type == "array":
        _validate_array(value, schema, path)


def _validate_object(value: Any, schema: dict[str, Any], path: str) -> None:
    if not isinstance(value, dict):
        raise StructuredOutputError(f"Expected {path} to be object")

    properties = schema.get("properties", {})
    required = schema.get("required", [])

    for field_name in required:
        if field_name not in value:
            raise StructuredOutputError(f"Missing required property {path}.{field_name}")

    if schema.get("additionalProperties") is False:
        unexpected_fields = sorted(set(value) - set(properties))
        if unexpected_fields:
            raise StructuredOutputError(
                f"Unexpected property {path}.{unexpected_fields[0]}"
            )

    for field_name, field_schema in properties.items():
        if field_name in value and isinstance(field_schema, dict):
            _validate_value(value[field_name], field_schema, f"{path}.{field_name}")


def _validate_array(value: Any, schema: dict[str, Any], path: str) -> None:
    if not isinstance(value, list):
        raise StructuredOutputError(f"Expected {path} to be array")

    item_schema = schema.get("items")
    if not isinstance(item_schema, dict):
        return

    for index, item in enumerate(value):
        _validate_value(item, item_schema, f"{path}[{index}]")


def _validate_type(value: Any, schema_type: str, path: str) -> None:
    if _matches_type(value, schema_type):
        return
    raise StructuredOutputError(f"Expected {path} to be {schema_type}")


def _matches_type(value: Any, schema_type: str) -> bool:
    if schema_type == "object":
        return isinstance(value, dict)
    if schema_type == "array":
        return isinstance(value, list)
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return True
