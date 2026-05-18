from __future__ import annotations

from typing import TYPE_CHECKING, Any, Sequence

if TYPE_CHECKING:
    from .agent import Agent
    from .contracts import ModelResponse, ToolCall
    from .run_context import RunContextWrapper
    from .run_steps import TurnInput

# hook : 在 agent的运行流程中 可以在 你想要观察的地方 发出当前你想知道的信息的 一种监视器
class LifecycleHooks:
    def on_agent_start(self, context: RunContextWrapper, agent: Agent) -> None:
        pass

    def on_agent_end(
        self,
        context: RunContextWrapper,
        agent: Agent,
        output: Any,
    ) -> None:
        pass

    def on_llm_start(
        self,
        context: RunContextWrapper,
        agent: Agent,
        turn_input: TurnInput,
    ) -> None:
        pass

    def on_llm_end(
        self,
        context: RunContextWrapper,
        agent: Agent,
        model_response: ModelResponse | None,
    ) -> None:
        pass

    def on_tool_start(
        self,
        context: RunContextWrapper,
        agent: Agent,
        tool_call: ToolCall,
    ) -> None:
        pass

    def on_tool_end(
        self,
        context: RunContextWrapper,
        agent: Agent,
        tool_call: ToolCall,
        result: Any,
    ) -> None:
        pass

    def on_handoff(
        self,
        context: RunContextWrapper,
        from_agent: Agent,
        to_agent: Agent,
    ) -> None:
        pass

    def on_error(
        self,
        context: RunContextWrapper,
        agent: Agent,
        error: Exception,
    ) -> None:
        pass


LifecycleHookSequence = Sequence[LifecycleHooks]


def emit_agent_start(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
) -> None:
    for hook in hooks:
        hook.on_agent_start(context, agent)


def emit_agent_end(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
    output: Any,
) -> None:
    for hook in hooks:
        hook.on_agent_end(context, agent, output)


def emit_llm_start(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
    turn_input: TurnInput,
) -> None:
    for hook in hooks:
        hook.on_llm_start(context, agent, turn_input)


def emit_llm_end(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
    model_response: ModelResponse | None,
) -> None:
    for hook in hooks:
        hook.on_llm_end(context, agent, model_response)


def emit_tool_start(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
    tool_call: ToolCall,
) -> None:
    for hook in hooks:
        hook.on_tool_start(context, agent, tool_call)


def emit_tool_end(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
    tool_call: ToolCall,
    result: Any,
) -> None:
    for hook in hooks:
        hook.on_tool_end(context, agent, tool_call, result)


def emit_handoff(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    from_agent: Agent,
    to_agent: Agent,
) -> None:
    for hook in hooks:
        hook.on_handoff(context, from_agent, to_agent)


def emit_error(
    hooks: LifecycleHookSequence,
    context: RunContextWrapper,
    agent: Agent,
    error: Exception,
) -> None:
    for hook in hooks:
        hook.on_error(context, agent, error)
