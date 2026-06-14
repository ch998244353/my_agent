from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .patches import parse_patch


SafetyAction = Literal["allow", "approve", "block"]


# 决策结果类。业务上它表示 agent 准备执行某条命令时，系统给出的安全结论：直接允许、暂停审批或禁止
@dataclass(frozen=True)
class SafetyDecision:
    action: SafetyAction
    reason: str
    category: str

    @property
    def requires_approval(self) -> bool:
        return self.action != "allow"

    @property
    def blocked(self) -> bool:
        return self.action == "block"


# shell 命令分类策略类,将一条命令分类classify返回safetydecision,并用方法。这里先保存三组业务规则：安全前缀、需要审批的前缀、明确禁止的片段
@dataclass(frozen=True)
class ShellCommandPolicy:
    safe_prefixes: tuple[str, ...] = (
        "python -m pytest",
        "pytest",
        "python -m compileall",
        "git status",
        "git diff",
    )
    approval_prefixes: tuple[str, ...] = (
        "git",
        "pip",
        "python -m pip",
        "npm",
        "pnpm",
        "yarn",
    )
    blocked_fragments: tuple[str, ...] = (
        "rm -rf /",
        "git reset --hard",
        "git clean -fd",
        "git checkout --",
        "del /s",
        "remove-item -recurse",
        "format ",
        "shutdown",
        "mkfs",
    )

    # 接收一条 shell 命令文本，返回分类结果
    def classify(self, command: str) -> SafetyDecision:
        normalized = _normalize_command(command)
        normalized_lower = normalized.lower()

        if not normalized:
            return SafetyDecision(
                action="approve",
                reason="Empty shell command requires human review.",
                category="unknown_shell_command",
            )

        if any(fragment in normalized_lower for fragment in self.blocked_fragments):
            return SafetyDecision(
                action="block",
                reason="Command contains an explicitly blocked shell fragment.",
                category="blocked_shell_command",
            )

        if any(
            _matches_prefix(normalized_lower, safe_prefix)
            for safe_prefix in self.safe_prefixes
        ):
            return SafetyDecision(
                action="allow",
                reason="Command matches safe shell prefix.",
                category="safe_shell_command",
            )

        if any(
            _matches_prefix(normalized_lower, approval_prefix)
            for approval_prefix in self.approval_prefixes
        ):
            return SafetyDecision(
                action="approve",
                reason="Command matches approval-required shell prefix.",
                category="approval_shell_command",
            )

        return SafetyDecision(
            action="approve",
            reason="Command does not match a known safe shell prefix.",
            category="unknown_shell_command",
        )

    # 接收工具运行上下文、模型传入参数、调用 ID，返回是否需要审批
    def needs_approval(
        self,
        context_wrapper: Any,
        arguments: dict[str, Any],
        call_id: str | None,
    ) -> bool:
        _ = context_wrapper, call_id
        command = arguments.get("command")
        if not isinstance(command, str):
            command = ""
        return self.classify(command).requires_approval


@dataclass(frozen=True)
class PatchApprovalPolicy:
    approve_deletes: bool = True
    approve_large_changes: bool = True
    large_change_threshold: int = 3
 
    # 接收补丁文本和是否 dry run，返回安全决策。它处理的业务是：预览补丁不写盘可放行；实际删除文件或大范围改动要人工确认；非法 patch 不审批，交给补丁工具正常报错
    def classify_patch_text(self, patch: str, dry_run: bool) -> SafetyDecision:
        if dry_run:
            return SafetyDecision(
                action="allow",
                reason="Dry-run patch validation does not write files.",
                category="dry_run_patch",
            )

        try:
            operations = parse_patch(patch)
        except ValueError:
            return SafetyDecision(
                action="allow",
                reason="Invalid patch text should fail inside the patch tool without approval.",
                category="invalid_patch",
            )

        if self.approve_deletes and any(
            operation.action == "delete" for operation in operations
        ):
            return SafetyDecision(
                action="approve",
                reason="Patch deletes files and requires approval.",
                category="delete_patch",
            )

        if (
            self.approve_large_changes
            and len(operations) > self.large_change_threshold
        ):
            return SafetyDecision(
                action="approve",
                reason=(
                    f"Patch touches {len(operations)} files, exceeding "
                    f"threshold {self.large_change_threshold}."
                ),
                category="large_patch",
            )

        return SafetyDecision(
            action="approve",
            reason="Actual patch writes require approval.",
            category="write_patch",
        )

    def needs_approval(
        self,
        context_wrapper: Any,
        arguments: dict[str, Any],
        call_id: str | None,
    ) -> bool:
        _ = context_wrapper, call_id
        patch = arguments.get("patch")
        if not isinstance(patch, str):
            patch = ""
        dry_run = bool(arguments.get("dry_run", False))
        return self.classify_patch_text(patch, dry_run).requires_approval


def _normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def _matches_prefix(command: str, prefix: str) -> bool:
    normalized_prefix = _normalize_command(prefix).lower()
    return command == normalized_prefix or command.startswith(f"{normalized_prefix} ")
