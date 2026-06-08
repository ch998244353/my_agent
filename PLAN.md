# PLAN4 - Verification / Reflection Loop

## 模块定位

- 类型：添加新模块，并少量完善旧代码
- 目标：让 `my_agent` 在修改代码后能运行验证命令，把失败结果反馈给模型，形成“编辑 - 验证 - 修复”的 Coding Agent 闭环。
- 新增代码预算：约 500-1200 行，不含测试。
- 单节课原则：每节新增核心逻辑尽量不超过 80 行。
- 前置模块：需要已有 Environment/Test Tool；建议已完成 Edit Tool。
- 不做事项：不做复杂 CI 集成、不做 GitHub Actions 日志分析、不做多 Agent reviewer。
- 设计边界：`verification.py` 只负责验证策略、验证结果聚合、验证命令编排；命令执行继续复用 `Environment.run()` 和 `CommandResult`，不要重写 shell 执行层。

## 当前项目落点

| 位置 | 用途 |
|---|---|
| `my_agent/src/agents/environment.py` | 复用命令执行能力 |
| `my_agent/src/agents/shell_tools.py` | 复用 test command tool |
| `my_agent/src/agents/run_config.py` | 放 verification policy |
| `my_agent/src/agents/run_steps.py` | 工具结果后追加验证 observation |
| `my_agent/src/agents/run_loop.py` | 第 5-6 课如需在主循环层控制验证触发和 attempts 计数，只允许围绕验证闭环做最小接入 |
| `my_agent/src/agents/result.py` | 记录 verification metadata |
| `my_agent/src/agents/__init__.py` | 暴露 verification API |

## 计划新增文件

| 文件 | 职责 |
|---|---|
| `my_agent/src/agents/verification.py` | `VerificationPolicy`、`VerificationResult`、`VerificationRunner` |
| `my_agent/tests/test_verification.py` | 验证策略和结果格式单测 |
| `my_agent/tests/test_verification_loop.py` | edit 后自动验证的 loop 测试 |

## 参考代码位置

| 参考项目 | 位置 | 借鉴点 |
|---|---|---|
| `reference/aider-main` | `aider/linter.py` | lint/test 结果如何转为模型反馈 |
| `reference/aider-main` | `aider/coders/base_coder.py` | `lint_edited()`、`apply_updates()` 后的反馈循环 |
| `reference/mini-swe-agent-main` | `src/minisweagent/agents/default.py` | action-observation 循环和 trajectory |
| `reference/mini-swe-agent-main` | `src/minisweagent/environments/local.py` | 命令执行结果结构 |
| `reference/OpenHands-main` | `openhands/app_server/event/event_service.py` | 后期 event log 思路，本模块只轻量借鉴 |

## 规划修订说明

- OpenAI Responses / function calling 的工具流是“模型请求工具 - 应用执行工具 - 工具输出回写给模型 - 模型继续响应”。本模块的 verification observation 应放在工具结果进入模型上下文的链路上，而不是只作为后台日志。
- `my_agent` 已有 `CommandResult.to_observation()` 和 `run_test_command` 的 allowlist 机制，新验证模块应包装这些能力，重点新增“何时验证、验证几次、如何汇总失败信息”。
- 旧计划只列出 `run_steps.py`，但实际课程到自动验证触发和 max attempts 时可能需要主循环协作，因此把 `run_loop.py` 加入受控范围；未到对应课次前不提前改它。

## 课程拆分

| 课次 | 小目标 | 主要改动 | 新增逻辑上限 | 完成标准 |
|---:|---|---|---:|---|
| 1 | 定义验证策略 | 新增 `VerificationPolicy`，包含 commands、auto_after_tools、max_attempts | 70 行 | 策略默认关闭，不影响现有测试 |
| 2 | 定义验证结果 | 新增 `VerificationResult`，统一 command、returncode、passed、output | 60 行 | 单个命令结果可格式化为 observation |
| 3 | 实现 VerificationRunner | 调用 `Environment` 顺序执行验证命令 | 80 行 | 多命令执行，任一失败即 failed |
| 4 | 接入 run config | 在 `RunConfig` 中挂可选 verification policy | 50 行 | 不设置 policy 时行为完全不变 |
| 5 | 接入工具后验证 | 当 `apply_patch` 等工具成功后，自动追加验证 observation | 80 行 | edit 后模型能看到测试失败信息 |
| 6 | 控制反思次数 | 用 max attempts 防止无限“改 - 测 - 改” | 70 行 | 达到次数后停止自动验证并提示 |
| 7 | 记录结果摘要 | 在 `RunResult` 或 step record 中保存 verification summary | 70 行 | 最终答复可说明验证是否通过 |

## 教学重点

| 主题 | 讲解重点 |
|---|---|
| 闭环 | Coding Agent 的核心不是会改代码，而是会验证改动 |
| observation 设计 | 测试失败信息要短、准、可行动 |
| 自动化边界 | 自动验证可以默认关闭，避免破坏普通 Agent |
| 失败控制 | max attempts 比无限循环更适合教学项目 |

## 上下文压缩保护点

每节课结束时必须记录： 
1. `VerificationPolicy` 当前字段。
2. 自动验证触发哪些 tool。
3. 最近一次失败 observation 的格式。
4. max attempts 当前规则。
 
