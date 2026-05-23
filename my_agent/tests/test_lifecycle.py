from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    FunctionTool,
    LifecycleHooks,
    ModelResponse,
    RunConfig,
    Runner,
    ToolArgument,
    ToolCall,
    ToolRegistry,
    ToolSpec,
)


class ScriptedResponseModel:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self._index = 0

    def get_response(self, messages, tool_specs):
        response = self.responses[self._index]
        self._index += 1
        return response


class RecordingHooks(LifecycleHooks):
    def __init__(self) -> None:
        self.events: list[str] = []
        self.context_ids: list[int] = []

    def _record(self, event: str, context) -> None:
        self.events.append(event)
        self.context_ids.append(id(context))

    def on_agent_start(self, context, agent) -> None:
        self._record(f"agent_start:{agent.name}", context)

    def on_agent_end(self, context, agent, output) -> None:
        self._record(f"agent_end:{agent.name}:{output}", context)

    def on_llm_start(self, context, agent, turn_input) -> None:
        self._record(f"llm_start:{agent.name}", context)

    def on_llm_end(self, context, agent, model_response) -> None:
        self._record(f"llm_end:{agent.name}", context)

    def on_tool_start(self, context, agent, tool_call) -> None:
        self._record(f"tool_start:{tool_call.tool_name}", context)

    def on_tool_end(self, context, agent, tool_call, result) -> None:
        self._record(f"tool_end:{tool_call.tool_name}:{result}", context)

    def on_handoff(self, context, from_agent, to_agent) -> None:
        self._record(f"handoff:{from_agent.name}->{to_agent.name}", context)

    def on_error(self, context, agent, error) -> None:
        self._record(f"error:{agent.name}:{error}", context)


def echo_tool() -> FunctionTool:
    return FunctionTool(
        spec=ToolSpec(
            name="echo_text",
            description="Return the same text.",
            arguments=[
                ToolArgument(
                    name="text",
                    description="Input text.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        ),
        handler=lambda text: text,
    )


def failing_tool() -> FunctionTool:
    return FunctionTool(
        spec=ToolSpec(
            name="fail_tool",
            description="Always fail.",
            arguments=[],
            returns="string",
        ),
        handler=lambda: (_ for _ in ()).throw(RuntimeError("tool failed")),
    )


class LifecycleHooksTestCase(unittest.TestCase):
    def test_run_config_hooks_record_agent_llm_and_tool_order(self) -> None:
        hooks = RecordingHooks()
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel(
                [
                    ModelResponse(
                        response_id="resp_1",
                        output=[],
                        output_text=None,
                        tool_calls=[
                            ToolCall("echo_text", {"text": "hello"}, "call_1"),
                        ],
                    ),
                    ModelResponse(
                        response_id="resp_2",
                        output=[],
                        output_text=None,
                        tool_calls=[
                            ToolCall("final_answer", {"answer": "done"}, "call_2"),
                        ],
                    ),
                ]
            ),
            name="main",
            tool_registry=registry,
        )

        result = Runner.run_sync(
            agent,
            "Echo then finish.",
            config=RunConfig(hooks=hooks),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(
            hooks.events,
            [
                "agent_start:main",
                "llm_start:main",
                "llm_end:main",
                "tool_start:echo_text",
                "tool_end:echo_text:hello",
                "llm_start:main",
                "llm_end:main",
                "tool_start:final_answer",
                "tool_end:final_answer:done",
                "agent_end:main:done",
            ],
        )
        self.assertEqual(set(hooks.context_ids), {id(result.context_wrapper)})

    def test_agent_hooks_record_handoff(self) -> None:
        hooks = RecordingHooks()
        specialist = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel(
                [
                    ModelResponse(
                        response_id="resp_target",
                        output=[],
                        output_text=None,
                        tool_calls=[
                            ToolCall(
                                "final_answer",
                                {"answer": "specialist done"},
                                "call_target",
                            ),
                        ],
                    )
                ]
            ),
            name="specialist",
        )
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel(
                [
                    ModelResponse(
                        response_id="resp_source",
                        output=[],
                        output_text=None,
                        tool_calls=[
                            ToolCall(
                                "transfer_to_specialist",
                                {"task": "finish this"},
                                "call_handoff",
                            )
                        ],
                    )
                ]
            ),
            name="main",
            handoffs=[specialist],
            hooks=hooks,
        )

        result = Runner.run_sync(agent, "Delegate.")

        self.assertTrue(result.reached_final_answer)
        self.assertEqual(result.final_answer, "specialist done")
        self.assertEqual(
            hooks.events,
            [
                "agent_start:main",
                "llm_start:main",
                "llm_end:main",
                "handoff:main->specialist",
                "agent_end:main:specialist done",
            ],
        )

    def test_hooks_record_tool_error(self) -> None:
        hooks = RecordingHooks()
        registry = ToolRegistry()
        registry.register(failing_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ScriptedResponseModel(
                [
                    ModelResponse(
                        response_id="resp_1",
                        output=[],
                        output_text=None,
                        tool_calls=[ToolCall("fail_tool", {}, "call_1")],
                    ),
                    ModelResponse(
                        response_id="resp_2",
                        output=[],
                        output_text=None,
                        tool_calls=[
                            ToolCall("final_answer", {"answer": "recovered"}, "call_2"),
                        ],
                    ),
                ]
            ),
            name="main",
            tool_registry=registry,
        )

        result = Runner.run_sync(
            agent,
            "Fail once then recover.",
            config=RunConfig(hooks=hooks),
        )

        self.assertTrue(result.reached_final_answer)
        self.assertIn("error:main:Tool 'fail_tool' failed: tool failed", hooks.events)
        self.assertEqual(
            hooks.events,
            [
                "agent_start:main",
                "llm_start:main",
                "llm_end:main",
                "tool_start:fail_tool",
                "error:main:Tool 'fail_tool' failed: tool failed",
                "llm_start:main",
                "llm_end:main",
                "tool_start:final_answer",
                "tool_end:final_answer:recovered",
                "agent_end:main:recovered",
            ],
        )


if __name__ == "__main__":
    unittest.main()
