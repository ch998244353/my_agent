from __future__ import annotations

import sys

import pytest

from agents.environment import LocalEnvironment
from agents.shell_tools import create_shell_command_tool, create_test_command_tool
from agents.tools import ToolExecutionError


def _python_command(script: str) -> str:
    return f'"{sys.executable}" -c "{script}"'


def test_shell_command_tool_spec_and_default_approval(tmp_path) -> None:
    tool = create_shell_command_tool(LocalEnvironment(cwd=tmp_path))

    assert tool.name == "run_shell_command"
    assert [argument.name for argument in tool.spec.arguments] == [
        "command",
        "cwd",
        "timeout_seconds",
        "max_chars",
    ]
    assert tool.spec.returns == "string"
    approval = tool.requires_approval_for(
        None,
        None,
        {"call_id": "call-1", "arguments": {"command": "echo hello"}},
    )
    assert approval.requires_approval is True
    assert approval.call_id == "call-1"


def test_shell_command_tool_returns_command_observation(tmp_path) -> None:
    tool = create_shell_command_tool(LocalEnvironment(cwd=tmp_path))

    observation = tool.execute(
        {
            "command": _python_command("print('shell-ok')"),
        }
    )

    assert "Command observation" in observation
    assert "status: success" in observation
    assert "stdout:\nshell-ok" in observation


def test_shell_command_tool_forwards_cwd_timeout_and_clipping(tmp_path) -> None:
    child = tmp_path / "child"
    child.mkdir()
    tool = create_shell_command_tool(LocalEnvironment(cwd=tmp_path))

    observation = tool.execute(
        {
            "command": _python_command("import os; print(os.getcwd()); print('abcdef')"),
            "cwd": child,
            "timeout_seconds": 5,
            "max_chars": 3,
        }
    )

    assert str(child) in observation
    assert "abc" in observation
    assert "[tool output truncated:" in observation


def test_test_command_tool_spec_and_default_command(tmp_path) -> None:
    default_command = _python_command("print('test-default')")
    tool = create_test_command_tool(
        LocalEnvironment(cwd=tmp_path),
        default_command=default_command,
    )

    assert tool.name == "run_test_command"
    assert [argument.name for argument in tool.spec.arguments] == [
        "command",
        "cwd",
        "timeout_seconds",
        "max_chars",
    ]
    observation = tool.execute({})

    assert "status: success" in observation
    assert "test-default" in observation


def test_test_command_tool_allows_allowlisted_command(tmp_path) -> None:
    allowed_command = _python_command("print('allowed-test')")
    tool = create_test_command_tool(
        LocalEnvironment(cwd=tmp_path),
        allowed_commands=(allowed_command,),
    )

    observation = tool.execute({"command": allowed_command})

    assert "status: success" in observation
    assert "allowed-test" in observation


def test_test_command_tool_rejects_command_outside_allowlist(tmp_path) -> None:
    allowed_command = _python_command("print('allowed-test')")
    blocked_command = _python_command("print('blocked-test')")
    tool = create_test_command_tool(
        LocalEnvironment(cwd=tmp_path),
        allowed_commands=(allowed_command,),
    )

    with pytest.raises(ToolExecutionError):
        tool.execute({"command": blocked_command})


def test_shell_and_test_tools_are_public_api(tmp_path) -> None:
    from agents import (
        DEFAULT_TEST_COMMAND,
        CommandResult,
        Environment,
        LocalEnvironment,
        ToolRegistry,
        Workspace,
        create_shell_command_tool,
        create_test_command_tool,
    )

    environment = LocalEnvironment(workspace=Workspace(root=tmp_path))
    registry = ToolRegistry()
    registry.register(create_shell_command_tool(environment))
    registry.register(create_test_command_tool(environment))

    assert DEFAULT_TEST_COMMAND == "python -m pytest"
    assert CommandResult.__name__ == "CommandResult"
    assert isinstance(environment, Environment)
    assert [spec.name for spec in registry.list_specs()] == [
        "run_shell_command",
        "run_test_command",
    ]
