from __future__ import annotations

from pathlib import Path

from agents import (
    LocalEnvironment,
    ToolRegistry,
    Workspace,
    create_shell_command_tool,
    create_test_command_tool,
)


def build_shell_test_tool_registry(workspace_root: str | Path = ".") -> ToolRegistry:
    workspace = Workspace(root=workspace_root)
    environment = LocalEnvironment(workspace=workspace)
    registry = ToolRegistry()
    registry.register(create_shell_command_tool(environment))
    registry.register(create_test_command_tool(environment))
    return registry


if __name__ == "__main__":
    tool_registry = build_shell_test_tool_registry()
    for tool_spec in tool_registry.list_specs():
        print(tool_spec.name)
