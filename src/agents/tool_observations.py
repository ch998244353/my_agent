from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .tool_runtime import clip_tool_text

if TYPE_CHECKING:
    from .environment import CommandResult
    from .patches import PatchResult


# dict/list/tuple/基础类型保留为 JSON 结构，把其他无法序列化的对象降级成字符串
def _json_safe(value: object) -> object:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_json_safe(item) for item in value]
    return str(value)

# 把 detail 里的值转成稳定、可读、接近 JSON 风格的字符串。基础值单独处理，复杂结构用 json.dumps() 输出
def _detail_value_text(value: object) -> str:
    value = _json_safe(value)
    if isinstance(value, bool):
        return str(value).lower()
    if value is None:
        return "null"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# 一次工具执行后的统一观察结果
@dataclass(frozen=True)
class ToolObservation:
    tool_name: str
    status: str
    summary: str
    details: Mapping[str, object] = field(default_factory=dict)
    output: str | None = None
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "summary": self.summary,
            "details": _json_safe(dict(self.details)),
            "output": self.output,
            "truncated": self.truncated,
        }

    def to_text(self, max_chars: int | None = None) -> str:
        lines = [
            "Tool observation",
            f"tool: {self.tool_name}",
            f"status: {self.status}",
            f"summary: {self.summary}",
            "details:",
        ]
        if self.details:
            for key, value in self.details.items():
                lines.append(f"  {key}: {_detail_value_text(value)}")
        else:
            lines.append("  (none)")

        output = self.output if self.output is not None else "(no output)"
        lines.extend(["output:", clip_tool_text(output, max_chars)])
        if self.truncated:
            lines.append("truncated: true")
        return "\n".join(lines)


# 把命令执行结果变成一句业务摘要
def _command_summary(result: CommandResult) -> str:
    if result.timed_out:
        return "Command timed out."
    if result.returncode == 0:
        return "Command completed with exit code 0."
    if result.returncode is None:
        return "Command failed without an exit code."
    return f"Command failed with exit code {result.returncode}."


# 接收 CommandResult，返回合并后的输出文本或 None
def _command_output(result: CommandResult) -> str | None:
    sections: list[str] = []
    if result.stdout:
        sections.append(f"stdout:\n{result.stdout.rstrip()}")
    if result.stderr:
        sections.append(f"stderr:\n{result.stderr.rstrip()}")
    if not sections:
        return None
    return "\n".join(sections)



# 接收工具名、命令结果和可选截断长度，返回 ToolObservation
def command_result_observation(
    tool_name: str,
    result: CommandResult,
    *,
    max_chars: int | None = None,
) -> ToolObservation:
    output = _command_output(result)
    truncated = False
    if output is not None and max_chars is not None and len(output) > max_chars:
        output = clip_tool_text(output, max_chars)
        truncated = True

    return ToolObservation(
        tool_name=tool_name,
        status="ok" if result.succeeded else "error",
        summary=_command_summary(result),
        details={
            "command": result.command,
            "cwd": str(result.cwd),
            "returncode": result.returncode,
            "timed_out": result.timed_out,
        },
        output=output,
        truncated=truncated,
    )


# 把补丁结果变成一句业务摘要,处理三类业务：有错误就是失败；无错误且 dry_run=True 是预览成功,错误且实际写入则是补丁应用成功
def _patch_summary(result: PatchResult) -> str:
    if result.errors:
        return f"Patch failed with {len(result.errors)} error(s)."
    if result.dry_run:
        return f"Patch dry run completed with {len(result.changes)} change(s)."
    return f"Patch applied with {len(result.changes)} change(s)."


# 接收 PatchResult，返回变更和错误的可读文本
def _patch_output(result: PatchResult) -> str | None:
    sections: list[str] = []
    if result.changes:
        sections.append(
            "changes:\n"
            + "\n".join(f"{change.action} {change.path}" for change in result.changes)
        )
    if result.errors:
        error_lines: list[str] = []
        for error in result.errors:
            path_suffix = f" ({error.path})" if error.path else ""
            error_lines.append(f"{error.reason}: {error.message}{path_suffix}")
        sections.append("errors:\n" + "\n".join(error_lines))
    if not sections:
        return None
    return "\n".join(sections)


# 接收工具名、补丁结果和可选截断长度，返回 ToolObservation
def patch_result_observation(
    tool_name: str,
    result: PatchResult,
    *,
    max_chars: int | None = None,
) -> ToolObservation:
    output = _patch_output(result)
    truncated = False
    if output is not None and max_chars is not None and len(output) > max_chars:
        output = clip_tool_text(output, max_chars)
        truncated = True

    return ToolObservation(
        tool_name=tool_name,
        status="ok" if result.success else "error",
        summary=_patch_summary(result),
        details={
            "dry_run": result.dry_run,
            "changed_files": list(result.changed_files),
            "change_count": len(result.changes),
            "error_count": len(result.errors),
        },
        output=output,
        truncated=truncated,
    )


__all__ = [
    "ToolObservation",
    "command_result_observation",
    "patch_result_observation",
]
