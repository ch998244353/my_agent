from __future__ import annotations

from pathlib import Path

from .contracts import ToolArgument, ToolSpec
from .environment import Environment
from .tools import FunctionTool, ToolApproval, ToolExecutionError


DEFAULT_TEST_COMMAND = "python -m pytest"


def create_shell_command_tool(
    environment: Environment,
    *,
    needs_approval: ToolApproval = True,
) -> FunctionTool:
    def run_shell_command(
        command: str,
        cwd: str | Path | None = None,
        timeout_seconds: float | None = None,
        max_chars: int | None = None,
    ) -> str:
        result = environment.run(
            command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
        return result.to_observation(max_chars=max_chars)

    return FunctionTool(
        spec=ToolSpec(
            name="run_shell_command",
            description="Run a shell command in the configured environment.",
            arguments=[
                ToolArgument(
                    name="command",
                    description="Shell command to execute.",
                    schema={"type": "string"},
                ),
                ToolArgument(
                    name="cwd",
                    description="Optional working directory for this command.",
                    schema={"type": "string"},
                    required=False,
                ),
                ToolArgument(
                    name="timeout_seconds",
                    description="Optional timeout for this command.",
                    schema={"type": "number"},
                    required=False,
                ),
                ToolArgument(
                    name="max_chars",
                    description="Maximum stdout and stderr characters to include.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="string",
        ),
        handler=run_shell_command,
        needs_approval=needs_approval,
    )


def create_test_command_tool(
    environment: Environment,
    *,
    default_command: str = DEFAULT_TEST_COMMAND,
    allowed_commands: tuple[str, ...] | None = None,
    needs_approval: ToolApproval = False,
) -> FunctionTool:
    command_allowlist = allowed_commands or (default_command,)

    def run_test_command(
        command: str | None = None,
        cwd: str | Path | None = None,
        timeout_seconds: float | None = None,
        max_chars: int | None = None,
    ) -> str:
        selected_command = command or default_command
        if selected_command not in command_allowlist:
            raise ToolExecutionError(
                "run_test_command",
                f"Command is not in the test command allowlist: {selected_command}",
            )
        result = environment.run(
            selected_command,
            cwd=cwd,
            timeout_seconds=timeout_seconds,
        )
        return result.to_observation(max_chars=max_chars)

    return FunctionTool(
        spec=ToolSpec(
            name="run_test_command",
            description="Run an allowed test command in the configured environment.",
            arguments=[
                ToolArgument(
                    name="command",
                    description="Optional allowed test command to execute.",
                    schema={"type": "string"},
                    required=False,
                ),
                ToolArgument(
                    name="cwd",
                    description="Optional working directory for this test command.",
                    schema={"type": "string"},
                    required=False,
                ),
                ToolArgument(
                    name="timeout_seconds",
                    description="Optional timeout for this test command.",
                    schema={"type": "number"},
                    required=False,
                ),
                ToolArgument(
                    name="max_chars",
                    description="Maximum stdout and stderr characters to include.",
                    schema={"type": "integer"},
                    required=False,
                ),
            ],
            returns="string",
        ),
        handler=run_test_command,
        needs_approval=needs_approval,
    )
