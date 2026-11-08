from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .agent import Agent
    from .run_context import RunContextWrapper


# guardrail = 可以阻止 agent 继续运行的检查器


# guardrail 函数的返回值
@dataclass(frozen=True)
class GuardrailFunctionOutput:
    output_info: Any          # 检查的附加信息
    tripwire_triggered: bool  # 表示“警戒线是否被触发”


# 一次输入检查的结果记录
@dataclass(frozen=True)
class InputGuardrailResult:
    guardrail: InputGuardrail
    guardrail_name: str
    agent_input: str
    output: GuardrailFunctionOutput


@dataclass(frozen=True)
class OutputGuardrailResult:
    guardrail: OutputGuardrail
    guardrail_name: str
    agent_output: Any
    output: GuardrailFunctionOutput



# 把一个普通函数包装成“检查器”后的对象,run就是实行检查函数
@dataclass(frozen=True)
class InputGuardrail:
    guardrail_function: Callable[
        [RunContextWrapper, Agent, str],
        GuardrailFunctionOutput,
    ]
    name: str | None = None

    def get_name(self) -> str:
        if self.name:
            return self.name
        return getattr(self.guardrail_function, "__name__", self.__class__.__name__)

    def run(
        self,
        agent: Agent,
        agent_input: str,
        context: RunContextWrapper,
    ) -> InputGuardrailResult:
        output = self.guardrail_function(context, agent, agent_input)
        return InputGuardrailResult(
            guardrail=self,
            guardrail_name=self.get_name(),
            agent_input=agent_input,
            output=output,
        )
 
@dataclass(frozen=True)
class OutputGuardrail:
    guardrail_function: Callable[
        [RunContextWrapper, Agent, Any],
        GuardrailFunctionOutput,
    ]
    name: str | None = None

    def get_name(self) -> str:
        if self.name:
            return self.name
        return getattr(self.guardrail_function, "__name__", self.__class__.__name__)

    def run(
        self,
        context: RunContextWrapper,
        agent: Agent,
        agent_output: Any,
    ) -> OutputGuardrailResult:
        output = self.guardrail_function(context, agent, agent_output)
        return OutputGuardrailResult(
            guardrail=self,
            guardrail_name=self.get_name(),
            agent_output=agent_output,
            output=output,
        )



# 这个函数是一个装饰器，用来把普通函数变成 InputGuardrail 对象
'''
@input_guardrail(name="dangerous_input_check")  - 内部可不传入名字
def check_input(ctx, agent, text):
    return GuardrailFunctionOutput(
        output_info=None,
        tripwire_triggered=False,
    )
'''
def input_guardrail(
    func: Callable[[RunContextWrapper, Agent, str], GuardrailFunctionOutput] | None = None,
    *,
    name: str | None = None,
) -> InputGuardrail | Callable[
    [Callable[[RunContextWrapper, Agent, str], GuardrailFunctionOutput]],
    InputGuardrail,
]:
    def decorator(
        real_func: Callable[[RunContextWrapper, Agent, str], GuardrailFunctionOutput],
    ) -> InputGuardrail:
        return InputGuardrail(guardrail_function=real_func, name=name)

    if func is None:
        return decorator
    return decorator(func)


def output_guardrail(
    func: Callable[[RunContextWrapper, Agent, Any], GuardrailFunctionOutput] | None = None,
    *,
    name: str | None = None,
) -> OutputGuardrail | Callable[
    [Callable[[RunContextWrapper, Agent, Any], GuardrailFunctionOutput]],
    OutputGuardrail,
]:
    def decorator(
        real_func: Callable[[RunContextWrapper, Agent, Any], GuardrailFunctionOutput],
    ) -> OutputGuardrail:
        return OutputGuardrail(guardrail_function=real_func, name=name)

    if func is None:
        return decorator
    return decorator(func)
