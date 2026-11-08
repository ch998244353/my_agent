from __future__ import annotations

import inspect
from types import UnionType
from typing import Any, Literal, Union, get_args, get_origin


JSONSchema = dict[str, Any]

def annotation_to_json_schema(annotation: Any) -> JSONSchema:
    if annotation is inspect._empty or annotation is Any:
        return {}

    
    origin = get_origin(annotation)
    if origin in (list, tuple, set):
        return _array_schema(annotation)
    if origin is dict:
        return _object_schema(annotation)
    if origin is Literal:
        return _literal_schema(annotation)
    if origin in (Union, UnionType):
        return _union_schema(annotation)

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


# 将schema转换为具体字符串类型
def schema_type_label(schema: JSONSchema) -> str:
    schema_type = schema.get("type")
    if schema_type == "array":
        item_label = schema_type_label(schema.get("items", {}))
        return f"array[{item_label}]"
    if isinstance(schema_type, str):
        return schema_type
    if isinstance(schema_type, list):
        return "|".join(str(item_type) for item_type in schema_type)
    return "any"


# 处理 list[ ]  
def _array_schema(annotation: Any) -> JSONSchema:
    item_annotation = _first_type_argument(annotation)
    item_schema = annotation_to_json_schema(item_annotation)
    schema: JSONSchema = {"type": "array"}
    if item_schema:
        schema["items"] = item_schema
    return schema


# 处理 dict[ , ] 
def _object_schema(annotation: Any) -> JSONSchema:
    value_annotation = _second_type_argument(annotation) # 默认取出第二个,因为第一个默认字符串
    value_schema = annotation_to_json_schema(value_annotation)
    schema: JSONSchema = {"type": "object"}
    if value_schema:
        schema["additionalProperties"] = value_schema
    return schema


# 处理 Union[A, B] 和 A | B
def _union_schema(annotation: Any) -> JSONSchema:
    item_types: list[str] = []
    for item_annotation in get_args(annotation):
        if item_annotation is type(None):
            item_types.append("null")
            continue
        item_schema = annotation_to_json_schema(item_annotation)
        item_type = item_schema.get("type")
        if isinstance(item_type, str):
            item_types.append(item_type)
    if not item_types:
        return {}
    return {"type": item_types}


# 处理  Literal["web", "local"]
def _literal_schema(annotation: Any) -> JSONSchema:
    values = list(get_args(annotation))
    schema: JSONSchema = {"enum": values}
    literal_type = _literal_json_type(values)
    if literal_type is not None:
        schema["type"] = literal_type
    return schema


# Literal 的枚举值是否同一种类型
def _literal_json_type(values: list[Any]) -> str | None:
    if not values:
        return None
    if all(isinstance(value, str) for value in values):
        return "string"
    if all(isinstance(value, bool) for value in values):
        return "boolean"
    if all(isinstance(value, int) and not isinstance(value, bool) for value in values):
        return "integer"
    if all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in values
    ):
        return "number"
    return None


# 从类型注解里取出第一个、第二个类型参数
def _first_type_argument(annotation: Any) -> Any:
    args = get_args(annotation)
    return args[0] if args else Any

def _second_type_argument(annotation: Any) -> Any:
    args = get_args(annotation)
    return args[1] if len(args) >= 2 else Any
