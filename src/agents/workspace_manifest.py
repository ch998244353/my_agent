from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from .workspace import Workspace


# 一次 coding agent 任务的工作区策略
@dataclass(frozen=True)
class WorkspaceManifest:
    root: Path | str
    allowed_paths: tuple[Path | str, ...] = (".",)
    ignore_patterns: tuple[str, ...] = (".git", ".codegraph", "__pycache__")
    default_test_command: str = "python -m pytest"
    allowed_test_commands: tuple[str, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_paths", tuple(self.allowed_paths))
        object.__setattr__(self, "ignore_patterns", tuple(self.ignore_patterns))
        object.__setattr__(self, "env", dict(self.env))

        commands: list[str] = []
        for command in (self.default_test_command, *self.allowed_test_commands):
            if command not in commands:
                commands.append(command)
        object.__setattr__(self, "allowed_test_commands", tuple(commands))

    def resolved_root(self) -> Path:
        return Path(self.root).expanduser().resolve(strict=False)

    # 把“策略说明”转换成真正负责路径安全的运行对象
    def build_workspace(self) -> Workspace:
        return Workspace(
            root=self.resolved_root(),
            allowed_paths=self.allowed_paths,
            ignore_patterns=self.ignore_patterns,
        )

    def metadata(self) -> dict[str, object]:
        return {
            "root": str(self.resolved_root()),
            "allowed_paths": [self._metadata_path(path) for path in self.allowed_paths],
            "ignore_patterns": list(self.ignore_patterns),
            "default_test_command": self.default_test_command,
            "allowed_test_commands": list(self.allowed_test_commands),
            "env_keys": sorted(self.env),
        }

    @staticmethod
    def _metadata_path(path: Path | str) -> str:
        return Path(path).as_posix()
