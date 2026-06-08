from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import Agent, AgentMemory, ToolRegistry, Workspace, create_apply_patch_tool


def build_edit_workspace_agent(model: Any, workspace_root: str | Path = ".") -> Agent:
    workspace = Workspace(workspace_root)
    tool_registry = ToolRegistry()
    tool_registry.register(create_apply_patch_tool(workspace))

    return Agent(
        memory=AgentMemory(),
        model=model,
        name="EditWorkspaceAgent",
        instructions=(
            "Use apply_patch for workspace file edits. "
            "Prefer dry_run=true before applying risky changes."
        ),
        tool_registry=tool_registry,
    )


__all__ = ["build_edit_workspace_agent"]
