from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    ChatMessage,
    ModelCallError,
    ModelResponse,
    ModelResponseError,
    ModelResponseParseError,
    ModelResponseStatusError,
    ModelSettings,
    OpenAIResponsesModel,
    ToolArgument,
    ToolSpec,
)
from agents.models import (  # noqa: E402
    chat_message_to_response_input,
    parse_tool_call_arguments,
    response_item_to_tool_call,
    response_refusal_text,
    response_output_text,
    response_schema_to_text_format,
    tool_call_output_to_response_input,
    tool_spec_to_openai_tool,
    validate_response_status,
    format_model_error,
)


class FakeResponsesClient:
    def __init__(self, output, response_id: str = "resp_1", response_fields=None):
        if output and isinstance(output[0], list):
            self.outputs = output
        else:
            self.outputs = [output]
        self.response_id = response_id
        self.response_fields = dict(response_fields or {})
        self.last_request = None
        self.requests = []

    def create(self, **kwargs):
        self.last_request = kwargs
        self.requests.append(kwargs)
        output_index = min(len(self.requests) - 1, len(self.outputs) - 1)
        return SimpleNamespace(
            id=f"{self.response_id}_{len(self.requests)}",
            output=self.outputs[output_index],
            **self.response_fields,
        )


class FakeOpenAIClient:
    def __init__(self, output, response_id: str = "resp_1", response_fields=None):
        self.responses = FakeResponsesClient(
            output,
            response_id=response_id,
            response_fields=response_fields,
        )


class DumpOnlyObject:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return dict(self._payload)


class OpenAIResponsesModelTestCase(unittest.TestCase):
    def test_model_call_error_keeps_legacy_provider_error_message(self) -> None:
        original = ValueError("missing API key")

        error = ModelCallError(original)

        self.assertIs(error.original, original)
        self.assertEqual(
            str(error),
            "Model call failed during model_call: ValueError: missing API key",
        )

    def test_model_response_errors_describe_parse_and_status_failures(self) -> None:
        original = ValueError("invalid JSON")
        parse_error = ModelResponseParseError(
            "Could not parse response output.",
            original=original,
        )
        status_error = ModelResponseStatusError("Response status was incomplete.")

        self.assertIsInstance(parse_error, ModelResponseError)
        self.assertIs(parse_error.original, original)
        self.assertIsNone(status_error.original)
        self.assertEqual(
            format_model_error(parse_error),
            "ModelResponseParseError: Could not parse response output.",
        )
        self.assertEqual(
            format_model_error(status_error),
            "ModelResponseStatusError: Response status was incomplete.",
        )

    def test_tool_spec_to_openai_tool_builds_function_schema(self) -> None:
        tool_spec = ToolSpec(
            name="get_weather",
            description="Get weather for a city.",
            arguments=[
                ToolArgument(
                    name="city",
                    description="City name.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        )

        openai_tool = tool_spec_to_openai_tool(tool_spec)

        self.assertEqual(openai_tool["type"], "function")
        self.assertEqual(openai_tool["name"], "get_weather")
        self.assertTrue(openai_tool["strict"])
        self.assertEqual(
            openai_tool["parameters"]["properties"]["city"]["type"],
            "string",
        )
        self.assertEqual(openai_tool["parameters"]["required"], ["city"])
        self.assertFalse(openai_tool["parameters"]["additionalProperties"])

    def test_tool_spec_to_openai_tool_uses_argument_schema_and_required_flag(self) -> None:
        tool_spec = ToolSpec(
            name="search_docs",
            description="Search documents.",
            arguments=[
                ToolArgument(
                    name="query",
                    description="Search query.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="mode",
                    description="Search mode.",
                    schema={"type": "string"},
                    required=False,
                ),
            ],
            returns="array",
        )

        openai_tool = tool_spec_to_openai_tool(tool_spec)

        parameters = openai_tool["parameters"]
        self.assertEqual(parameters["required"], ["query"])
        self.assertEqual(
            parameters["properties"]["mode"],
            {
                "type": "string",
                "description": "Search mode.",
            },
        )

    def test_tool_spec_to_openai_tool_prefers_argument_schema(self) -> None:
        tool_spec = ToolSpec(
            name="rank_docs",
            description="Rank documents.",
            arguments=[
                ToolArgument(
                    name="tags",
                    description="Tags to include.",
                    schema={"type": "array", "items": {"type": "string"}},
                ),
                ToolArgument(
                    name="mode",
                    description="Ranking mode.",
                    schema={"type": "string", "enum": ["fast", "deep"]},
                ),
            ],
            returns="array",
        )

        openai_tool = tool_spec_to_openai_tool(tool_spec)

        properties = openai_tool["parameters"]["properties"]
        self.assertEqual(
            properties["tags"],
            {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tags to include.",
            },
        )
        self.assertEqual(
            properties["mode"],
            {
                "type": "string",
                "enum": ["fast", "deep"],
                "description": "Ranking mode.",
            },
        )

    def test_chat_message_to_response_input_keeps_core_roles(self) -> None:
        user_message = ChatMessage(role="user", content="What is the weather?")
        tool_response = ChatMessage(role="tool_response", content="Observation:\n18C")

        user_input = chat_message_to_response_input(user_message)
        tool_response_input = chat_message_to_response_input(tool_response)

        self.assertEqual(user_input, {"role": "user", "content": "What is the weather?"})
        self.assertEqual(
            tool_response_input,
            {"role": "user", "content": "Observation:\n18C"},
        )

    def test_response_item_to_tool_call_parses_function_call(self) -> None:
        response_item = {
            "type": "function_call",
            "name": "get_weather",
            "arguments": "{\"city\": \"Shanghai\"}",
            "call_id": "call_1",
        }

        tool_call = response_item_to_tool_call(response_item)

        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool_name, "get_weather")
        self.assertEqual(tool_call.arguments, {"city": "Shanghai"})
        self.assertEqual(tool_call.call_id, "call_1")

    def test_response_item_to_tool_call_reads_model_dump_item(self) -> None:
        response_item = DumpOnlyObject(
            {
                "type": "function_call",
                "name": "get_weather",
                "arguments": "{\"city\": \"Shanghai\"}",
                "call_id": "call_1",
            }
        )

        tool_call = response_item_to_tool_call(response_item)

        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool_name, "get_weather")
        self.assertEqual(tool_call.arguments, {"city": "Shanghai"})
        self.assertEqual(tool_call.call_id, "call_1")

    def test_parse_tool_call_arguments_rejects_invalid_json(self) -> None:
        with self.assertRaises(ModelResponseParseError) as raised:
            parse_tool_call_arguments("{bad json")

        self.assertIn("Invalid tool call arguments JSON", str(raised.exception))

    def test_parse_tool_call_arguments_rejects_non_object_arguments(self) -> None:
        with self.assertRaises(ModelResponseParseError) as raised:
            parse_tool_call_arguments("[1, 2]")

        self.assertIn("Tool call arguments must be a JSON object", str(raised.exception))

    def test_response_item_to_tool_call_requires_name_and_call_id(self) -> None:
        with self.assertRaises(ModelResponseParseError) as raised:
            response_item_to_tool_call(
                {
                    "type": "function_call",
                    "arguments": "{}",
                    "call_id": "call_1",
                }
            )

        self.assertIn("missing string name", str(raised.exception))

        with self.assertRaises(ModelResponseParseError) as raised:
            response_item_to_tool_call(
                {
                    "type": "function_call",
                    "name": "get_weather",
                    "arguments": "{}",
                }
            )

        self.assertIn("missing string call_id", str(raised.exception))

    def test_tool_call_output_to_response_input_keeps_call_id(self) -> None:
        response_item = tool_call_output_to_response_input(
            tool_call=response_item_to_tool_call(
                {
                    "type": "function_call",
                    "name": "get_weather",
                    "arguments": "{\"city\": \"Shanghai\"}",
                    "call_id": "call_1",
                }
            ),
            output="Weather in Shanghai: 18C and cloudy.",
        )

        self.assertEqual(
            response_item,
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "Weather in Shanghai: 18C and cloudy.",
            },
        )

    def test_response_schema_to_text_format_builds_json_schema_format(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        }

        text_format = response_schema_to_text_format(
            name="weather_answer",
            schema=schema,
        )

        self.assertEqual(
            text_format,
            {
                "format": {
                    "type": "json_schema",
                    "name": "weather_answer",
                    "schema": schema,
                    "strict": True,
                }
            },
        )

    def test_response_output_text_reads_message_content(self) -> None:
        response = SimpleNamespace(
            output=[
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "First part."},
                        {"type": "output_text", "text": "Second part."},
                    ],
                }
            ]
        )

        self.assertEqual(response_output_text(response), "First part.\nSecond part.")

    def test_response_output_text_reads_model_dump_items(self) -> None:
        response = DumpOnlyObject(
            {
                "output": [
                    DumpOnlyObject(
                        {
                            "type": "message",
                            "content": [
                                DumpOnlyObject(
                                    {"type": "output_text", "text": "First part."}
                                ),
                                DumpOnlyObject(
                                    {"type": "output_text", "text": "Second part."}
                                ),
                            ],
                        }
                    )
                ]
            }
        )

        self.assertEqual(response_output_text(response), "First part.\nSecond part.")

    def test_response_refusal_text_reads_refusal_content(self) -> None:
        response = SimpleNamespace(
            output=[
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "refusal",
                            "refusal": "I cannot help with that request.",
                        }
                    ],
                }
            ]
        )

        self.assertEqual(
            response_refusal_text(response),
            "I cannot help with that request.",
        )

    def test_validate_response_status_rejects_failed_response(self) -> None:
        response = SimpleNamespace(
            status="failed",
            error={"message": "quota exceeded"},
            incomplete_details=None,
        )

        with self.assertRaises(ModelResponseStatusError) as raised:
            validate_response_status(response)

        self.assertIn("failed", str(raised.exception))
        self.assertIn("quota exceeded", str(raised.exception))

    def test_openai_responses_model_decide_returns_first_tool_call(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "function_call",
                    "name": "get_weather",
                    "arguments": "{\"city\": \"Shanghai\"}",
                    "call_id": "call_1",
                }
            ]
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)
        tool_spec = ToolSpec(
            name="get_weather",
            description="Get weather for a city.",
            arguments=[
                ToolArgument(
                    name="city",
                    description="City name.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        )

        tool_call = model.decide(
            messages=[ChatMessage(role="user", content="Weather in Shanghai?")],
            tool_specs=[tool_spec],
        )

        self.assertIsNotNone(tool_call)
        self.assertEqual(tool_call.tool_name, "get_weather")
        self.assertEqual(tool_call.arguments, {"city": "Shanghai"})
        self.assertEqual(fake_client.responses.last_request["model"], "gpt-test")
        self.assertEqual(fake_client.responses.last_request["tool_choice"], "auto")
        self.assertNotIn("parallel_tool_calls", fake_client.responses.last_request)
        self.assertNotIn("reasoning", fake_client.responses.last_request)
        self.assertEqual(
            fake_client.responses.last_request["tools"][0]["name"],
            "get_weather",
        )

    def test_openai_responses_model_get_response_returns_full_model_response(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "function_call",
                    "name": "get_weather",
                    "arguments": "{\"city\": \"Shanghai\"}",
                    "call_id": "call_1",
                },
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "Need weather data."}
                    ],
                },
            ],
            response_id="resp_weather",
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)
        tool_spec = ToolSpec(
            name="get_weather",
            description="Get weather for a city.",
            arguments=[
                ToolArgument(
                    name="city",
                    description="City name.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        )

        model_response = model.get_response(
            messages=[ChatMessage(role="user", content="Weather in Shanghai?")],
            tool_specs=[tool_spec],
        )

        self.assertIsInstance(model_response, ModelResponse)
        self.assertEqual(model_response.response_id, "resp_weather_1")
        self.assertEqual(model_response.output_text, "Need weather data.")
        self.assertEqual(len(model_response.output), 2)
        self.assertEqual(len(model_response.tool_calls), 1)
        self.assertEqual(model_response.tool_calls[0].tool_name, "get_weather")
        self.assertIs(model_response.raw, model.last_response)

    def test_openai_responses_model_rejects_incomplete_response(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Partial text."}],
                }
            ],
            response_fields={
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
            },
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)

        with self.assertRaises(ModelResponseStatusError) as raised:
            model.get_response(
                messages=[ChatMessage(role="user", content="Say hi.")],
                tool_specs=[],
            )

        self.assertIn("incomplete", str(raised.exception))
        self.assertIn("max_output_tokens", str(raised.exception))

    def test_openai_responses_model_records_refusal(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "refusal",
                            "refusal": "I cannot help with that request.",
                        }
                    ],
                }
            ],
            response_fields={"status": "completed"},
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)

        model_response = model.get_response(
            messages=[ChatMessage(role="user", content="Unsafe request.")],
            tool_specs=[],
        )

        self.assertIsNone(model_response.output_text)
        self.assertEqual(
            model_response.refusal,
            "I cannot help with that request.",
        )

    def test_openai_responses_model_decide_returns_none_without_tool_call(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "No tool needed."}],
                }
            ]
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)

        tool_call = model.decide(
            messages=[ChatMessage(role="user", content="Say hi.")],
            tool_specs=[],
        )

        self.assertIsNone(tool_call)

    def test_openai_responses_model_applies_extended_model_settings(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Done."}],
                }
            ]
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)
        tool_spec = ToolSpec(
            name="search_docs",
            description="Search docs.",
            arguments=[],
            returns="array",
        )

        model.get_response(
            messages=[ChatMessage(role="user", content="Search docs.")],
            tool_specs=[tool_spec],
            model_settings=ModelSettings(
                parallel_tool_calls=True,
                verbosity="high",
                reasoning={"effort": "low"},
            ),
        )

        self.assertTrue(fake_client.responses.last_request["parallel_tool_calls"])
        self.assertEqual(
            fake_client.responses.last_request["text"],
            {"verbosity": "high"},
        )
        self.assertEqual(
            fake_client.responses.last_request["reasoning"],
            {"effort": "low"},
        )
        self.assertFalse(model.last_request_summary["has_schema"])

    def test_openai_responses_model_merges_verbosity_with_response_schema(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "{\"answer\": \"ok\"}"}],
                }
            ]
        )
        response_schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        }
        model = OpenAIResponsesModel(
            model="gpt-test",
            client=fake_client,
            response_schema=response_schema,
            response_schema_name="mini_agent_answer",
        )

        model.get_response(
            messages=[ChatMessage(role="user", content="Answer as JSON.")],
            tool_specs=[],
            model_settings=ModelSettings(verbosity="low"),
        )

        self.assertEqual(
            fake_client.responses.last_request["text"],
            {
                "format": {
                    "type": "json_schema",
                    "name": "mini_agent_answer",
                    "schema": response_schema,
                    "strict": True,
                },
                "verbosity": "low",
            },
        )

    def test_openai_responses_model_sends_response_schema_as_text_format(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "{\"answer\": \"No tool needed.\"}",
                        }
                    ],
                }
            ]
        )
        response_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        }
        model = OpenAIResponsesModel(
            model="gpt-test",
            client=fake_client,
            response_schema=response_schema,
            response_schema_name="mini_agent_answer",
        )

        tool_call = model.decide(
            messages=[ChatMessage(role="user", content="Say hi as JSON.")],
            tool_specs=[],
        )

        self.assertIsNone(tool_call)
        self.assertEqual(
            fake_client.responses.last_request["text"],
            {
                "format": {
                    "type": "json_schema",
                    "name": "mini_agent_answer",
                    "schema": response_schema,
                    "strict": True,
                }
            },
        )
        self.assertEqual(model.last_output_text, "{\"answer\": \"No tool needed.\"}")

    def test_openai_responses_model_records_safe_request_summary(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "{\"answer\": \"ok\"}"}],
                }
            ]
        )
        response_schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        }
        tool_spec = ToolSpec(
            name="lookup_secret",
            description="Look up private data.",
            arguments=[],
            returns="object",
        )
        model = OpenAIResponsesModel(
            model="gpt-test",
            client=fake_client,
            response_schema=response_schema,
        )

        model.get_response(
            messages=[
                ChatMessage(role="system", content="private system instruction"),
                ChatMessage(role="user", content="my secret account is 123"),
            ],
            tool_specs=[tool_spec],
        )

        self.assertEqual(
            model.last_request_summary,
            {
                "model": "gpt-test",
                "input_count": 2,
                "tool_count": 1,
                "has_schema": True,
                "uses_previous_response_id": False,
            },
        )
        summary_text = str(model.last_request_summary)
        self.assertNotIn("my secret account is 123", summary_text)
        self.assertNotIn("lookup_secret", summary_text)

    def test_openai_responses_model_uses_local_history_for_plain_chat_turns(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Hello."}],
                    }
                ],
                [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Again."}],
                    }
                ],
            ],
            response_id="resp_chat",
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)

        model.get_response(
            messages=[ChatMessage(role="user", content="Say hello.")],
            tool_specs=[],
        )
        model.get_response(
            messages=[
                ChatMessage(role="user", content="Say hello."),
                ChatMessage(role="assistant", content="Hello."),
                ChatMessage(role="user", content="Say it again."),
            ],
            tool_specs=[],
        )

        second_request = fake_client.responses.requests[1]
        self.assertNotIn("previous_response_id", second_request)
        self.assertEqual(
            second_request["input"],
            [
                {"role": "user", "content": "Say hello."},
                {"role": "assistant", "content": "Hello."},
                {"role": "user", "content": "Say it again."},
            ],
        )

    def test_openai_responses_model_continues_with_previous_response_id(self) -> None:
        fake_client = FakeOpenAIClient(
            output=[
                [
                    {
                        "type": "function_call",
                        "name": "get_weather",
                        "arguments": "{\"city\": \"Shanghai\"}",
                        "call_id": "call_1",
                    }
                ],
                [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Shanghai is 18C and cloudy.",
                            }
                        ],
                    }
                ],
            ],
            response_id="resp_weather",
        )
        model = OpenAIResponsesModel(model="gpt-test", client=fake_client)
        tool_spec = ToolSpec(
            name="get_weather",
            description="Get weather for a city.",
            arguments=[
                ToolArgument(
                    name="city",
                    description="City name.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        )

        tool_call = model.decide(
            messages=[ChatMessage(role="user", content="Weather in Shanghai?")],
            tool_specs=[tool_spec],
        )
        model.record_tool_output(tool_call, "Weather in Shanghai: 18C and cloudy.")
        next_tool_call = model.decide(
            messages=[
                ChatMessage(role="user", content="Weather in Shanghai?"),
                ChatMessage(
                    role="tool_response",
                    content="Observation:\nWeather in Shanghai: 18C and cloudy.",
                ),
            ],
            tool_specs=[tool_spec],
        )

        self.assertIsNone(next_tool_call)
        self.assertEqual(
            fake_client.responses.requests[1]["previous_response_id"],
            "resp_weather_1",
        )
        self.assertEqual(
            fake_client.responses.requests[1]["input"],
            [
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "Weather in Shanghai: 18C and cloudy.",
                }
            ],
        )
        self.assertEqual(
            model.last_request_summary,
            {
                "model": "gpt-test",
                "input_count": 1,
                "tool_count": 1,
                "has_schema": False,
                "uses_previous_response_id": True,
            },
        )


if __name__ == "__main__":
    unittest.main()
