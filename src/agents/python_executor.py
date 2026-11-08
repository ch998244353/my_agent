from __future__ import annotations

import ast
import io
from contextlib import redirect_stdout
from typing import Any

from .contracts import CodeExecutionResult, ToolArgument, ToolSpec
from .tools import FunctionTool


PYTHON_EXECUTOR_TOOL_NAME = "python_executor"


class CodeExecutionError(RuntimeError):
    pass


class _FinalAnswerSignal(BaseException):
    def __init__(self, value: Any) -> None:
        self.value = value


class MiniPythonExecutor:   # 直接运行代码
    def __init__(self) -> None:
        self.state: dict[str, Any] = {"__builtins__": __builtins__}
        self._install_final_answer()

    def execute(self, code: str) -> CodeExecutionResult:
        logs_buffer = io.StringIO()
        is_final_answer = False
        try:
            tree = ast.parse(code, mode="exec")
            with redirect_stdout(logs_buffer):
                try:
                    output = self._execute_tree(tree)
                except _FinalAnswerSignal as signal:
                    output = signal.value
                    is_final_answer = True
        except Exception as exc:
            raise CodeExecutionError(
                f"Python code execution failed: {type(exc).__name__}: {exc}"
            ) from exc

        return CodeExecutionResult(
            output=output,
            logs=logs_buffer.getvalue(),
            is_final_answer=is_final_answer,
        )

    def _install_final_answer(self) -> None:
        def final_answer(value: Any) -> None:
            raise _FinalAnswerSignal(value)

        self.state["final_answer"] = final_answer

    def _execute_tree(self, tree: ast.Module) -> Any | None:
        if not tree.body:
            return None

        last_node = tree.body[-1]
        if isinstance(last_node, ast.Expr):
            setup_tree = ast.Module(body=tree.body[:-1], type_ignores=[])
            ast.fix_missing_locations(setup_tree)
            exec(compile(setup_tree, "<mini-code-agent>", "exec"), self.state, self.state)

            expression = ast.Expression(last_node.value)
            ast.fix_missing_locations(expression)
            return eval(
                compile(expression, "<mini-code-agent>", "eval"),
                self.state,
                self.state,
            )

        exec(compile(tree, "<mini-code-agent>", "exec"), self.state, self.state)
        return None


def create_python_executor_tool(
    executor: MiniPythonExecutor,
    tool_name: str = PYTHON_EXECUTOR_TOOL_NAME,
) -> FunctionTool:
    def python_executor(code: str) -> CodeExecutionResult:
        return executor.execute(code)

    return FunctionTool(
        spec=ToolSpec(
            name=tool_name,
            description="Execute Python code in the agent's Python runtime.",
            arguments=[
                ToolArgument(
                    name="code",
                    description="Python code to execute.",
                    schema={"type": "string"},
                )
            ],
            returns="object",
        ),
        handler=python_executor,
    )
