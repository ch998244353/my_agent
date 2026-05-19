from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    ChatMessage,
    ModelResponse,
    OpenAIResponsesModel,
    ToolArgument,
    ToolSpec,
)
from mini_smolagent.models import (  # noqa: E402
    chat_message_to_response_input,
    response_item_to_tool_call,
    response_output_text,
    response_schema_to_text_format,
    tool_call_output_to_response_input,
    tool_spec_to_openai_tool,
)


class FakeResponsesClient:
    def __init__(self, output, response_id: str = "resp_1"):
        if output and isinstance(output[0], list):
            self.outputs = output
        else:
            self.outputs = [output]
        self.response_id = response_id
        self.last_request = None
        self.requests = []

    def create(self, **kwargs):
        self.last_request = kwargs
        self.requests.append(kwargs)
        output_index = min(len(self.requests) - 1, len(self.outputs) - 1)
        return SimpleNamespace(
            id=f"{self.response_id}_{len(self.requests)}",
            output=self.outputs[output_index],
        )


class FakeOpenAIClient:
    def __init__(self, output, response_id: str = "resp_1"):
        self.responses = FakeResponsesClient(output, response_id=response_id)


class OpenAIResponsesModelTestCase(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
