from __future__ import annotations

import unittest
from types import SimpleNamespace

from agents import (
    Agent,
    AgentMemory,
    ChatMessage,
    ModelRequest,
    ModelSettings,
    OpenAIChatCompletionsModel,
    ToolArgument,
    ToolCall,
    ToolSpec,
)
from agents.model_events import ModelEventAccumulator


class FakeStream(list):
    def __init__(self, *items) -> None:
        super().__init__(items)
        self.closed = False

    def close(self) -> None:
        self.closed = True


def tool_delta(index, *, call_id=None, name=None, arguments=None):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def chunk(*, content=None, reasoning_content=None, tools=(), finish=None, usage=None):
    choice = SimpleNamespace(
        delta=SimpleNamespace(
            content=content,
            reasoning_content=reasoning_content,
            tool_calls=list(tools),
        ),
        finish_reason=finish,
    )
    return SimpleNamespace(choices=[choice], usage=usage)


class FakeCompletions:
    def __init__(self, stream, response) -> None:
        self.stream = stream
        self.response = response
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.stream if kwargs.get("stream") else self.response


class FakeClient:
    def __init__(self, completions) -> None:
        self.chat = SimpleNamespace(completions=completions)

    def with_options(self, **_kwargs):
        return self


class ChatCompletionsAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_replays_reasoning_content_after_tool_approval_restore(
        self,
    ) -> None:
        stream = FakeStream(
            chunk(reasoning_content="private reasoning"),
            chunk(
                tools=(
                    tool_delta(
                        0,
                        call_id="call-think",
                        name="search",
                        arguments='{"q":"weather"}',
                    ),
                )
            ),
            chunk(finish="tool_calls"),
        )
        first_model = OpenAIChatCompletionsModel(
            model="deepseek-thinking",
            api_key="secret",
            base_url="https://example.com/v1",
            client=FakeClient(FakeCompletions(stream, None)),
        )
        request = ModelRequest(
            model_name=first_model.model,
            messages=(ChatMessage("user", "check the weather"),),
            tool_specs=(
                ToolSpec(
                    "search",
                    "search",
                    [ToolArgument("q", "query", {"type": "string"})],
                    "text",
                ),
            ),
            model_settings=ModelSettings(),
            request_id="request-thinking",
            attempt_id="attempt-thinking",
        )
        accumulator = ModelEventAccumulator(
            "attempt-thinking",
            max_buffer_chars=10_000,
        )
        async for event in first_model.stream_response(request):
            accumulator.apply(event)
        response = accumulator.finish()

        restored_model = OpenAIChatCompletionsModel(
            model="deepseek-thinking",
            api_key="secret",
            base_url="https://example.com/v1",
            client=FakeClient(FakeCompletions(FakeStream(), None)),
        )
        restored_model.restore_state(first_model.export_state())
        restored_model.restore_pending_tool_calls(tuple(response.tool_calls))
        restored_model.record_tool_output(response.tool_calls[0], "sunny")

        messages = restored_model._kwargs(request, stream=False)["messages"]
        assistant = next(item for item in messages if item["role"] == "assistant")
        self.assertEqual(assistant["reasoning_content"], "private reasoning")
        self.assertEqual(assistant["tool_calls"][0]["id"], "call-think")
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertNotIn("private reasoning", response.output_text or "")

    async def test_restores_pending_tool_call_before_recording_its_output(
        self,
    ) -> None:
        completions = FakeCompletions(FakeStream(), None)
        model = OpenAIChatCompletionsModel(
            model="compatible-model",
            api_key="secret",
            base_url="https://example.com/v1",
            client=FakeClient(completions),
        )
        pending = ToolCall(
            "run_shell_command",
            {"command": "python -c \"print(6 * 7)\""},
            "call-shell",
        )

        model.restore_pending_tool_calls((pending,))
        model.restore_pending_tool_calls((pending,))
        model.record_tool_output(pending, "42")

        request = ModelRequest(
            model_name=model.model,
            messages=(ChatMessage("user", "calculate"),),
            tool_specs=(),
            model_settings=ModelSettings(),
            request_id="request-resume",
            attempt_id="attempt-resume",
        )
        messages = model._kwargs(request, stream=False)["messages"]
        assistant_messages = [
            message for message in messages if message["role"] == "assistant"
        ]
        self.assertEqual(len(assistant_messages), 1)
        self.assertEqual(
            assistant_messages[0]["tool_calls"][0]["id"],
            pending.call_id,
        )
        self.assertEqual(messages[-1]["role"], "tool")
        self.assertEqual(messages[-1]["tool_call_id"], pending.call_id)

    async def test_output_type_requests_json_and_includes_schema_in_prompt(
        self,
    ) -> None:
        schema = {
            "type": "object",
            "properties": {"kind": {"type": "string"}},
            "required": ["kind"],
            "additionalProperties": False,
        }
        message = SimpleNamespace(content='{"kind":"plan"}', tool_calls=[])
        completion = SimpleNamespace(
            id="chat-json",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(total_tokens=3),
        )
        completions = FakeCompletions(FakeStream(), completion)
        model = OpenAIChatCompletionsModel(
            model="compatible-model",
            api_key="secret",
            base_url="https://example.com/v1",
            client=FakeClient(completions),
        )
        Agent(memory=AgentMemory(), model=model, output_type=schema)
        request = ModelRequest(
            model_name=model.model,
            messages=(
                ChatMessage("system", "Plan the task."),
                ChatMessage("user", "go"),
            ),
            tool_specs=(),
            model_settings=ModelSettings(),
            request_id="request-json",
            attempt_id="attempt-json",
        )

        await model.get_response_for_request(request)

        sent = completions.calls[0]
        self.assertEqual(sent["response_format"], {"type": "json_object"})
        system_text = "\n".join(
            item["content"]
            for item in sent["messages"]
            if item["role"] == "system"
        )
        self.assertIn("JSON Schema", system_text)
        self.assertIn('"additionalProperties":false', system_text)

    async def test_streams_text_fragmented_tools_usage_and_closes(self) -> None:
        stream = FakeStream(
            chunk(content="先查"),
            chunk(
                tools=(
                    tool_delta(
                        0, call_id="call-a", name="search", arguments='{"q":'
                    ),
                    tool_delta(1, call_id="call-b", name="sum", arguments='{"x":'),
                )
            ),
            chunk(
                tools=(
                    tool_delta(0, arguments='"天气"}'),
                    tool_delta(1, arguments="1}"),
                )
            ),
            chunk(
                finish="tool_calls",
                usage=SimpleNamespace(
                    prompt_tokens=4,
                    completion_tokens=3,
                    total_tokens=7,
                ),
            ),
        )
        completions = FakeCompletions(stream, None)
        model = OpenAIChatCompletionsModel(
            model="compatible-model",
            api_key="secret",
            base_url="https://example.com/v1",
            client=FakeClient(completions),
        )
        request = ModelRequest(
            model_name=model.model,
            messages=(ChatMessage("user", "do it"),),
            tool_specs=(
                ToolSpec(
                    "search",
                    "search",
                    [ToolArgument("q", "query", {"type": "string"})],
                    "text",
                ),
                ToolSpec(
                    "sum",
                    "sum",
                    [ToolArgument("x", "number", {"type": "number"})],
                    "number",
                ),
            ),
            model_settings=ModelSettings(),
            request_id="request-1",
            attempt_id="attempt-1",
        )
        accumulator = ModelEventAccumulator("attempt-1", max_buffer_chars=10_000)
        async for event in model.stream_response(request):
            accumulator.apply(event)
        response = accumulator.finish()

        self.assertEqual(response.output_text, "先查")
        self.assertEqual(
            [call.tool_name for call in response.tool_calls],
            ["search", "sum"],
        )
        self.assertEqual(response.tool_calls[0].arguments, {"q": "天气"})
        self.assertEqual(response.usage["total_tokens"], 7)
        self.assertTrue(stream.closed)
        self.assertEqual(
            completions.calls[0]["stream_options"],
            {"include_usage": True},
        )

        model.record_tool_output(response.tool_calls[0], "sunny")
        self.assertEqual(model._tool_exchange[-1]["tool_call_id"], "call-a")

    async def test_non_stream_response_uses_structured_roles(self) -> None:
        message = SimpleNamespace(
            content="done",
            tool_calls=[],
        )
        completion = SimpleNamespace(
            id="chat-1",
            choices=[SimpleNamespace(message=message)],
            usage=SimpleNamespace(total_tokens=3),
            request_id="provider-request",
        )
        completions = FakeCompletions(FakeStream(), completion)
        model = OpenAIChatCompletionsModel(
            model="compatible-model",
            api_key="secret",
            base_url="https://example.com/v1",
            client=FakeClient(completions),
        )
        request = ModelRequest(
            model_name=model.model,
            messages=(ChatMessage("system", "help"), ChatMessage("user", "hello")),
            tool_specs=(),
            model_settings=ModelSettings(),
            request_id="request-2",
            attempt_id="attempt-2",
        )
        response = await model.get_response_for_request(request)
        self.assertEqual(response.output_text, "done")
        self.assertEqual(completions.calls[0]["messages"][0]["role"], "system")
        self.assertNotIn("response_format", completions.calls[0])
        self.assertEqual(response.usage, {"total_tokens": 3})


if __name__ == "__main__":
    unittest.main()
