from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .context_mentions import MentionCandidate
    from .run_context import RunContextWrapper


SelectedFileMode = Literal["read_only", "editable"]
_VALID_SELECTED_FILE_MODES = frozenset({"read_only", "editable"})


# 被选入任务上下文的文件
@dataclass(frozen=True)
class SelectedFile:
    path: str
    mode: SelectedFileMode
    reason: str
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "path", _normalize_selected_file_path(self.path))
        if self.mode not in _VALID_SELECTED_FILE_MODES:
            raise ValueError(f"Unsupported selected file mode: {self.mode!r}")
        if not self.reason.strip():
            raise ValueError("Selected file reason cannot be empty.")
        if not self.source.strip():
            raise ValueError("Selected file source cannot be empty.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "mode": self.mode,
            "reason": self.reason,
            "source": self.source,
        }

    def summary_line(self) -> str:
        return (
            f"- {self.path} [{self.mode}] "
            f"reason={self.reason} source={self.source}"
        )


#  管理“一组当前关注文件”
class SelectedFilesState:
    def __init__(self, files: list[SelectedFile] | None = None) -> None:
        self._files: dict[str, SelectedFile] = {}
        for selected_file in files or ():
            self.add_file(
                selected_file.path,
                mode=selected_file.mode,
                reason=selected_file.reason,
                source=selected_file.source,
            )

    def add_file(
        self,
        path: str,
        *,
        mode: SelectedFileMode,
        reason: str,
        source: str,
    ) -> SelectedFile:
        selected_file = SelectedFile(
            path=path,
            mode=mode,
            reason=reason,
            source=source,
        )
        existing = self._files.get(selected_file.path)
        if existing is not None and existing.mode == "editable":
            return existing
        if existing is not None and selected_file.mode != "editable":
            return existing
        self._files[selected_file.path] = selected_file
        return selected_file

    def add_mentions(
        self,
        candidates: Iterable[MentionCandidate],
        *,
        mode: SelectedFileMode = "read_only",
        source: str = "context_mentions",
    ) -> tuple[SelectedFile, ...]:
        added_files: list[SelectedFile] = []
        for candidate in candidates:
            matched_path = candidate.matched_path
            if not matched_path or not matched_path.strip():
                continue
            added_files.append(
                self.add_file(
                    matched_path,
                    mode=mode,
                    reason="mentioned_by_user",
                    source=source,
                )
            )
        return tuple(added_files)

    def drop_file(self, path: str) -> bool:
        normalized_path = _normalize_selected_file_path(path)
        return self._files.pop(normalized_path, None) is not None

    def get(self, path: str) -> SelectedFile | None:
        normalized_path = _normalize_selected_file_path(path)
        return self._files.get(normalized_path)

    def files(self) -> tuple[SelectedFile, ...]:
        return tuple(
            self._files[path]
            for path in sorted(self._files)
        )

    def summary(self) -> str:
        return "\n".join(selected_file.summary_line() for selected_file in self.files())


# 从用户任务文本里识别出被提到的文件，并把这些文件加入当前 agent 的 selected files
def add_task_mentions_to_selected_files(
    task: str,
    context_wrapper: RunContextWrapper,
    *,
    max_inventory_entries: int = 500,
    max_inventory_depth: int | None = None,
) -> tuple[SelectedFile, ...]:
    if not task.strip():
        return ()
    workspace = context_wrapper.workspace
    selected_files = context_wrapper.selected_files
    if workspace is None or selected_files is None:
        return ()

    from .context_mentions import resolve_mentions_against_inventory
    from .workspace import WorkspacePathError
    from .workspace_inventory import build_workspace_inventory

    try:
        inventory = build_workspace_inventory(
            workspace,
            max_entries=max_inventory_entries,
            max_depth=max_inventory_depth,
        )
    except (OSError, WorkspacePathError):
        return ()

    candidates = resolve_mentions_against_inventory(task, inventory)
    return selected_files.add_mentions(candidates)


def _normalize_selected_file_path(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        raise ValueError("Selected file path cannot be empty.")
    return normalized


__all__ = [
    "SelectedFile",
    "SelectedFileMode",
    "SelectedFilesState",
    "add_task_mentions_to_selected_files",
]
