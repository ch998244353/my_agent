from __future__ import annotations

import json
from pathlib import Path

from agents.workspace import Workspace
from agents.workspace_manifest import WorkspaceManifest


def test_manifest_builds_workspace_from_policy(tmp_path: Path) -> None:
    manifest = WorkspaceManifest(
        root=tmp_path,
        allowed_paths=("src",),
        ignore_patterns=(".cache",),
    )

    workspace = manifest.build_workspace()

    assert isinstance(workspace, Workspace)
    assert workspace.root == tmp_path.resolve()
    assert workspace.allowed_paths == (tmp_path.resolve() / "src",)
    assert workspace.ignore_patterns == (".cache",)


def test_manifest_defaults_match_workspace_defaults(tmp_path: Path) -> None:
    manifest = WorkspaceManifest(root=tmp_path)
    workspace = Workspace(root=tmp_path)

    assert manifest.allowed_paths == (".",)
    assert manifest.ignore_patterns == workspace.ignore_patterns
    assert manifest.build_workspace().allowed_paths == workspace.allowed_paths


def test_manifest_allows_default_test_command(tmp_path: Path) -> None:
    manifest = WorkspaceManifest(root=tmp_path)

    assert manifest.default_test_command == "python -m pytest"
    assert manifest.allowed_test_commands == ("python -m pytest",)


def test_manifest_normalizes_test_command_allowlist(tmp_path: Path) -> None:
    manifest = WorkspaceManifest(
        root=tmp_path,
        default_test_command="python -m pytest tests",
        allowed_test_commands=(
            "ruff check .",
            "python -m pytest tests",
            "ruff check .",
        ),
    )

    assert manifest.allowed_test_commands == (
        "python -m pytest tests",
        "ruff check .",
    )


def test_manifest_metadata_is_json_safe_without_env_values(tmp_path: Path) -> None:
    manifest = WorkspaceManifest(
        root=tmp_path,
        allowed_paths=("src", Path("tests")),
        ignore_patterns=(".git", "__pycache__"),
        default_test_command="python -m pytest tests",
        allowed_test_commands=("ruff check .",),
        env={"SECRET_TOKEN": "hidden", "PYTHONPATH": "src"},
    )

    metadata = manifest.metadata()
    serialized = json.dumps(metadata)

    assert metadata == {
        "root": str(tmp_path.resolve()),
        "allowed_paths": ["src", "tests"],
        "ignore_patterns": [".git", "__pycache__"],
        "default_test_command": "python -m pytest tests",
        "allowed_test_commands": [
            "python -m pytest tests",
            "ruff check .",
        ],
        "env_keys": ["PYTHONPATH", "SECRET_TOKEN"],
    }
    assert "hidden" not in serialized
    assert "src\"}" not in serialized
