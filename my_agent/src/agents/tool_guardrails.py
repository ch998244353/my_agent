from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from .contracts import ToolCall

if TYPE_CHECKING:
    from .agent import Agent
    from .run_context import RunContextWrapper

ToolGuardrailBehavior = Literal["allow", "reject_content", "raise_exception"]
ToolGuardrailEnabled = bool | Any


# 检查gd是否可以启用, 可以传入is_enable也可以是其他信息
def _is_enabled_for(is_enabled: ToolGuardrailEnabled, *args: Any) -> bool:
    if isinstance(is_enabled, bool):
        return is_enabled
    parameters = inspect.signature(is_enabled).parameters
    if any(parameter.kind is inspect.Parameter.VAR_POSITIONAL for parameter in parameters.values()):
        return bool(is_enabled(*args))
    positional_count = sum(
        1
        for parameter in parameters.values()
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    return bool(is_enabled(*args[:positional_count]))


@dataclass(frozen=True)
class ToolGuardrailFunctionOutput:
    output_info: Any = None  # 检查结果的附加信息
    behavior: ToolGuardrailBehavior = "allow"
    message: str | None = None  # reject_content 时，给模型看的拒绝说明 

    @classmethod
    def allow(cls, output_info: Any = None) -> ToolGuardrailFunctionOutput:
        return cls(output_info=output_info, behavior="allow")

    @classmethod
    def reject_content(cls, message: str, output_info: Any = None) -> ToolGuardrailFunctionOutput:
        return cls(output_info=output_info, behavior="reject_content", message=message)

    @classmethod
    def raise_exception(cls, output_info: Any = None) -> ToolGuardrailFunctionOutput:
        return cls(output_info=output_info, behavior="raise_exception")


# 工具执行前后检查结果 
@dataclass(frozen=True)
class ToolInputGuardrailResult:
    guardrail: ToolInputGuardrail; guardrail_name: str
    tool_call: ToolCall; output: ToolGuardrailFunctionOutput

@dataclass(frozen=True)
class ToolOutputGuardrailResult:
    guardrail: ToolOutputGuardrail; guardrail_name: str
    tool_call: ToolCall; tool_output: Any; output: ToolGuardrailFunctionOutput


class ToolGuardrailTripwireTriggered(RuntimeError):
    def __init__(self, guardrail_result: ToolInputGuardrailResult | ToolOutputGuardrailResult):
        self.guardrail_result = guardrail_result
        self.guardrail = guardrail_result.guardrail
        self.output = guardrail_result.output
        super().__init__(
            f"Tool guardrail '{guardrail_result.guardrail_name}' triggered tripwire."
        )


class ToolInputGuardrailTripwireTriggered(ToolGuardrailTripwireTriggered):
    pass


class ToolOutputGuardrailTripwireTriggered(ToolGuardrailTripwireTriggered):
    pass


# 输入/输出 Guardrail 包装器, 包装一个普通函数。这个函数未来会在工具执行前后运行
@dataclass(frozen=True)
class ToolInputGuardrail:
    guardrail_function: Any
    name: str | None = None
    is_enabled: ToolGuardrailEnabled = True

    def get_name(self) -> str:
        return self.name or getattr(self.guardrail_function, "__name__", self.__class__.__name__)

    def is_enabled_for(self, context: RunContextWrapper, agent: Agent, tool_call: ToolCall) -> bool:
        return _is_enabled_for(self.is_enabled, context, agent, tool_call)

    def run(self, context: RunContextWrapper, agent: Agent, tool_call: ToolCall) -> ToolInputGuardrailResult:
        output = self.guardrail_function(context, agent, tool_call)
        return ToolInputGuardrailResult(self, self.get_name(), tool_call, output)

@dataclass(frozen=True)
class ToolOutputGuardrail:
    guardrail_function: Any
    name: str | None = None
    is_enabled: ToolGuardrailEnabled = True

    def get_name(self) -> str:
        return self.name or getattr(self.guardrail_function, "__name__", self.__class__.__name__)

    def is_enabled_for(self, context: RunContextWrapper, agent: Agent, tool_call: ToolCall, tool_output: Any) -> bool:
        return _is_enabled_for(self.is_enabled, context, agent, tool_call, tool_output)

    def run(self, context: RunContextWrapper, agent: Agent, tool_call: ToolCall, tool_output: Any) -> ToolOutputGuardrailResult:
        output = self.guardrail_function(context, agent, tool_call, tool_output)
        return ToolOutputGuardrailResult(self, self.get_name(), tool_call, tool_output, output)



# 两个装饰器
def tool_input_guardrail(
    func: Any = None,
    *,
    name: str | None = None,
    is_enabled: ToolGuardrailEnabled = True,
) -> Any:
    def decorator(real_func: Any) -> ToolInputGuardrail:
        return ToolInputGuardrail(real_func, name=name, is_enabled=is_enabled)
    return decorator if func is None else decorator(func)

def tool_output_guardrail(
    func: Any = None,
    *,
    name: str | None = None,
    is_enabled: ToolGuardrailEnabled = True,
) -> Any:
    def decorator(real_func: Any) -> ToolOutputGuardrail:
        return ToolOutputGuardrail(real_func, name=name, is_enabled=is_enabled)
    return decorator if func is None else decorator(func)
