from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import (
    Agent,
    AgentMemory,
    RunConfig,
    ToolRegistry,
    Workspace,
    create_readonly_workspace_tools,
)


READ_ONLY_REPO_INSTRUCTIONS = (
    "You are a read-only repo-aware assistant. Use list_workspace_files, "
    "read_workspace_file, and search_workspace_text to inspect the repository. "
    "Do not claim to edit files, run shell commands, run tests, or use git."
)


class ReadOnlyWorkspaceAgentSetup:
    def __init__(
        self,
        workspace: Workspace,
        agent: Agent,
        run_config: RunConfig,
    ) -> None:
        self.workspace = workspace
        self.agent = agent
        self.run_config = run_config


def build_read_only_workspace_agent(
    root: str | Path,
    model: Any,
    *,
    allowed_paths: tuple[str | Path, ...] = (".",),
    ignore_patterns: tuple[str, ...] = (".git", ".codegraph", "__pycache__"),
    name: str = "ReadOnlyWorkspaceAgent",
) -> ReadOnlyWorkspaceAgentSetup:
    workspace = Workspace(
        root=root,
        allowed_paths=allowed_paths,
        ignore_patterns=ignore_patterns,
    )
    tool_registry = ToolRegistry()
    for tool in create_readonly_workspace_tools(workspace):
        tool_registry.register(tool)

    agent = Agent(
        memory=AgentMemory(),
        model=model,
        name=name,
        instructions=READ_ONLY_REPO_INSTRUCTIONS,
        tool_registry=tool_registry,
    )
    run_config = RunConfig(context={"workspace": workspace})

    return ReadOnlyWorkspaceAgentSetup(
        workspace=workspace,
        agent=agent,
        run_config=run_config,
    )
