from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mini_smolagent import (  # noqa: E402
    Agent,
    AgentMemory,
    ChatMessage,
    ModelResponse,
    ModelSettings,
    ToolCall,
)


class RecordingModel:
    def __init__(self, actions=None) -> None:
        self.actions = list(actions or [])
        self.last_messages: list[ChatMessage] = []
        self.last_tool_specs = []
        self._index = 0

    def decide(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        if self._index >= len(self.actions):
            return None
        action = self.actions[self._index]
        self._index += 1
        return action


class SchemaAwareModel(RecordingModel):
    def __init__(self) -> None:
        super().__init__()
        self.response_schema = None
        self.response_schema_name = "final_response"
        self.response_schema_strict = True

    def get_response(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        return ModelResponse(
            response_id="resp_1",
            output=[],
            output_text='{"answer": "done"}',
            tool_calls=[],
        )


class AgentConfigurationTestCase(unittest.TestCase):
    def test_agent_has_sdk_style_default_configuration(self) -> None:
        agent = Agent(memory=AgentMemory(), model=RecordingModel())

        self.assertEqual(agent.name, "Agent")
        self.assertIsNone(agent.instructions)
        self.assertEqual(agent.model_settings, ModelSettings())
        self.assertIsNone(agent.output_type)
        self.assertEqual(agent.tool_use_behavior, "run_llm_again")

    def test_agent_accepts_identity_prompt_and_output_configuration(self) -> None:
        settings = ModelSettings(temperature=0.2, top_p=0.9, tool_choice="auto")

        agent = Agent(
            name="Weather bot",
            instructions="You answer with compact weather facts.",
            memory=AgentMemory(),
            model=RecordingModel(),
            model_settings=settings,
            output_type=dict,
            tool_use_behavior="stop_on_first_tool",
        )

        self.assertEqual(agent.name, "Weather bot")
        self.assertEqual(agent.instructions, "You answer with compact weather facts.")
        self.assertIs(agent.model_settings, settings)
        self.assertIs(agent.output_type, dict)
        self.assertEqual(agent.tool_use_behavior, "stop_on_first_tool")

    def test_agent_configures_model_with_output_schema(self) -> None:
        output_schema = {
            "type": "object",
            "properties": {
                "answer": {"type": "string"},
            },
            "required": ["answer"],
            "additionalProperties": False,
        }
        model = SchemaAwareModel()

        Agent(
            memory=AgentMemory(),
            model=model,
            output_type=output_schema,
        )

        self.assertIs(model.response_schema, output_schema)
        self.assertEqual(model.response_schema_name, "agent_output")
        self.assertTrue(model.response_schema_strict)

    def test_instructions_are_sent_as_first_model_message(self) -> None:
        model = RecordingModel(
            [
                ToolCall(
                    tool_name="final_answer",
                    arguments={"answer": "done"},
                    call_id="call_1",
                )
            ]
        )
        agent = Agent(
            name="Concise assistant",
            instructions="Always answer in one sentence.",
            memory=AgentMemory(),
            model=model,
        )

        agent.run("Say done.")

        self.assertEqual(model.last_messages[0].role, "system")
        self.assertEqual(model.last_messages[0].content, "Always answer in one sentence.")
        self.assertEqual(model.last_messages[1].role, "user")
        self.assertEqual(model.last_messages[1].content, "Say done.")

    def test_empty_instructions_do_not_add_system_message(self) -> None:
        model = RecordingModel()
        agent = Agent(memory=AgentMemory(), model=model, instructions="")

        agent.run("Stop immediately.")

        self.assertEqual(model.last_messages[0].role, "user")
        self.assertEqual(model.last_messages[0].content, "Stop immediately.")


if __name__ == "__main__":
    unittest.main()
