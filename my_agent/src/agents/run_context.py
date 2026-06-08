from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .workspace import Workspace

ToolApprovalStatus = Literal["unknown", "pending", "approved", "rejected"]


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

    @property
    def workspace(self) -> Workspace | None:
        if not isinstance(self.context, dict):
            return None

        from .workspace import Workspace

        value = self.context.get("workspace")
        if isinstance(value, Workspace):
            return value
        return None

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
