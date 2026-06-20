# 方案 A 总体验证书：可恢复的本地 Coding 执行闭环

## 1. 验证目标

本文件用于检查 `PLAN01.md` 到 `PLAN06.md` 全部完成后，`my_agent` 是否真正具备“可恢复的本地 Coding 执行闭环”。

最终能力不是单点功能，而是一个串联成果：

1. coding CLI 能启动本地 workspace task。
2. 危险 shell 或真实 edit 触发 pending approval。
3. pending 时保存 state JSON。
4. 用户通过 CLI approve/reject 恢复。
5. 恢复后继续执行 tool 或记录拒绝 observation。
6. 执行后按策略运行 verification。
7. 全过程写入 trajectory JSONL。
8. 所有行为有测试，不依赖真实 OpenAI API。
9. 每个 PLAN 都包含并完成最后一节“维护项目结构文档说明”课程；这节课必须基于当前文件与 git 上一个 commit 版本之间的 diff 更新 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm` 中受影响的架构文档，使 docs/llm 与最终代码、测试和运行流程一致。

## 2. 文件级验收

必须存在并被实现使用：

- `my_agent/src/agents/run_state.py`
  - 有 JSON-safe snapshot export/import 入口。
- `my_agent/src/agents/coding_state.py`
  - 有 state envelope 保存/读取能力。
- `my_agent/src/agents/coding_cli.py`
  - 支持 `--state-json`。
  - 支持 `--resume-state`。
  - 支持 approve/reject 参数。
  - 支持 verification CLI 参数。
  - pending/resume 时可写 trajectory。
- `my_agent/src/agents/approval_summaries.py`
  - 生成 shell/patch 用户可读审批摘要。
- `my_agent/src/agents/trajectory.py`
  - 支持方案 A 需要的 state/resume/approval evidence。

如果实现者选择不创建 `approval_summaries.py` 或 `coding_state.py`，必须在对应 PLAN 文件中说明等价实现位置，并保证职责没有混入过长的 `coding_cli.py`。

## 3. 行为验收场景

### 场景 1：fresh run 触发 pending approval 并保存 state

输入：

- 使用 fake model 返回一个需要 approval 的 `run_shell_command` 或 `apply_patch` tool call。
- CLI 命令包含 `--state-json .agent/run-state.json` 和 `--trajectory-jsonl .agent/run.jsonl`。

期望：

- CLI 返回 exit code 2。
- `.agent/run-state.json` 存在。
- state JSON 可被 `json.loads` 解析。
- state JSON 包含 task、workspace_root、profile/model/session/trajectory 信息。
- state JSON 包含 snapshot dict。
- state JSON 包含 pending approval summary。
- CLI 输出包含 tool name、call id、风险摘要、resume 命令提示。
- trajectory JSONL 包含 `approval_required` 和 `state_saved`。

失败判定：

- state 文件不存在。
- state 文件包含不可 JSON 序列化内容。
- pending approval 丢失 call id。
- CLI 只打印 pending 但没有恢复提示。

### 场景 2：approve 后恢复并执行工具

输入：

- 使用场景 1 生成的 state 文件。
- CLI 命令包含 `--resume-state .agent/run-state.json --approve TOOL:CALL_ID`。

期望：

- CLI 读取 state 文件并重建 coding agent setup。
- 对应 approval status 变成 approved。
- pending tool 被执行。
- 如果执行后无新的 pending approval，state 文件被删除或标记完成；项目必须选择一种明确策略。
- trajectory JSONL append，不覆盖旧事件。
- trajectory 包含 `resume_started`、`approval_decision`、`tool_result` 或等价事件。

失败判定：

- approve 后仍然因为同一个 tool call pending。
- tool call 被执行两次。
- trajectory 覆盖 fresh run 证据。
- workspace/context 丢失导致工具运行在错误目录。

### 场景 3：reject 后恢复并回灌拒绝 observation

输入：

- 使用场景 1 生成的 state 文件。
- CLI 命令包含 `--resume-state .agent/run-state.json --reject TOOL:CALL_ID --rejection-reason "not allowed"`。

期望：

- 对应 approval status 变成 rejected。
- 原工具 handler 不执行。
- run history 出现 rejected observation 或 tool result metadata。
- 模型下一轮可以看到拒绝原因。
- trajectory 包含 `approval_decision`，decision 为 rejected。

失败判定：

- reject 后工具仍执行。
- 拒绝原因没有进入模型可见 observation。
- result.pending_approvals 仍错误显示已拒绝的 call。

### 场景 4：verification 自动触发

输入：

- CLI fresh 或 resume run 配置 `--verify-command "python -m pytest"` 和 `--verify-after-tool apply_patch`。
- fake environment 返回成功或失败 verification result。

期望：

- 指定 tool 执行后自动运行 verification。
- verification result 写入 `RunItem`。
- `RunResult.verification_summary` 正确反映 attempts、passed、last command。
- CLI 输出验证结果。
- trajectory 包含 `verification_result` 或 `verification_skipped`。
- 验证失败时 observation 能进入下一轮模型上下文。

失败判定：

- verification policy 配置了但未执行。
- verification 失败被当作异常直接中断进程。
- 验证输出过长且未裁剪。

### 场景 5：审批摘要可读且不改变安全语义

输入：

- shell command approval。
- apply_patch approval。
- invalid patch approval。

期望：

- shell 摘要包含 command、cwd、reason。
- patch 摘要包含 dry_run 和 changed paths 或解析失败说明。
- invalid patch 摘要不崩溃。
- `ShellCommandPolicy` 和 `PatchApprovalPolicy` 的 allow/approve/block 判定不因摘要模块改变。

失败判定：

- 摘要模块重新实现安全判定并与 policy 不一致。
- 摘要泄露 env values。
- patch 摘要解析失败导致审批流程崩溃。

### 场景 6：trajectory 可以独立证明完整链路

输入：

- 一次 fresh pending。
- 一次 approve resume。
- 一次 verification。

期望 JSONL 事件顺序至少能证明：

1. run started。
2. model response。
3. approval required。
4. state saved。
5. resume started。
6. approval decision。
7. tool result 或 approval rejected。
8. verification result/skipped。
9. final output 或 run stopped。

失败判定：

- JSONL 任意一行不是合法 JSON。
- 无法区分 fresh 和 resume。
- 无法判断用户做了 approve 还是 reject。
- 最终事件无法判断 verification 是否通过。

## 4. 测试验收

建议执行的测试集合：

```powershell
python -m pytest tests/test_run_state.py tests/test_result.py tests/test_run_context_approvals.py -v
python -m pytest tests/test_coding_state.py tests/test_coding_cli.py -v
python -m pytest tests/test_coding_policies.py tests/test_tool_execution_plan.py tests/test_tool_approval_pause.py tests/test_tool_approval_runtime.py -v
python -m pytest tests/test_verification.py tests/test_verification_loop.py -v
python -m pytest tests/test_trajectory.py -v
```

最终合格标准：

- 上述测试全部通过。
- 若新增 public API，则 `tests/test_public_api.py` 必须通过。
- 不需要真实 `OPENAI_API_KEY`。
- 不需要执行危险 shell。
- Windows 路径下通过。

## 5. 兼容性验收

必须保持：

- 未传 `--state-json` 时，pending approval 仍返回 exit code 2，兼容旧行为。
- 未传 verification 参数时，不自动运行验证命令。
- 原有 `--trajectory-jsonl` 在普通 final run 下继续工作。
- 原有 `WorkspaceManifest.allowed_test_commands` 仍限制 test command。
- 原有 approval key 仍是 `(tool_name, call_id)`。

## 6. 阶段完成判定

只有当以下全部成立时，方案 A 才算完成：

1. PLAN01-PLAN06 每个模块的验收标准都满足。
2. 至少一个端到端测试覆盖 fresh pending -> state save -> approve resume -> tool execution -> verification -> trajectory。
3. 至少一个端到端测试覆盖 fresh pending -> state save -> reject resume -> rejected observation -> trajectory。
4. 所有新增文件和 public API 都有测试。
5. `teach/` 和 `中文方案/` 中的计划文件与实际实现没有明显偏离。
6. PLAN01-PLAN06 的最后一节“维护项目结构文档说明”课程都已完成；每节课都记录了使用的 `git diff -- <本 PLAN 涉及文件>` 或 `git diff HEAD~1..HEAD -- <本 PLAN 涉及文件>` 命令和从 diff 归纳出的关键变化，`my_agent/docs/llm` 已根据最终代码同步更新，若某个 PLAN 没有造成文档变化，课堂总结必须明确说明已经检查且无需变更。
