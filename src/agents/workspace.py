from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path


class WorkspacePathError(ValueError):
    """Raised when a path cannot be safely resolved inside a workspace."""

    def __init__(
        self,
        path: str | Path,
        root: Path,
        reason: str = "outside workspace root",
    ) -> None:
        self.path = Path(path)
        self.root = root
        self.reason = reason
        super().__init__(f"Path {self.path!s} is {reason}: {root!s}")


# 限制agent工作路径
@dataclass(frozen=True)
class Workspace:
    root: str | Path
    allowed_paths: tuple[str | Path, ...] = (".",)
    ignore_patterns: tuple[str, ...] = (".git", ".codegraph", "__pycache__")


    '''
    把 self.allowed_paths 里的每个路径都 resolve 成安全的标准路径，
    然后保存成不可变 tuple
    即使这个 dataclass 是 frozen=True 也能在 __post_init__ 初始化阶段设置它。
    '''
    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "root",
            Path(self.root).expanduser().resolve(strict=False),
        )
        object.__setattr__(
            self,
            "allowed_paths",
            tuple(self.resolve_path(path) for path in self.allowed_paths),
        )


    # 把传入的路径转换成 workspace 内部的安全绝对路径；如果路径跑出了 workspace 根目录，就报错
    def resolve_path(self, path: str | Path = ".") -> Path:
        # 把 path 转成 Path 对象并展开 ~
        raw_path = Path(path).expanduser()
        # 判断是不是绝对路径
        if raw_path.is_absolute():
            candidate = raw_path
        else:
            candidate = self.root / raw_path
        # 解析成标准路径
        resolved = candidate.resolve(strict=False)
        # 检查路径是否还在 workspace 内
        if not self._is_under_root(resolved):
            raise WorkspacePathError(path, self.root)
        return resolved


    # 把绝对路径转成相对于 workspace root 的路径
    def relative_path(self, path: str | Path) -> Path:
        return self.resolve_path(path).relative_to(self.root)


    # 判断路径是否在允许访问的目录里
    def is_allowed(self, path: str | Path) -> bool:
        try:
            resolved = self.resolve_path(path)
        except WorkspacePathError:
            return False
        return any(self._is_under(resolved, allowed) for allowed in self.allowed_paths)


    # 判断路径是否命中忽略规则
    def is_ignored(self, path: str | Path) -> bool:
        try:
            relative = self.relative_path(path)
        except WorkspacePathError:
            return False
        relative_text = relative.as_posix()
        return any(
            pattern in relative.parts or fnmatch(relative_text, pattern)
            for pattern in self.ignore_patterns
        )


    # 最终检查：必须合法、允许、未忽略
    def ensure_readable_path(self, path: str | Path) -> Path:
        resolved = self.resolve_path(path)
        if not self.is_allowed(resolved):
            raise WorkspacePathError(path, self.root, "outside allowed paths")
        if self.is_ignored(resolved):
            raise WorkspacePathError(path, self.root, "ignored by workspace policy")
        return resolved


    # 判断路径是否在 workspace root 下
    def _is_under_root(self, path: Path) -> bool:
        return self._is_under(path, self.root)


    # 通用版“是否在某个目录下”
    @staticmethod
    def _is_under(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
        except ValueError:
            return False
        return True
