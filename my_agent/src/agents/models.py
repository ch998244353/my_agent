from __future__ import annotations

import json
from dataclasses import dataclass, field
from inspect import Parameter, signature
from typing import Any, Protocol, runtime_checkable

from .contracts import ChatMessage, ModelResponse, ToolCall, ToolSpec
from .model_settings import ModelSettings


@runtime_checkable
class ModelAdapter(Protocol):
    """同步运行循环需要的最小模型调用边界。"""

    def get_response(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
    ) -> ModelResponse:
        """执行一次模型请求，并返回标准化的模型响应。"""


def supports_model_adapter(model: Any) -> bool:
    """判断对象是否提供当前最小模型调用能力。"""
    return callable(getattr(model, "get_response", None))


def format_model_error(exc: BaseException) -> str:
    if isinstance(exc, ModelCallError):
        return str(exc)
    error_type = exc.__class__.__name__
    error_message = str(exc) or "<no message>"
    return f"Model call failed during model_call: {error_type}: {error_message}"


class ModelCallError(RuntimeError):
    def __init__(self, original: BaseException) -> None:
        self.original = original
        super().__init__(format_model_error(original))


def _accepts_model_settings(get_response: Any) -> bool:
    try:
        parameters = signature(get_response).parameters
    except (TypeError, ValueError):
        return True

    return "model_settings" in parameters or any(
        parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )


def call_model_response(
    model: Any,
    messages: list[ChatMessage],
    tool_specs: list[ToolSpec],
    model_settings: ModelSettings,
) -> ModelResponse:
    get_response = getattr(model, "get_response", None)
    if not callable(get_response):
        raise TypeError("Model does not provide a callable get_response().")

    if _accepts_model_settings(get_response):
        return get_response(
            messages,
            tool_specs,
            model_settings=model_settings,
        )
    return get_response(messages, tool_specs)


# 把你自己定义的 ToolSpec 转换成 OpenAI Responses API 可识别的工具格式。
def tool_spec_to_openai_tool(tool_spec: ToolSpec) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for argument in tool_spec.arguments:
        argument_schema = _argument_to_openai_schema(argument)
        argument_schema["description"] = argument.description

        properties[argument.name] = argument_schema
        if argument.required:
            required.append(argument.name)

    return {
        "type": "function",
        "name": tool_spec.name,
        "description": tool_spec.description,
        "parameters": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "strict": True,
    }


def _argument_to_openai_schema(argument: Any) -> dict[str, Any]:
    return dict(argument.schema)

# ChatMessage 转换成 OpenAI Responses API 的 input 消息格式
def chat_message_to_response_input(message: ChatMessage) -> dict[str, str]:
    if message.role in {"system", "user", "assistant"}:
        return {"role": message.role, "content": message.content}
    if message.role == "tool_call":
        return {"role": "assistant", "content": message.content}
    return {"role": "user", "content": message.content}


def _get_field(item: Any, field_name: str) -> Any:
    if isinstance(item, dict):
        return item.get(field_name)
    return getattr(item, field_name, None)

# 从模型返回的 output item 中解析出 ToolCall；如果不是 function_call 就返回 None。
def response_item_to_tool_call(item: Any) -> ToolCall | None:
    if _get_field(item, "type") != "function_call":
        return None

    arguments = _get_field(item, "arguments") or "{}"
    if isinstance(arguments, str):
        parsed_arguments = json.loads(arguments)
    else:
        parsed_arguments = dict(arguments)

    return ToolCall(
        tool_name=_get_field(item, "name"),
        arguments=parsed_arguments,
        call_id=_get_field(item, "call_id"),
    )

# 具执行结果转换成 OpenAI Responses API 需要的 function_call_output 格式
def tool_call_output_to_response_input(tool_call: ToolCall, output: Any) -> dict[str, str]:
    return {
        "type": "function_call_output",
        "call_id": tool_call.call_id,
        "output": str(output),
    }


def response_schema_to_text_format(
    name: str,
    schema: dict[str, Any],
    strict: bool = True,
) -> dict[str, Any]:
    return {
        "format": {
            "type": "json_schema",
            "name": name,
            "schema": schema,
            "strict": strict,
        }
    }


def _apply_model_settings(
    request_kwargs: dict[str, Any],
    model_settings: ModelSettings | None,
) -> None:
    if model_settings is None:
        return
    if model_settings.temperature is not None:
        request_kwargs["temperature"] = model_settings.temperature
    if model_settings.top_p is not None:
        request_kwargs["top_p"] = model_settings.top_p
    if model_settings.tool_choice is not None:
        request_kwargs["tool_choice"] = model_settings.tool_choice
    if model_settings.max_output_tokens is not None:
        request_kwargs["max_output_tokens"] = model_settings.max_output_tokens
    if model_settings.store is not None:
        request_kwargs["store"] = model_settings.store


# 从模型 response 中提取最终文本 output_text；如果没有直接字段，就从 output.content.text 里拼接。
def response_output_text(response: Any) -> str | None:
    output_text = _get_field(response, "output_text")
    if isinstance(output_text, str):
        return output_text

    text_parts: list[str] = []
    for output_item in _get_field(response, "output") or []:
        for content_item in _get_field(output_item, "content") or []:
            text = _get_field(content_item, "text")
            if isinstance(text, str):
                text_parts.append(text)

    if not text_parts:
        return None
    return "\n".join(text_parts)


@dataclass
class OpenAIResponsesModel:
    model: str = "gpt-5.4"
    client: Any | None = None
    response_schema: dict[str, Any] | None = None
    response_schema_name: str = "final_response"
    response_schema_strict: bool = True
    last_response: Any | None = None
    last_input: list[dict[str, Any]] | None = None
    last_tools: list[dict[str, Any]] | None = None
    last_output_text: str | None = None
    previous_response_id: str | None = None
    pending_tool_outputs: list[dict[str, str]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.client is not None:
            return
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIResponsesModel needs the openai package. "
                "Install it before using the real API client."
            ) from exc

        self.client = OpenAI()

    def record_tool_output(self, tool_call: ToolCall, output: Any) -> None:
        self.pending_tool_outputs.append(
            tool_call_output_to_response_input(tool_call, output)
        )

    def decide(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
    ) -> ToolCall | None:
        model_response = self.get_response(messages, tool_specs)
        if model_response.tool_calls:
            return model_response.tool_calls[0]
        return None
    

    # 统一执行一次模型请求，并返回完整 ModelResponse
    def get_response(
        self,
        messages: list[ChatMessage],
        tool_specs: list[ToolSpec],
        model_settings: ModelSettings | None = None,
    ) -> ModelResponse:
        if self.previous_response_id is not None and self.pending_tool_outputs:
            response_input = list(self.pending_tool_outputs)
        else:
            response_input = [
                chat_message_to_response_input(message) for message in messages
            ]

        tools = [tool_spec_to_openai_tool(tool_spec) for tool_spec in tool_specs]
        self.last_input = response_input
        self.last_tools = tools

        request_kwargs: dict[str, Any] = {
            "model": self.model,
            "input": response_input,
            "tools": tools,
            "tool_choice": "auto",
        }
        _apply_model_settings(request_kwargs, model_settings)

        if self.response_schema is not None:
            request_kwargs["text"] = response_schema_to_text_format(
                name=self.response_schema_name,
                schema=self.response_schema,
                strict=self.response_schema_strict,
            )

        if self.previous_response_id is not None:
            request_kwargs["previous_response_id"] = self.previous_response_id

        if self.client is None:
            raise RuntimeError("OpenAIResponsesModel client is not initialized.")

        response = self.client.responses.create(**request_kwargs)
        self.last_response = response
        self.last_output_text = response_output_text(response)
        self.previous_response_id = (
            _get_field(response, "id") or self.previous_response_id
        )
        self.pending_tool_outputs.clear()

        output = list(_get_field(response, "output") or [])
        tool_calls = [
            tool_call
            for item in output
            if (tool_call := response_item_to_tool_call(item)) is not None
        ]
        response_id = _get_field(response, "id")

        return ModelResponse(
            response_id=response_id,
            output=output,
            output_text=self.last_output_text,
            tool_calls=tool_calls,
            raw=response,
        )
