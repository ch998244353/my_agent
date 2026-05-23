from __future__ import annotations

import re
from typing import TYPE_CHECKING, Protocol

from .contracts import ToolArgument, ToolCall, ToolSpec

if TYPE_CHECKING:
    from .agent import Agent


HANDOFF_TOOL_PREFIX = "transfer_to_"


class HandoffTarget(Protocol):
    name: str


# 把 Agent.name 转成安全的工具名片段，例如 Math Agent -> math_agent
def normalize_handoff_name(agent_name: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", agent_name.lower()).strip("_")
    return normalized or "agent"


# 生成模型可调用的 handoff 工具名
def handoff_tool_name(agent: HandoffTarget) -> str:
    return f"{HANDOFF_TOOL_PREFIX}{normalize_handoff_name(agent.name)}"

# 建立 tool_name -> target_agent 映射
def handoff_map(handoffs: list[Agent]) -> dict[str, Agent]:
    return {handoff_tool_name(agent): agent for agent in handoffs}


def handoff_tool_specs(handoffs: list[Agent]) -> list[ToolSpec]:
    return [
        ToolSpec(
            name=tool_name,
            description=f"Hand off control to {agent.name}.",
            arguments=[
                ToolArgument(
                    name="task",
                    description="Task that the target agent should handle.",
                    schema={"type": "string"},
                )
            ],
            returns="object",
        )
        for tool_name, agent in handoff_map(handoffs).items()
    ]


# 从根据名字得到具体子agent
def handoff_target_for(handoffs: list[Agent], action: ToolCall) -> Agent | None:
    return handoff_map(handoffs).get(action.tool_name)
