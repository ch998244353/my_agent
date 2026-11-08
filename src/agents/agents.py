from __future__ import annotations

from typing import Any

from .agent import Agent
from .memory import AgentMemory
from .python_executor import MiniPythonExecutor
from .tools import ToolRegistry


MultiStepAgent = Agent
MiniToolCallingAgent = Agent


def MiniCodeAgent(
    *,
    memory: AgentMemory,
    model: Any,
    executor: MiniPythonExecutor | None = None,
    max_steps: int = 5,
    tool_registry: ToolRegistry | None = None,
) -> Agent:
    return Agent.for_code(
        memory=memory,
        model=model,
        executor=executor,
        max_steps=max_steps,
        tool_registry=tool_registry,
    )
