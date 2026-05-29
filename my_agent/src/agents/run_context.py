from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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
 