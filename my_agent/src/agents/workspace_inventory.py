from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


WorkspaceEntryKind = Literal["file", "directory"]
WorkspaceEntryReason = Literal[
    "ok",
    "outside_allowed_paths",
    "ignored_by_workspace_policy",
]


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
