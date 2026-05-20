from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    Agent,
    AgentMemory,
    AgentToolError,
    FunctionTool,
    ToolCall,
    ToolArgument,
    ToolExecutionError,
    ToolNotFoundError,
    ToolRegistry,
    ToolSpec,
    function_tool,
    create_agent_tool,
)


class ToolRegistryTestCase(unittest.TestCase):
    def test_register_and_get_tool(self) -> None:
        registry = ToolRegistry()
        tool = FunctionTool(
            spec=ToolSpec(
                name="calculator",
                description="Add two numbers.",
                arguments=[
                    ToolArgument(
                        name="a",
                        description="First number.",
                        schema={"type": "number"},
                    ),
                    ToolArgument(
                        name="b",
                        description="Second number.",
                        schema={"type": "number"},
                    ),
                ],
                returns="number",
            ),
            handler=lambda a, b: a + b,
        )

        registry.register(tool)

        fetched_tool = registry.get("calculator")
        self.assertEqual(fetched_tool.name, "calculator")
        self.assertEqual(fetched_tool.spec.returns, "number")

    def test_execute_registered_tool(self) -> None:
        registry = ToolRegistry()

        def get_weather(city: str) -> str:
            return f"Weather in {city}: 18C and cloudy."

        registry.register(
            FunctionTool(
                spec=ToolSpec(
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
                ),
                handler=get_weather,
            )
        )

        result = registry.execute("get_weather", {"city": "Shanghai"})
        self.assertEqual(result, "Weather in Shanghai: 18C and cloudy.")

    def test_list_specs_returns_registered_tool_specs(self) -> None:
        registry = ToolRegistry()
        registry.register(
            FunctionTool(
                spec=ToolSpec(
                    name="search_docs",
                    description="Search local documents.",
                    arguments=[
                        ToolArgument(
                            name="query",
                            description="Search query.",
                            schema={"type": "string"},
                        )
                    ],
                    returns="array",
                ),
                handler=lambda query: [query],
            )
        )

        specs = registry.list_specs()

        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].name, "search_docs")

    def test_get_unknown_tool_raises_error(self) -> None:
        registry = ToolRegistry()

        with self.assertRaises(ToolNotFoundError):
            registry.get("unknown_tool")

    def test_function_tool_decorator_builds_tool_from_signature(self) -> None:
        @function_tool
        def get_weather(city: str, days: int = 1) -> str:
            """Get weather for a city."""
            return f"{city}:{days}"

        self.assertIsInstance(get_weather, FunctionTool)
        self.assertEqual(get_weather.name, "get_weather")
        self.assertEqual(get_weather.spec.description, "Get weather for a city.")
        self.assertEqual(get_weather.spec.returns, "string")
        self.assertEqual(
            [
                (argument.name, argument.schema, argument.required)
                for argument in get_weather.spec.arguments
            ],
            [
                ("city", {"type": "string"}, True),
                ("days", {"type": "integer"}, False),
            ],
        )
        self.assertEqual(get_weather.execute({"city": "Shanghai"}), "Shanghai:1")

    def test_function_tool_accepts_name_and_description_overrides(self) -> None:
        @function_tool(
            name_override="lookup_weather",
            description_override="Look up a forecast.",
        )
        def get_weather(city: str) -> str:
            """Ignored description."""
            return city

        self.assertEqual(get_weather.name, "lookup_weather")
        self.assertEqual(get_weather.spec.description, "Look up a forecast.")

    def test_function_tool_rejects_missing_and_unexpected_arguments(self) -> None:
        @function_tool
        def echo_text(text: str) -> str:
            """Echo text."""
            return text

        with self.assertRaises(ToolExecutionError) as missing_error:
            echo_text.execute({})
        self.assertIn("Missing required arguments: text", str(missing_error.exception))

        with self.assertRaises(ToolExecutionError) as unexpected_error:
            echo_text.execute({"text": "hi", "extra": "unused"})
        self.assertIn("Unexpected arguments: extra", str(unexpected_error.exception))

    def test_function_tool_wraps_handler_errors(self) -> None:
        @function_tool
        def explode() -> str:
            """Always fail."""
            raise ValueError("service unavailable")

        with self.assertRaises(ToolExecutionError) as error:
            explode.execute({})
        self.assertIn("service unavailable", str(error.exception))

    def test_function_tool_maps_rich_annotations_to_tool_schemas(self) -> None:
        @function_tool
        def search_docs(
            query: str,
            mode: Literal["fast", "deep"] = "fast",
            tags: list[str] | None = None,
        ) -> list[str]:
            """Search documents."""
            return [query, mode, *(tags or [])]

        arguments = {argument.name: argument for argument in search_docs.spec.arguments}

        self.assertEqual(
            arguments["mode"].schema,
            {"type": "string", "enum": ["fast", "deep"]},
        )
        self.assertEqual(arguments["tags"].schema, {"type": ["array", "null"]})
        self.assertEqual(search_docs.spec.returns, "array")

    def test_function_tool_arguments_use_schema(self) -> None:
        @function_tool
        def get_scores(names: list[str]) -> list[int]:
            """Get scores by name."""
            return [len(name) for name in names]

        argument = get_scores.spec.arguments[0]

        self.assertEqual(argument.schema, {"type": "array", "items": {"type": "string"}})

    def test_create_agent_tool_runs_child_agent_with_isolated_memory(self) -> None:
        class FinalAnswerModel:
            def decide(self, messages, tool_specs):
                return ToolCall("final_answer", {"answer": "child done"}, "call_1")

        child_agent = Agent(memory=AgentMemory(), model=FinalAnswerModel(), name="Child")

        tool = create_agent_tool(child_agent)
        result = tool.execute({"input": "Solve this subtask."})

        self.assertEqual(tool.name, "child")
        self.assertEqual(result, "child done")
        self.assertIsNone(child_agent.memory.task)

    def test_create_agent_tool_raises_when_child_agent_has_no_final_answer(self) -> None:
        class NoFinalAnswerModel:
            def decide(self, messages, tool_specs):
                return None

        child_agent = Agent(memory=AgentMemory(), model=NoFinalAnswerModel(), name="Child")
        tool = create_agent_tool(child_agent)

        with self.assertRaises(AgentToolError) as error:
            tool.execute({"input": "Solve this subtask."})

        self.assertIn("Child", str(error.exception))
        self.assertIn("did not reach a final answer", str(error.exception))

    def test_create_agent_tool_applies_child_run_limits(self) -> None:
        class CountingModel:
            def __init__(self) -> None:
                self.calls = 0

            def decide(self, messages, tool_specs):
                self.calls += 1
                return ToolCall("final_answer", {"answer": "child done"}, "call_1")

        model = CountingModel()
        child_agent = Agent(memory=AgentMemory(), model=model, name="Child")
        tool = create_agent_tool(child_agent, max_turns=0)

        with self.assertRaises(AgentToolError):
            tool.execute({"input": "Solve this subtask."})

        self.assertEqual(model.calls, 0)

    def test_create_agent_tool_can_be_disabled(self) -> None:
        class FinalAnswerModel:
            def decide(self, messages, tool_specs):
                return ToolCall("final_answer", {"answer": "child done"}, "call_1")

        child_agent = Agent(memory=AgentMemory(), model=FinalAnswerModel(), name="Child")
        parent_agent = Agent(memory=AgentMemory(), model=FinalAnswerModel())
        tool = create_agent_tool(child_agent, is_enabled=False)

        parent_agent.tool_registry.register(tool)

        self.assertNotIn(
            "child",
            [tool_spec.name for tool_spec in parent_agent._tool_specs_for_model()],
        )


if __name__ == "__main__":
    unittest.main()
