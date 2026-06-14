from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypeVar

if TYPE_CHECKING:
    from .contracts import ToolApprovalRequest
    from .environment import Environment
    from .repo_context import RepoContext
    from .run_state import ApprovalSnapshot
    from .selected_files import SelectedFilesState
    from .verification import VerificationRunner
    from .workspace import Workspace
    from .workspace_manifest import WorkspaceManifest

ToolApprovalStatus = Literal["unknown", "pending", "approved", "rejected"]
ContextValueT = TypeVar("ContextValueT")

CONTEXT_WORKSPACE_KEY = "workspace"
CONTEXT_ENVIRONMENT_KEY = "environment"
CONTEXT_VERIFICATION_RUNNER_KEY = "verification_runner"
CONTEXT_SELECTED_FILES_KEY = "selected_files"
CONTEXT_REPO_CONTEXT_KEY = "repo_context"
CONTEXT_WORKSPACE_MANIFEST_KEY = "workspace_manifest"


@dataclass
class _ToolApprovalRecord:
    status: ToolApprovalStatus = "pending"
    rejection_message: str | None = None

# 运行依赖和业务状态
'''
当前登录用户是谁
数据库连接是什么
当前 request_id 是什么
有哪些权限
这次 run 的 trace id 是什么
usage 统计是多少
hooks/guardrails 需要读写什么状态
'''
@dataclass
class RunContextWrapper:
    context: Any | None = None
    usage: dict[str, Any] = field(default_factory=dict)  # token、请求次数、cost 统计
    metadata: dict[str, Any] = field(default_factory=dict)  # 运行时附加信息

    # 一个字典，用来保存所有工具调用的审批记录
    _tool_approvals: dict[tuple[str, str], _ToolApprovalRecord] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def _context_value(
        self,
        key: str,
        expected_type: type[ContextValueT],
    ) -> ContextValueT | None:
        if not isinstance(self.context, dict):
            return None

        value = self.context.get(key)
        if isinstance(value, expected_type):
            return value
        return None

    @property
    def workspace(self) -> Workspace | None:
        from .workspace import Workspace

        return self._context_value(CONTEXT_WORKSPACE_KEY, Workspace)

    @property
    def environment(self) -> Environment | None:
        from .environment import Environment

        return self._context_value(CONTEXT_ENVIRONMENT_KEY, Environment)

    @property
    def verification_runner(self) -> VerificationRunner | None:
        from .verification import VerificationRunner

        return self._context_value(
            CONTEXT_VERIFICATION_RUNNER_KEY,
            VerificationRunner,
        )

    @property
    def selected_files(self) -> SelectedFilesState | None:
        from .selected_files import SelectedFilesState

        return self._context_value(CONTEXT_SELECTED_FILES_KEY, SelectedFilesState)

    @property
    def repo_context(self) -> RepoContext | None:
        from .repo_context import RepoContext

        return self._context_value(CONTEXT_REPO_CONTEXT_KEY, RepoContext)

    @property
    def workspace_manifest(self) -> WorkspaceManifest | None:
        from .workspace_manifest import WorkspaceManifest

        return self._context_value(CONTEXT_WORKSPACE_MANIFEST_KEY, WorkspaceManifest)

    @staticmethod
    def _approval_key(tool_name: str, call_id: str) -> tuple[str, str]:
        return (tool_name, call_id)

    # 把某次工具调用登记为待审批状态
    def request_tool_call_approval(self, tool_name: str, call_id: str) -> None:
        key = self._approval_key(tool_name, call_id)
        self._tool_approvals.setdefault(key, _ToolApprovalRecord())

    # 批准工具调用
    def approve_tool_call(self, tool_name: str, call_id: str) -> None:
        key = self._approval_key(tool_name, call_id)
        self._tool_approvals[key] = _ToolApprovalRecord(status="approved")

    def reject_tool_call(
        self,
        tool_name: str,
        call_id: str,
        rejection_message: str | None = None,
    ) -> None:
        key = self._approval_key(tool_name, call_id)
        self._tool_approvals[key] = _ToolApprovalRecord(
            status="rejected",
            rejection_message=rejection_message,
        )

    def export_tool_approvals(
        self,
        approval_requests: Iterable[ToolApprovalRequest],
    ) -> tuple[ApprovalSnapshot, ...]:
        from .run_state import ApprovalSnapshot

        snapshots: list[ApprovalSnapshot] = []
        for request in approval_requests:
            status = self.approval_status_for(request.tool_name, request.call_id)
            if status == "unknown":
                status = "pending"
            snapshots.append(
                ApprovalSnapshot(
                    tool_name=request.tool_name,
                    call_id=request.call_id,
                    arguments=dict(request.arguments),
                    status=status,
                    reason=request.reason,
                    rejection_message=self.rejection_message_for(
                        request.tool_name,
                        request.call_id,
                    ),
                )
            )
        return tuple(snapshots)

    def import_tool_approvals(self, approvals: Iterable[ApprovalSnapshot]) -> None:
        for approval in approvals:
            if approval.status == "pending":
                self.request_tool_call_approval(approval.tool_name, approval.call_id)
            elif approval.status == "approved":
                self.approve_tool_call(approval.tool_name, approval.call_id)
            elif approval.status == "rejected":
                self.reject_tool_call(
                    approval.tool_name,
                    approval.call_id,
                    approval.rejection_message,
                )
            else:
                raise ValueError(f"Unknown approval status: {approval.status!r}")

    # 查询审批状态
    def approval_status_for(self, tool_name: str, call_id: str) -> ToolApprovalStatus:
        record = self._tool_approvals.get(self._approval_key(tool_name, call_id))
        if record is None:
            return "unknown"
        return record.status

    def rejection_message_for(self, tool_name: str, call_id: str) -> str | None:
        record = self._tool_approvals.get(self._approval_key(tool_name, call_id))
        if record is None or record.status != "rejected":
            return None
        return record.rejection_message
