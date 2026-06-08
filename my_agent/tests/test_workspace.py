from pathlib import Path

import pytest

from agents.run_context import RunContextWrapper
from agents.workspace import Workspace, WorkspacePathError


def test_workspace_resolves_relative_path_inside_root(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)

    resolved = workspace.resolve_path("src/agents/agent.py")

    assert resolved == tmp_path / "src" / "agents" / "agent.py"


def test_workspace_rejects_path_escape(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)

    with pytest.raises(WorkspacePathError):
        workspace.resolve_path("../outside.py")


def test_workspace_returns_relative_path(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)

    relative = workspace.relative_path(tmp_path / "src" / "agents")

    assert relative == Path("src/agents")


def test_workspace_default_policy_allows_root_path(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)

    readable = workspace.ensure_readable_path("src/agents/agent.py")

    assert readable == tmp_path / "src" / "agents" / "agent.py"


def test_workspace_rejects_path_outside_allowed_paths(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path, allowed_paths=("src",))

    with pytest.raises(WorkspacePathError):
        workspace.ensure_readable_path("tests/test_workspace.py")


def test_workspace_rejects_ignored_path(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)

    with pytest.raises(WorkspacePathError):
        workspace.ensure_readable_path(".git/config")


def test_run_context_wrapper_exposes_workspace_from_context(tmp_path: Path) -> None:
    workspace = Workspace(root=tmp_path)
    context = RunContextWrapper(context={"workspace": workspace})

    assert context.workspace is workspace


def test_run_context_wrapper_workspace_is_none_when_missing() -> None:
    context = RunContextWrapper(context={"request_id": "run_123"})

    assert context.workspace is None
