from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .workspace import Workspace, WorkspacePathError


WorkspaceEntryKind = Literal["file", "directory"]
WorkspaceEntryReason = Literal[
    "ok",
    "outside_allowed_paths",
    "ignored_by_workspace_policy",
]


# 描述单个文件或目录的路径、类型、大小、可读性和忽略原因
@dataclass(frozen=True)
class WorkspaceFileEntry:
    path: str
    kind: WorkspaceEntryKind
    size_bytes: int | None = None
    readable: bool = True
    ignored: bool = False
    reason: WorkspaceEntryReason | str = "ok"

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "kind": self.kind,
            "size_bytes": self.size_bytes,
            "readable": self.readable,
            "ignored": self.ignored,
            "reason": self.reason,
        }


# 描述一次扫描结果，包含 root、base_path、entries 和 truncated
@dataclass(frozen=True)
class WorkspaceInventory:
    root: str
    base_path: str
    entries: tuple[WorkspaceFileEntry, ...] = field(default_factory=tuple)
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "base_path": self.base_path,
            "entries": [entry.to_dict() for entry in self.entries],
            "truncated": self.truncated,
        }


# 接收 Workspace 和起始 path，返回可复用清单
def build_workspace_inventory(
    workspace: Workspace,
    path: str | Path = ".",
    max_entries: int = 200,
    max_depth: int | None = None,
) -> WorkspaceInventory:
    directory = workspace.ensure_readable_path(path)
    if not directory.is_dir():
        raise WorkspacePathError(path, workspace.root, "not a directory")

    entries: list[WorkspaceFileEntry] = []
    limit = max(0, int(max_entries))
    depth_limit = None if max_depth is None else max(0, int(max_depth))
    truncated = _append_directory_entries(
        workspace,
        directory,
        entries,
        limit,
        current_depth=0,
        max_depth=depth_limit,
    )

    return WorkspaceInventory(
        root=str(workspace.root),
        base_path=workspace.relative_path(directory).as_posix(),
        entries=tuple(entries),
        truncated=truncated,
    )


# 递归扫描
def _append_directory_entries(
    workspace: Workspace,
    directory: Path,
    entries: list[WorkspaceFileEntry],
    limit: int,
    current_depth: int,
    max_depth: int | None,
) -> bool:
    truncated = False
    for child in _candidate_children(workspace, directory):
        child_depth = current_depth + 1
        if max_depth is not None and child_depth > max_depth:
            truncated = True
            continue
        if len(entries) >= limit:
            return True
        entry = _entry_from_path(workspace, child)
        entries.append(entry)
        if entry.readable and child.is_dir():
            child_truncated = _append_directory_entries(
                workspace,
                child,
                entries,
                limit,
                current_depth=child_depth,
                max_depth=max_depth,
            )
            if child_truncated:
                truncated = True
                if len(entries) >= limit:
                    return True
    return truncated


# 备选子项
def _candidate_children(workspace: Workspace, directory: Path) -> list[Path]:
    candidate_children: list[Path] = []
    for child in sorted(
        directory.iterdir(),
        key=lambda item: workspace.relative_path(item).as_posix(),
    ):
        try:
            candidate_children.append(workspace.resolve_path(child))
        except WorkspacePathError:
            continue
    return candidate_children


def _entry_from_path(workspace: Workspace, path: Path) -> WorkspaceFileEntry:
    is_directory = path.is_dir()
    readable, ignored, reason = _entry_policy(workspace, path)
    return WorkspaceFileEntry(
        path=workspace.relative_path(path).as_posix(),
        kind="directory" if is_directory else "file",
        size_bytes=None if is_directory else path.stat().st_size,
        readable=readable,
        ignored=ignored,
        reason=reason,
    )


# 集中判断 allowed、ignored 和 ok 三类状态
def _entry_policy(workspace: Workspace, path: Path) -> tuple[bool, bool, WorkspaceEntryReason]:
    if not workspace.is_allowed(path):
        return False, False, "outside_allowed_paths"
    if workspace.is_ignored(path):
        return False, True, "ignored_by_workspace_policy"
    return True, False, "ok"
