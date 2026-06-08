from agents import (
    Agent,
    AgentMemory,
    FunctionTool,
    RunConfig,
    ToolCall,
    ToolRegistry,
    ToolSpec,
    VerificationPolicy,
)


class ToolThenStopModel:
    def __init__(self):
        self.turn_messages = []

    def decide(self, messages, tool_specs):
        self.turn_messages.append(messages)
        if len(self.turn_messages) == 1:
            return ToolCall(
                call_id="call_apply_patch",
                tool_name="apply_patch",
                arguments={},
            )
        return None


class TwoToolsThenStopModel:
    def __init__(self):
        self.turn_messages = []

    def decide(self, messages, tool_specs):
        self.turn_messages.append(messages)
        if len(self.turn_messages) <= 2:
            return ToolCall(
                call_id=f"call_apply_patch_{len(self.turn_messages)}",
                tool_name="apply_patch",
                arguments={},
            )
        return None


def test_successful_tool_adds_verification_observation_to_next_turn():
    model = ToolThenStopModel()
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            spec=ToolSpec(
                name="apply_patch",
                description="Apply a patch.",
                arguments=[],
                returns="string",
            ),
            handler=lambda: "patch applied",
            needs_approval=False,
        )
    )
    agent = Agent(
        memory=AgentMemory(),
        model=model,
        tool_registry=registry,
        max_steps=5,
    )
    config = RunConfig(
        verification=VerificationPolicy(
            commands=[
                'python -c "import sys; print(\'verification failed\'); sys.exit(1)"'
            ],
            auto_after_tools=["apply_patch"],
        )
    )

    result = agent.run("change code", config=config)

    assert len(model.turn_messages) == 2
    second_turn_text = "\n".join(message.content for message in model.turn_messages[1])
    assert "Verification observation" in second_turn_text
    assert "status: failed" in second_turn_text
    assert "verification failed" in second_turn_text
    assert any(item.item_type == "verification_result" for item in result.new_items)
    summary = result.verification_summary
    assert summary is not None
    assert summary.attempts == 1
    assert summary.passed is False
    assert summary.skipped == 0
    assert "status: failed" in (summary.last_observation or "")


def test_verification_stops_after_max_attempts_and_tells_model():
    model = TwoToolsThenStopModel()
    registry = ToolRegistry()
    registry.register(
        FunctionTool(
            spec=ToolSpec(
                name="apply_patch",
                description="Apply a patch.",
                arguments=[],
                returns="string",
            ),
            handler=lambda: "patch applied",
            needs_approval=False,
        )
    )
    agent = Agent(
        memory=AgentMemory(),
        model=model,
        tool_registry=registry,
        max_steps=5,
    )
    config = RunConfig(
        verification=VerificationPolicy(
            commands=[
                'python -c "import sys; print(\'verification failed\'); sys.exit(1)"'
            ],
            auto_after_tools=["apply_patch"],
            max_attempts=1,
        )
    )

    result = agent.run("change code", config=config)

    item_types = [item.item_type for item in result.new_items]
    assert item_types.count("verification_result") == 1
    assert item_types.count("verification_skipped") == 1
    assert len(model.turn_messages) == 3
    third_turn_text = "\n".join(message.content for message in model.turn_messages[2])
    assert "Verification skipped" in third_turn_text
    assert "max_attempts_reached" in third_turn_text
    summary = result.verification_summary
    assert summary is not None
    assert summary.attempts == 1
    assert summary.passed is False
    assert summary.skipped == 1
    assert "Verification skipped" in (summary.last_observation or "")
