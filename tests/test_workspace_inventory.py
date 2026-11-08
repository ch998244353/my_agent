from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from agents.workspace import Workspace
from agents.workspace_inventory import (
    WorkspaceFileEntry,
    WorkspaceInventory,
    build_workspace_inventory,
)


def test_workspace_file_entry_serializes_metadata() -> None:
    entry = WorkspaceFileEntry(
        path="src/agents/workspace.py",
        kind="file",
        size_bytes=128,
        readable=True,
        ignored=False,
        reason="ok",
    )

    assert entry.to_dict() == {
        "path": "src/agents/workspace.py",
        "kind": "file",
        "size_bytes": 128,
        "readable": True,
        "ignored": False,
        "reason": "ok",
    }


def test_workspace_inventory_serializes_entries() -> None:
    entry = WorkspaceFileEntry(
        path="src",
        kind="directory",
        size_bytes=None,
        readable=True,
        ignored=False,
        reason="ok",
    )
    inventory = WorkspaceInventory(
        root="/workspace/project",
        base_path=".",
        entries=(entry,),
        truncated=False,
    )

    assert inventory.to_dict() == {
        "root": "/workspace/project",
        "base_path": ".",
        "entries": [entry.to_dict()],
        "truncated": False,
    }


def test_workspace_inventory_models_are_frozen() -> None:
    entry = WorkspaceFileEntry(path="README.md", kind="file")

    with pytest.raises(FrozenInstanceError):
        entry.path = "changed.md"


def test_workspace_inventory_api_is_exported_from_package() -> None:
    from agents import (
        WorkspaceFileEntry as PublicWorkspaceFileEntry,
        WorkspaceInventory as PublicWorkspaceInventory,
        build_workspace_inventory as public_build_workspace_inventory,
    )

    assert PublicWorkspaceFileEntry is WorkspaceFileEntry
    assert PublicWorkspaceInventory is WorkspaceInventory
    assert public_build_workspace_inventory is build_workspace_inventory


def test_build_workspace_inventory_scans_readable_tree(tmp_path: Path) -> None:
    (tmp_path / "src" / "agents").mkdir(parents=True)
    (tmp_path / "src" / "agents" / "agent.py").write_text("print('hi')", encoding="utf-8")
    (tmp_path / "src" / "README.md").write_text("docs", encoding="utf-8")

    inventory = build_workspace_inventory(Workspace(root=tmp_path), path="src")

    assert inventory.root == str(tmp_path.resolve())
    assert inventory.base_path == "src"
    assert [entry.path for entry in inventory.entries] == [
        "src/README.md",
        "src/agents",
        "src/agents/agent.py",
    ]
    assert [entry.kind for entry in inventory.entries] == [
        "file",
        "directory",
        "file",
    ]
    assert inventory.truncated is False


def test_build_workspace_inventory_rejects_file_entry_path(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="not a directory"):
        build_workspace_inventory(Workspace(root=tmp_path), path="agent.py")


def test_build_workspace_inventory_marks_ignored_directories(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("ignored", encoding="utf-8")
    (tmp_path / "src").mkdir()

    inventory = build_workspace_inventory(Workspace(root=tmp_path))

    assert [entry.path for entry in inventory.entries] == [".git", "src"]
    ignored_entry = inventory.entries[0]
    assert ignored_entry.readable is False
    assert ignored_entry.ignored is True
    assert ignored_entry.reason == "ignored_by_workspace_policy"


def test_build_workspace_inventory_limits_scan_depth(tmp_path: Path) -> None:
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "src" / "README.md").write_text("docs", encoding="utf-8")
    (tmp_path / "src" / "pkg" / "module.py").write_text("print('hi')", encoding="utf-8")

    inventory = build_workspace_inventory(Workspace(root=tmp_path), path="src", max_depth=1)

    assert [entry.path for entry in inventory.entries] == [
        "src/README.md",
        "src/pkg",
    ]
    assert inventory.truncated is True
