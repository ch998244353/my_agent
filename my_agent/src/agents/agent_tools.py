from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from .contracts import ToolArgument, ToolSpec
from .memory import AgentMemory
from .result import RunResult
from .run_config import RunConfig
from .tools import FunctionTool, ToolEnabled, ToolExecutionError

if TYPE_CHECKING:
    from .agent import Agent


AgentToolOutputExtractor = Callable[[RunResult], object] # Agent 运行结果中取工具输出
AgentMemoryFactory = Callable[[], AgentMemory] # 如何为工具调用创建新的 memory 


class AgentToolError(ToolExecutionError):
    def __init__(self, agent_name: str, message: str):
        self.agent_name = agent_name
        super().__init__(agent_name, message)


# 默认工具名生成函数
def _default_agent_tool_name(agent: Agent) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "_", agent.name.lower()).strip("_")
    return normalized or "agent"


# 默认工具描述
def _default_agent_tool_description(agent: Agent) -> str:
    return f"Run the {agent.name} agent for a focused subtask."


# 默认输出提取函数
def _default_output_extractor(agent: Agent, result: RunResult) -> object:
    if not result.reached_final_answer:
        raise AgentToolError(agent.name, "child agent did not reach a final answer.")
    return result.final_answer


# 运行配置合并函数
def _resolve_run_config(
    run_config: RunConfig | None,
    max_steps: int | None,
    max_turns: int | None,
) -> RunConfig | None:
    if max_steps is None and max_turns is None:
        return run_config
    if run_config is None:
        return RunConfig(max_steps=max_steps, max_turns=max_turns)

    changes: dict[str, int] = {}
    if max_steps is not None:
        changes["max_steps"] = max_steps
    if max_turns is not None:
        changes["max_turns"] = max_turns
    return replace(run_config, **changes)


def create_agent_tool(
    agent: Agent,
    *,
    tool_name: str | None = None,
    tool_description: str | None = None,
    output_extractor: AgentToolOutputExtractor | None = None,
    memory_factory: AgentMemoryFactory = AgentMemory,
    is_enabled: ToolEnabled = True,
    run_config: RunConfig | None = None,
    max_steps: int | None = None,
    max_turns: int | None = None,
) -> FunctionTool:
    resolved_tool_name = tool_name or _default_agent_tool_name(agent)
    resolved_description = tool_description or _default_agent_tool_description(agent)
    resolved_output_extractor = output_extractor
    resolved_run_config = _resolve_run_config(run_config, max_steps, max_turns)

    def run_agent_tool(input: str) -> object:
        tool_agent = agent.clone(memory=memory_factory())
        result = tool_agent.run(input, config=resolved_run_config)
        if resolved_output_extractor is not None:
            return resolved_output_extractor(result)
        return _default_output_extractor(agent, result)

    return FunctionTool(
        spec=ToolSpec(
            name=resolved_tool_name,
            description=resolved_description,
            arguments=[
                ToolArgument(
                    name="input",
                    description="The focused task to send to this agent.",
                    schema={"type": "string"},
                )
            ],
            returns="any",
        ),
        handler=run_agent_tool,
        is_enabled=is_enabled,
    )
