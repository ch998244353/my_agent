from __future__ import annotations

import inspect
from dataclasses import dataclass
from types import UnionType
from typing import Any, Callable, Literal, Union, get_args, get_origin, get_type_hints

from .contracts import ToolArgument, ToolSpec
from .tool_schema import annotation_to_json_schema


FINAL_ANSWER_TOOL_NAME = "final_answer"


class ToolNotFoundError(LookupError):
    def __init__(self, tool_name: str):
        super().__init__(f"Tool '{tool_name}' is not registered.")
        self.tool_name = tool_name

# 
class ToolExecutionError(RuntimeError):
    def __init__(self, tool_name: str, message: str):
        super().__init__(f"Tool '{tool_name}' failed: {message}")
        self.tool_name = tool_name


@dataclass
#11111dfas
class FunctionTool:
    spec: ToolSpec
    handler: Callable[..., Any]

    @property
    def name(self) -> str:
        return self.spec.name

    def execute(self, arguments: dict[str, Any]) -> Any:
        
        # 工具允许接收的参数名集合
        allowed_arguments = {argument.name for argument in self.spec.arguments}
       
        # 模型漏传的必填参数
        required_arguments = [
            argument.name
            for argument in self.spec.arguments
            if argument.required and argument.name not in arguments
        ]
        
        # 模型多传的非法参数
        unexpected_arguments = sorted(set(arguments) - allowed_arguments)

        if required_arguments:
            missing_text = ", ".join(required_arguments)
            raise ToolExecutionError(
                self.name,
                f"Missing required arguments: {missing_text}",
            )
        if unexpected_arguments:
            unexpected_text = ", ".join(unexpected_arguments)
            raise ToolExecutionError(
                self.name,
                f"Unexpected arguments: {unexpected_text}",
            )

        try:
            return self.handler(**arguments)
        except ToolExecutionError:
            raise
        except Exception as exc:
            raise ToolExecutionError(self.name, str(exc)) from exc


# 把普通 Python 函数转换成 FunctionTool
def function_tool(
    func: Callable[..., Any] | None = None,
    *,
    name_override: str | None = None,
    description_override: str | None = None,
) -> FunctionTool | Callable[[Callable[..., Any]], FunctionTool]:
    def decorator(real_func: Callable[..., Any]) -> FunctionTool:
        return _create_function_tool(
            real_func,
            name_override=name_override,
            description_override=description_override,
        )

    if func is None:
        return decorator
    return decorator(func)


def _create_function_tool(
    func: Callable[..., Any],
    *,
    name_override: str | None = None,
    description_override: str | None = None,
) -> FunctionTool:
    signature = inspect.signature(func)  # 读取函数的参数签名
    '''
    def search(query: str, top_k: int = 5) -> list[str]:
    signature.parameters = {query: str , top_k: int = 5}
    signature.return_annotation = list[str]
    {  "query": Parameter("query: str"),  "top_k": Parameter("top_k: int = 5"),}
    '''
    type_hints = get_type_hints(func)    # 读取函数里的类型标注
    unsupported_kinds = {                # 定义不支持的参数类型
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
        inspect.Parameter.POSITIONAL_ONLY,
    }
    arguments: list[ToolArgument] = []
    argument_descriptions = _docstring_arg_descriptions(func)
    '''
        这个函数会从函数注释里解析 Args: 部分。 
        def add(a: int, b: int = 1) -> int:
            """Add two numbers.      函数description

            Args:                    各个参数
                a: First number.
                b: Second number.
            """

        会解析出：
        {
            "a": "First number.",
            "b": "Second number.",
        }
 
    '''

    # 提取所有参数
    for parameter_name, parameter in signature.parameters.items():
        if parameter.kind in unsupported_kinds:
            raise TypeError(
                "function_tool only supports keyword-compatible parameters."
            )

        annotation = type_hints.get(parameter_name, parameter.annotation) # 有就返回,否则就是parameter.annotation
        arguments.append(
            ToolArgument(
                name=parameter_name,
                description=argument_descriptions.get(parameter_name, ""),
                schema=annotation_to_json_schema(annotation),
                required=parameter.default is inspect._empty,
            )
        )

    return_annotation = type_hints.get("return", signature.return_annotation)
    return FunctionTool(
        spec=ToolSpec(
            name=name_override or func.__name__,
            description=description_override or _docstring_summary(func),
            arguments=arguments,
            returns=_annotation_to_tool_type(return_annotation),
        ),
        handler=func,
    )


def _annotation_to_tool_type(annotation: Any) -> str:
    if annotation is inspect._empty or annotation is Any:
        return "any"

    origin = get_origin(annotation)
    if origin in (list, tuple, set):
        return "array"
    if origin is dict:
        return "object"
    if origin in (Union, UnionType):
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(args) == 1:
            return _annotation_to_tool_type(args[0])
        return "any"
    if origin is Literal:
        values = get_args(annotation)
        value_types = {type(value) for value in values}
        if value_types <= {str}:
            return "string"
        if value_types <= {int}:
            return "integer"
        if value_types <= {float, int}:
            return "number"
        if value_types <= {bool}:
            return "boolean"
        return "any"

    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        list: "array",
        tuple: "array",
        set: "array",
        dict: "object",
    }
    return mapping.get(annotation, "any")


def _docstring_summary(func: Callable[..., Any]) -> str:
    docstring = inspect.getdoc(func) or ""
    if not docstring:
        return ""
    return docstring.splitlines()[0].strip()


def _docstring_arg_descriptions(func: Callable[..., Any]) -> dict[str, str]:
    docstring = inspect.getdoc(func) or ""  # 拿到函数中的文本
    descriptions: dict[str, str] = {}
    in_args_block = False

    for raw_line in docstring.splitlines():
        line = raw_line.strip()
        if line == "Args:":
            in_args_block = True
            continue
        if in_args_block and not line:
            continue
        if in_args_block and ":" not in line:
            break
        if in_args_block:
            name, description = line.split(":", 1)
            descriptions[name.strip()] = description.strip()

    return descriptions


def create_final_answer_tool() -> FunctionTool:
    def final_answer(answer: str) -> str:
        return answer

    return FunctionTool(
        spec=ToolSpec(
            name=FINAL_ANSWER_TOOL_NAME,
            description="Return the final answer and stop the agent loop.",
            arguments=[
                ToolArgument(
                    name="answer",
                    description="Final answer to return to the user.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        ),
        handler=final_answer,
    )


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, FunctionTool] = {}

    def register(self, tool: FunctionTool) -> None:
        self._tools[tool.name] = tool

    def get(self, tool_name: str) -> FunctionTool:
        if tool_name not in self._tools:
            raise ToolNotFoundError(tool_name)
        return self._tools[tool_name]

    def list_specs(self) -> list[ToolSpec]:
        return [tool.spec for tool in self._tools.values()]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        tool = self.get(tool_name)
        return tool.execute(arguments)
