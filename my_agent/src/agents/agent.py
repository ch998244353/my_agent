from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from .contracts import (
    ChatMessage,
    ToolCall,
    ToolSpec,
)
from .handoffs import handoff_map, handoff_target_for, handoff_tool_specs
from .guardrails import InputGuardrail, OutputGuardrail
from .lifecycle import LifecycleHooks
from .memory import AgentMemory
from .model_settings import ModelSettings
from .output import output_schema_from_output_type
from .python_executor import (
    PYTHON_EXECUTOR_TOOL_NAME,
    MiniPythonExecutor,
    create_python_executor_tool,
)
from .run_config import RunConfig
from .run_context import RunContextWrapper
from .result import RunResult
from .tools import (
    FINAL_ANSWER_TOOL_NAME,
    FunctionTool,
    ToolEnabled,
    ToolNotFoundError,
    ToolRegistry,
    create_final_answer_tool,
)


STRUCTURED_OUTPUT_SCHEMA_NAME = "agent_output"


@dataclass(frozen=True)
class AgentCapabilities:
    final_answer_tool: bool = True
    python_execution: bool = False
    python_executor: MiniPythonExecutor | None = None
    python_tool_name: str = PYTHON_EXECUTOR_TOOL_NAME


@dataclass
class Agent:
    memory: AgentMemory
    model: Any
    name: str = "Agent"
    instructions: str | None = None  # Agent 应该扮演什么角色
    model_settings: ModelSettings = field(default_factory=ModelSettings)
    output_type: type[Any] | dict[str, Any] | None = None  # 我希望 Agent 最终输出是什么结构
    tool_use_behavior: str | dict[str, list[str]] = "run_llm_again"
    tool_registry: ToolRegistry = field(default_factory=ToolRegistry)
    handoffs: list[Agent] = field(default_factory=list)
    max_steps: int = 5
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    hooks: LifecycleHooks | None = None
    input_guardrails: list[InputGuardrail] = field(default_factory=list)
    output_guardrails: list[OutputGuardrail] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._prepare_tools()
        self._prepare_output_schema()

    def clone(self, **overrides: Any) -> Agent:
        clone_overrides: dict[str, Any] = {
            "handoffs": list(self.handoffs),
            "input_guardrails": list(self.input_guardrails),
            "output_guardrails": list(self.output_guardrails),
        }
        clone_overrides.update(overrides)
        return replace(self, **clone_overrides)

    def as_tool(
        self,
        *,
        tool_name: str | None = None,
        tool_description: str | None = None,
        output_extractor: Callable[[RunResult], object] | None = None,
        memory_factory: Callable[[], AgentMemory] = AgentMemory,
        is_enabled: ToolEnabled = True,
        run_config: RunConfig | None = None,
        max_steps: int | None = None,
        max_turns: int | None = None,
    ) -> FunctionTool:
        from .agent_tools import create_agent_tool

        return create_agent_tool(
            self,
            tool_name=tool_name,
            tool_description=tool_description,
            output_extractor=output_extractor,
            memory_factory=memory_factory,
            is_enabled=is_enabled,
            run_config=run_config,
            max_steps=max_steps,
            max_turns=max_turns,
        )

    def _output_schema(self) -> dict[str, Any] | None:
        return output_schema_from_output_type(self.output_type)

    def _prepare_output_schema(self) -> None:
        output_schema = self._output_schema()
        if output_schema is None:
            return
        if hasattr(self.model, "response_schema"):
            self.model.response_schema = output_schema
        if hasattr(self.model, "response_schema_name"):
            self.model.response_schema_name = STRUCTURED_OUTPUT_SCHEMA_NAME
        if hasattr(self.model, "response_schema_strict"):
            self.model.response_schema_strict = True

    @classmethod
    def for_code(
        cls,
        memory: AgentMemory,
        model: Any,
        executor: MiniPythonExecutor | None = None,
        max_steps: int = 5,
        tool_registry: ToolRegistry | None = None,
    ) -> Agent:
        return cls(
            memory=memory,
            model=model,
            tool_registry=tool_registry or ToolRegistry(),
            max_steps=max_steps,
            capabilities=AgentCapabilities(
                final_answer_tool=False,
                python_execution=True,
                python_executor=executor,
            ),
        )

    def _prepare_tools(self) -> None:
        if self.capabilities.final_answer_tool:
            self._register_default_final_answer_tool()

        if self.capabilities.python_execution:
            self._register_python_executor_tool()

    def _register_default_final_answer_tool(self) -> None:
        try:
            self.tool_registry.get(FINAL_ANSWER_TOOL_NAME)
        except ToolNotFoundError:
            self.tool_registry.register(create_final_answer_tool())

    def _register_python_executor_tool(self) -> None:
        try:
            self.tool_registry.get(self.capabilities.python_tool_name)
        except ToolNotFoundError:
            executor = self.capabilities.python_executor or MiniPythonExecutor()
            self.tool_registry.register(
                create_python_executor_tool(
                    executor,
                    tool_name=self.capabilities.python_tool_name,
                )
            )

    def _messages_for_model(self) -> list[ChatMessage]:
        messages = self.memory.to_messages()
        if not self.instructions:
            return messages
        return [ChatMessage(role="system", content=self.instructions), *messages]


    # 给出agent工具化字典
    def _handoff_map(self) -> dict[str, Agent]:
        return handoff_map(self.handoffs)


    # 把 handoff 目标 Agent 转为模型可见的工具说明
    def _handoff_tool_specs(self) -> list[ToolSpec]:
        return handoff_tool_specs(self.handoffs)


    # 普通工具和 handoff 工具合并后一起给模型看
    def _tool_specs_for_model(
        self,
        context_wrapper: RunContextWrapper | None = None,
    ) -> list[ToolSpec]:
        return [
            *self.tool_registry.list_specs(context_wrapper, self),
            *self._handoff_tool_specs(),
        ]

    
    def _handoff_target_for(self, action: ToolCall) -> Agent | None:
        return handoff_target_for(self.handoffs, action)

    def run(
        self,
        task: str,
        config: RunConfig | None = None,
    ) -> RunResult:
        from .runner import Runner
        return Runner.run_sync(self, task, config=config)

    def _run(
        self,
        task: str,
        config: RunConfig | None = None,
    ) -> RunResult:
        from .run_loop import run_agent_loop

        return run_agent_loop(self, task, config=config)
