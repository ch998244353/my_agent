from collections.abc import Mapping
from typing import Any
from .patches import parse_patch

_PATCH_PARSE_FAILED = "patch parse failed before approval summary"
_SHELL_TOOL_RISKS = {
    "run_shell_command": "shell command requires approval before execution",
    "run_test_command": "test command requires approval before execution",
}

# 接收一次 pending tool call 的工具名、调用 ID、参数和审批原因，返回用户可读摘要
def approval_summary_for_tool_call(
    tool_name: str,
    call_id: str,
    arguments: Mapping[str, Any],
    reason: str | None,
) -> str:
    reason_text = reason or "<none>"
    if tool_name in _SHELL_TOOL_RISKS:
        command_default = (
            "<default test command>"
            if tool_name == "run_test_command"
            else "<missing>"
        )
        return "\n".join(
            (
                f"tool: {tool_name}",
                f"call_id: {call_id}",
                f"command: {_string_argument(arguments, 'command', command_default)}",
                f"cwd: {_string_argument(arguments, 'cwd', '<workspace root>')}",
                f"risk: {_SHELL_TOOL_RISKS[tool_name]}",
                f"reason: {reason_text}",
            )
        )
    
    # 传入一次工具调用的信息，返回用户可读文本
    if tool_name == "apply_patch":
        changed_paths, operations = _patch_summary_parts(arguments)
        dry_run = str(bool(arguments.get("dry_run", False))).lower()
        return "\n".join(
            (
                f"tool: {tool_name}",
                f"call_id: {call_id}",
                f"dry_run: {dry_run}",
                f"changed_paths: {changed_paths}",
                f"operations: {operations}",
                "risk: patch may write workspace files",
                f"reason: {reason_text}",
            )
        )
    return "\n".join(
        (
            f"tool: {tool_name}",
            f"call_id: {call_id}",
            "risk: tool requires approval before execution",
            f"reason: {reason_text}",
        )
    )


# 从工具参数里读取 patch 或 patch_text，返回两个展示字符串：影响路径和操作列表。
def _patch_summary_parts(arguments: Mapping[str, Any]) -> tuple[str, str]:
    patch = arguments.get("patch")
    if not isinstance(patch, str):
        patch = arguments.get("patch_text")
    if not isinstance(patch, str):
        patch = ""
    try:
        parsed = parse_patch(patch)
    except ValueError:
        return _PATCH_PARSE_FAILED, _PATCH_PARSE_FAILED
    changed_paths = ", ".join(operation.path for operation in parsed)
    operations = ", ".join(
        f"{operation.action}:{operation.path}" for operation in parsed
    )
    return changed_paths, operations

def _string_argument(arguments: Mapping[str, Any], key: str, default: str) -> str:
    value = arguments.get(key)
    if isinstance(value, str) and value:
        return value
    return default

__all__ = ["approval_summary_for_tool_call"]
