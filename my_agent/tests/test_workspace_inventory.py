from dataclasses import FrozenInstanceError

import pytest

from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory


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
