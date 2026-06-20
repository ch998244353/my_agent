# PLAN04：审批风险摘要与用户可读输出

## 1. 模块主要实现内容

本模块把 pending approval 输出从“内部工具调用参数”升级为“coding 用户能判断风险的审批摘要”。它不改变 approval 的执行语义，只增强 `ToolApprovalRequest.reason`、`RunResult.pending_approval_summaries`、CLI 展示和 state 文件中的摘要。

本模块完成后，用户看到的不只是 `run_shell_command call_x requires approval`，而是能看到命令、cwd、风险分类、触发策略、patch 影响文件等信息。

## 2. 参考文件与参考能力

主参考：

- `reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `FunctionTool.needs_approval` 支持 bool/callable。
  - tool guardrails 和 approval 应用于 shell、patch、secret access 等高风险动作。

补充参考：

- `reference/aider-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `Coder.handle_shell_commands()` 使用明确用户确认。
  - `/run`、`/test` 等命令让用户保持控制。

当前 `my_agent` 相关文件：

- `my_agent/src/agents/coding_policies.py`
- `my_agent/src/agents/tool_planning.py`
- `my_agent/src/agents/tool_execution.py`
- `my_agent/src/agents/result.py`
- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_policies.py`
- `my_agent/tests/test_tool_execution_plan.py`
- `my_agent/tests/test_coding_cli.py`

## 3. 计划新增内容

建议新增轻量 helper 文件：

- `my_agent/src/agents/approval_summaries.py`
- `my_agent/tests/test_approval_summaries.py`

建议接口：

```python
def approval_summary_for_tool_call(tool_name: str, arguments: Mapping[str, Any], reason: str) -> str: ...
```

不要把这个模块做成策略引擎。策略仍然属于 `ShellCommandPolicy` 和 `PatchApprovalPolicy`。

## 4. 课程拆分

### 课程 1：shell approval 摘要

优化目标：为 shell command 审批生成稳定文本摘要。

旧代码缺陷：用户难以从通用 reason 判断命令风险。

新增能力：展示 command、cwd、policy reason、风险提示。

创建文件：

- `my_agent/src/agents/approval_summaries.py`
- `my_agent/tests/test_approval_summaries.py`

实现方案：

- 对 `run_shell_command` 和 `run_test_command` 识别 `command`、`cwd`。
- 输出固定字段：`tool`、`call_id`、`command`、`cwd`、`risk`、`reason`。
- 不重新分类命令，只使用已有 reason 和 arguments。

执行标准：

- 新增代码不超过 80 行。
- 输出不要包含 env values。

### 课程 2：patch approval 摘要

优化目标：让用户知道 patch 将影响哪些路径以及是否真实写入。

旧代码缺陷：patch 审批信息没有足够强的 changed path / dry-run / operation 提示。

新增能力：展示 patch_text 简要信息、dry_run、可能涉及文件。

修改文件：

- `my_agent/src/agents/approval_summaries.py`
- `my_agent/tests/test_approval_summaries.py`

实现方案：

- 对 `apply_patch` arguments 读取 `patch` 或 `patch_text`、`dry_run`。
- 可以复用 `patches.parse_patch()` 解析 changed files；解析失败时显示“patch parse failed before approval summary”，但不阻止原审批流程。
- 摘要只用于展示，不用于安全判定。

执行标准：

- 新增代码不超过 80 行。
- valid add/update/delete patch 能列出目标路径。
- invalid patch 不导致 CLI 崩溃。

### 课程 3：接入 pending_approval_summaries

优化目标：让 `RunResult.pending_approval_summaries` 使用更清晰的摘要。

旧代码缺陷：CLI 与其他调用方只得到泛化 summary。

新增能力：所有用户入口共享审批摘要。

修改文件：

- `my_agent/src/agents/result.py`
- `my_agent/tests/test_result.py`

实现方案：

- 在 `pending_approval_summaries` 内调用 summary helper。
- 保留旧格式中 tool_name 和 call_id，避免破坏测试和用户脚本。
- 输出多行文本可以接受，但必须稳定。

执行标准：

- 新增代码不超过 80 行。
- pending approval summary 包含 tool、call_id、risk/command/path 信息。

### 课程 4：CLI 展示与 state 文件摘要同步

优化目标：CLI 打印和 PLAN02 state 文件使用同一套摘要。

旧代码缺陷：不同地方可能生成不一致描述。

新增能力：state 文件里的 `pending_approvals` 与 CLI 打印一致。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/src/agents/coding_state.py`
- `my_agent/tests/test_coding_cli.py`
- `my_agent/tests/test_coding_state.py`

实现方案：

- 让 `CodingRunStateStore.save_pending_result()` 存储 `result.pending_approval_summaries`。
- `_print_result()` 逐条打印摘要。
- resume 时如果用户未提供 decision，也打印这些摘要。

执行标准：

- 新增代码不超过 80 行。
- CLI pending 输出足够让用户判断是否 approve。

### 课程 5：维护项目结构文档说明

优化目标：先对比本 PLAN 完课后的当前文件与 git 上一个 commit 版本之间的 diff，再根据真实代码变化把审批风险摘要能力同步写入 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm`，让后续 PLAN05/PLAN06 能复用同一 pending approval 展示语义。

旧代码缺陷：如果文档只记录 approval pause/resume，不记录摘要生成位置和安全边界，后续课程可能在 CLI、state、trajectory 中各写一套不同摘要。

新增能力：项目结构文档能说明 `approval_summaries.py` 或等价实现位置、shell/patch summary 字段、invalid patch 行为、CLI 输出和 state pending summary 的一致性要求。

修改文件：

- `my_agent/docs/llm/STATE_AND_CONTRACTS.md`
- `my_agent/docs/llm/RUNTIME_FLOWS.md`
- `my_agent/docs/llm/MODULE_CARDS.md`
- `my_agent/docs/llm/SYMBOL_MAP.md`
- `my_agent/docs/llm/ARCHITECTURE_INDEX.md`
- `my_agent/docs/llm/MAINTENANCE_LOG.md`

实现方案：

- 先列出本 PLAN 实际改动的代码和测试文件，至少包括 `my_agent/src/agents/approval_summaries.py`、`my_agent/src/agents/coding_cli.py`、`my_agent/src/agents/coding_state.py` 以及相关测试文件中确实发生变化的文件。
- 如果本 PLAN 的代码尚未提交，使用 `git diff -- my_agent/src/agents/approval_summaries.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/tests/test_coding_cli.py my_agent/tests/test_coding_state.py` 查看当前文件相对 `HEAD` 的 diff。
- 如果本 PLAN 已经单独提交，使用 `git diff HEAD~1..HEAD -- my_agent/src/agents/approval_summaries.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/tests/test_coding_cli.py my_agent/tests/test_coding_state.py` 查看本 PLAN 相对上一个 commit 的 diff。
- 从 diff 中提取新增/改名/删除的摘要接口、CLI 输出字段、state pending summary 字段、测试入口和安全边界，禁止只凭记忆更新文档。
- 在 `STATE_AND_CONTRACTS.md` 记录 pending approval summary 的字段来源，以及不泄露敏感 env value 的边界。
- 在 `RUNTIME_FLOWS.md` 更新 pending approval 输出流程，说明 CLI 输出和 state 文件使用同一 summary。
- 在 `MODULE_CARDS.md` 增加 `approval_summaries.py` 职责，或说明等价实现文件。
- 在 `SYMBOL_MAP.md` 增加 `approval_summary_for_tool_call` 等摘要入口。
- 在 `ARCHITECTURE_INDEX.md` 标记审批摘要是展示层增强，不改变 policy 判定。
- 在 `MAINTENANCE_LOG.md` 记录本 PLAN 完课后 docs/llm 已同步，以及 shell/patch/invalid patch 的测试入口。

执行标准：

- 课堂总结必须记录使用的 diff 命令，以及从 diff 归纳出的关键代码变化清单。
- 文档必须明确本 PLAN 不改变 shell/patch 是否需要审批的安全语义。
- 文档必须说明 invalid patch summary 不应导致 pending 保存失败。
- 如果某个 docs/llm 文件检查后无需修改，课堂总结必须说明原因。

## 5. 本 PLAN 不做的事情

- 不改变 shell/patch 是否需要审批的判定。
- 不新增交互式确认 UI。
- 不执行 patch dry-run；只是展示已有 arguments 和可安全解析的信息。
- 不保存敏感 env value。

## 6. 验收标准

完成 PLAN04 后必须满足：

1. shell approval summary 包含 command 和 cwd。
2. patch approval summary 包含 dry_run 和 changed paths 或解析失败说明。
3. CLI pending 输出与 state 文件 pending summary 一致。
4. approve/reject 恢复流程不受摘要增强影响。
5. 新增测试覆盖 shell、patch、invalid patch、CLI 打印。
6. 已完成“课程 5：维护项目结构文档说明”，`my_agent/docs/llm` 与本 PLAN 的最终代码、测试和审批摘要边界一致。
