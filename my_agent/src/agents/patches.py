from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

from .workspace import Workspace, WorkspacePathError


PatchAction = Literal["add", "update", "delete"]
BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
_ADD_FILE = "*** Add File: "
_UPDATE_FILE = "*** Update File: "
_DELETE_FILE = "*** Delete File: "


@dataclass(frozen=True)
class PatchChange:
    action: PatchAction
    path: str



# 对某个文件的操作
@dataclass(frozen=True)
class PatchOperation:
    action: PatchAction
    path: str
    content: str | None = None


@dataclass(frozen=True)
class PatchError:
    reason: str
    message: str
    path: str | None = None

    def to_observation(self) -> dict[str, str | None]:
        return asdict(self)


class PatchApplyError(ValueError):
    def __init__(self, reason: str, message: str, path: str | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(message)


# 某次更改的结果汇总
@dataclass(frozen=True)
class PatchResult:
    dry_run: bool
    changes: tuple[PatchChange, ...] = ()
    errors: tuple[PatchError, ...] = ()

    @property
    def success(self) -> bool:
        return not self.errors

    @property
    def changed_files(self) -> tuple[str, ...]:
        return tuple(change.path for change in self.changes)

    def to_observation(self) -> dict[str, object]:
        return {
            "success": self.success,
            "dry_run": self.dry_run,
            "changed_files": list(self.changed_files),
            "changes": [asdict(change) for change in self.changes],
            "errors": [error.to_observation() for error in self.errors],
        }


# 根军文本 生成文件操作patchoperation
def parse_patch(patch_text: str) -> tuple[PatchOperation, ...]:
    """Parse the minimal edit patch format.
    Format:
    *** Begin Patch
    *** Add File: path
    +new file line
    *** Update File: path
    @@
    -old line
    +new line
    *** Delete File: path
    *** End Patch
    """
    lines = patch_text.splitlines()
    if not lines or lines[0] != BEGIN_PATCH:
        raise ValueError("Patch input must start with '*** Begin Patch'.")
    if len(lines) < 2 or lines[-1] != END_PATCH:
        raise ValueError("Patch input must end with '*** End Patch'.")

    operations: list[PatchOperation] = []
    index = 1
    while index < len(lines) - 1:
        line = lines[index]
        if line.startswith(_ADD_FILE):
            operation, index = _parse_add_file(lines, index)
        elif line.startswith(_UPDATE_FILE):
            operation, index = _parse_update_file(lines, index)
        elif line.startswith(_DELETE_FILE):
            operation, index = _parse_delete_file(lines, index)
        else:
            raise ValueError(f"Invalid patch operation header: {line}")
        operations.append(operation)

    if not operations:
        raise ValueError("Patch input must include at least one operation.")
    return tuple(operations)


# 逐个检查 PatchOperation：绝对路径直接拒绝, 检查是否在 workspace root 内,它只做路径校验，不读文件、不写磁盘
def validate_patch_paths(
    operations: tuple[PatchOperation, ...],
    workspace: Workspace,
    *,
    dry_run: bool = True,
) -> PatchResult:
    changes: list[PatchChange] = []
    errors: list[PatchError] = []

    for operation in operations:
        try:
            if Path(operation.path).is_absolute():
                raise WorkspacePathError(
                    operation.path,
                    workspace.root,
                    "absolute patch paths are not allowed",
                )
            resolved_path = workspace.ensure_readable_path(operation.path)
        except WorkspacePathError as exc:
            errors.append(
                PatchError(
                    reason="invalid_path",
                    message=str(exc),
                    path=operation.path,
                )
            )
            continue
        changes.append(
            PatchChange(
                action=operation.action,
                path=workspace.relative_path(resolved_path).as_posix(),
            )
        )

    return PatchResult(
        dry_run=dry_run,
        changes=tuple(changes),
        errors=tuple(errors),
    )


#调用 parse_patch() 把文本协议解析成 PatchOperation,并用上一节的 validate_patch_paths() 做路径校验
def dry_run_patch(patch_text: str, workspace: Workspace) -> PatchResult:
    try:
        operations = parse_patch(patch_text)
    except ValueError as exc:
        return PatchResult(
            dry_run=True,
            errors=(_invalid_patch_error(exc),),
        )

    return validate_patch_paths(operations, workspace, dry_run=True)


# 从普通文本解析后执行patchoperation操作 : 先调用 parse_patch()，再调用 validate_patch_paths()
def apply_patch(patch_text: str, workspace: Workspace) -> PatchResult:
    try:
        operations = parse_patch(patch_text)
    except ValueError as exc:
        return PatchResult(
            dry_run=False,
            errors=(_invalid_patch_error(exc),),
        )

    result = validate_patch_paths(operations, workspace, dry_run=False)
    if result.errors:
        return result

    errors: list[PatchError] = []
    for operation in operations:
        try:
            _apply_operation(operation, workspace)
        except PatchApplyError as exc:
            errors.append(
                PatchError(
                    reason=exc.reason,
                    message=str(exc),
                    path=exc.path or operation.path,
                )
            )
        except (OSError, UnicodeError) as exc:
            errors.append(
                PatchError(
                    reason="apply_failed",
                    message=str(exc),
                    path=operation.path,
                )
            )

    if errors:
        return PatchResult(dry_run=False, changes=result.changes, errors=tuple(errors))
    return result


def _invalid_patch_error(error: ValueError) -> PatchError:
    return PatchError(
        reason="invalid_patch",
        message=(
            f"{error} Use the patch envelope: *** Begin Patch ... "
            "*** End Patch, with Add/Update/Delete File headers."
        ),
    )


# 执行单个patch operation操作
def _apply_operation(operation: PatchOperation, workspace: Workspace) -> None:
    target = workspace.ensure_readable_path(operation.path)
    if operation.action == "add":
        if target.exists():
            raise PatchApplyError(
                "add_exists",
                f"Add File target already exists: {operation.path}",
                operation.path,
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(operation.content or "", encoding="utf-8")
        return
    if operation.action == "update":
        if not target.exists():
            raise PatchApplyError(
                "update_missing",
                f"Update File target does not exist: {operation.path}",
                operation.path,
            )
        original = target.read_text(encoding="utf-8")
        target.write_text(
            _apply_update_content(original, operation.content or "", operation.path),
            encoding="utf-8",
        )
        return
    if operation.action == "delete":
        if not target.exists():
            raise PatchApplyError(
                "delete_missing",
                f"Delete File target does not exist: {operation.path}",
                operation.path,
            )
        target.unlink()
        return
    raise PatchApplyError(
        "unsupported_action",
        f"Unsupported patch action: {operation.action}",
        operation.path,
    )


def _apply_update_content(original: str, diff: str, path: str) -> str:
    before, after = _diff_before_after(diff)
    if not before:
        raise PatchApplyError(
            "update_empty",
            f"Update File patch for {path} must include removed or context lines.",
            path,
        )
    if before not in original:
        raise PatchApplyError(
            "update_no_match",
            (
                f"{path} does not contain the patch's old/context lines. "
                f"Use exact current file content and retry:\n{before}"
            ),
            path,
        )
    return original.replace(before, after, 1)



# 将一段文本转化为修改前修改后版本
def _diff_before_after(diff: str) -> tuple[str, str]:
    before_lines: list[str] = []
    after_lines: list[str] = []
    for line in diff.splitlines():
        if line.startswith("@@"):
            continue
        if line.startswith("-"):
            before_lines.append(line[1:])
            continue
        if line.startswith("+"):
            after_lines.append(line[1:])
            continue
        if line.startswith(" "):
            line = line[1:]
        before_lines.append(line)
        after_lines.append(line)
    return _join_content(before_lines), _join_content(after_lines)


def _parse_add_file(lines: list[str], index: int) -> tuple[PatchOperation, int]:
    path = _parse_path_header(lines[index], _ADD_FILE)
    index += 1
    content_lines: list[str] = []
    while index < len(lines) - 1 and not _is_operation_header(lines[index]):
        line = lines[index]
        if not line.startswith("+"):
            raise ValueError(f"Add File line must start with '+': {line}")
        content_lines.append(line[1:])
        index += 1
    return PatchOperation("add", path, _join_content(content_lines)), index


def _parse_update_file(lines: list[str], index: int) -> tuple[PatchOperation, int]:
    path = _parse_path_header(lines[index], _UPDATE_FILE)
    index += 1
    diff_lines: list[str] = []
    while index < len(lines) - 1 and not _is_operation_header(lines[index]):
        diff_lines.append(lines[index])
        index += 1
    if not diff_lines:
        raise ValueError(f"Update File patch for {path} must include a hunk.")
    return PatchOperation("update", path, _join_content(diff_lines)), index


def _parse_delete_file(lines: list[str], index: int) -> tuple[PatchOperation, int]:
    path = _parse_path_header(lines[index], _DELETE_FILE)
    index += 1
    if index < len(lines) - 1 and not _is_operation_header(lines[index]):
        raise ValueError(f"Delete File patch for {path} must not include content.")
    return PatchOperation("delete", path), index


def _parse_path_header(line: str, prefix: str) -> str:
    path = line.removeprefix(prefix).strip()
    if not path:
        raise ValueError(f"Missing path in patch header: {line}")
    return path


def _is_operation_header(line: str) -> bool:
    return line.startswith((_ADD_FILE, _UPDATE_FILE, _DELETE_FILE))


def _join_content(lines: list[str]) -> str:
    return "\n".join(lines) + "\n"


__all__ = [
    "BEGIN_PATCH",
    "END_PATCH",
    "PatchAction",
    "PatchApplyError",
    "PatchChange",
    "PatchError",
    "PatchOperation",
    "PatchResult",
    "apply_patch",
    "dry_run_patch",
    "parse_patch",
    "validate_patch_paths",
]
