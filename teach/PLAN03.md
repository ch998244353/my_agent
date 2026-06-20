# PLAN03：CLI approve/reject 恢复执行

## 1. 模块主要实现内容

本模块实现从 PLAN02 state 文件恢复执行。用户可以对 pending tool call 做 approve 或 reject，然后 CLI 重建 coding agent setup，恢复 `RunState`，调用 `resume_agent_loop()` 继续执行。

本模块是方案 A 的核心能力：让 coding agent 从“暂停后结束”变成“暂停后可恢复”。

## 2. 参考文件与参考能力

主参考：

- `reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `NextStepInterruption`：approval 中断后返回 result，用户决策后恢复。
  - `RunState`：保存 generated items、approvals、trace、sandbox resume state。
  - `RunContextWrapper.approve_tool()` / `reject_tool()` 思路。

补充参考：

- `reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `InteractiveAgent` 的 confirm/human 模式，体现人在回路。

当前 `my_agent` 相关文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/src/agents/coding_state.py`
- `my_agent/src/agents/run_loop.py`
- `my_agent/src/agents/run_resume.py`
- `my_agent/src/agents/run_context.py`
- `my_agent/tests/test_coding_cli.py`
- `my_agent/tests/test_tool_approval_pause.py`

前置 PLAN01/PLAN02 提供：

```python
run_state_snapshot_from_dict(...)
CodingRunStateStore.load_envelope(...)
```

## 3. 计划新增内容

CLI 新增参数建议：

```text
--resume-state PATH
--approve TOOL_NAME:CALL_ID
--reject TOOL_NAME:CALL_ID
--rejection-reason TEXT
--approve-all
```

优先实现单个 approve/reject，再实现 `--approve-all`。不要支持复杂交互式 UI。

## 4. 课程拆分

### 课程 1：解析 approval decision 参数

优化目标：将 CLI 字符串解析为明确的 approval decision。

旧代码缺陷：CLI 没有 approve/reject 输入入口。

新增能力：`run_shell_command:call_123` 可被解析为 `(tool_name, call_id)`。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 增加小 helper：`_parse_approval_ref(value: str) -> tuple[str, str]`。
- 增加 config 字段：`resume_state_json`、`approval_decisions` 或等效结构。
- reject 必须允许 `--rejection-reason`，未提供时使用默认原因。

执行标准：

- 新增代码不超过 80 行。
- 错误格式要给 argparse 级别的清晰错误。

### 课程 2：从 state 文件重建 coding setup

优化目标：根据 state envelope 重建 `CodingAgentSetup`。

旧代码缺陷：state 文件保存后，CLI 不知道应该用哪个 workspace/profile/model/session 继续。

新增能力：resume path 可复用 `build_coding_cli_setup()` 或新增专用 builder。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/src/agents/coding_state.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 读取 envelope 中的 task、workspace_root、profile_name、model、session path、trajectory path。
- 构造新的 `CodingCliConfig` 或 `CodingAgentSetup`。
- 不从 state 文件恢复 `Environment` 对象，只按 manifest/workspace 重新构造。

执行标准：

- 新增代码不超过 80 行。
- 如果 workspace 不存在或不匹配，报清晰错误，不继续执行。

### 课程 3：应用 approve/reject 并恢复 RunState

优化目标：把用户决策写入恢复后的 `RunContextWrapper`。

旧代码缺陷：runtime 内部有 approval map，但 CLI 没有外部入口设置它。

新增能力：恢复后可以批准或拒绝具体 pending call。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_tool_approval_pause.py`

实现方案：

- `RunState.from_snapshot(snapshot, context_wrapper=setup.run_config.context_wrapper 或等效构造)` 按现有实际 API 适配。
- 对每个 approve 调用 `run_state.context_wrapper.approve_tool_call(tool_name, call_id)`。
- 对每个 reject 调用 `reject_tool_call(...)` 并携带 reason。
- 若用户没有对任何 pending call 做决策，CLI 应打印 pending 列表并返回 2。

执行标准：

- 新增代码不超过 80 行。
- approve 后 pending tool 会执行。
- reject 后工具不会执行，并产生 rejected observation。

### 课程 4：调用 resume_agent_loop 并处理 state 文件生命周期

优化目标：恢复执行后正确输出、保存 trajectory、更新或清理 state 文件。

旧代码缺陷：无恢复运行入口。

新增能力：resume 完成后可以删除或重写 state 文件。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 调用 `resume_agent_loop(setup.agent, run_state, setup.run_config)`。
- 如果恢复后仍有 pending approvals，重写 state 文件。
- 如果恢复后 final output 或 run stopped 且无 pending，删除 state 文件或写入 completed marker。建议删除，并在 trajectory 中保留证据。

执行标准：

- 新增代码不超过 80 行。
- approve-resume 之后 exit code 语义与 fresh run 一致。
- reject-resume 后如果模型继续请求新工具，仍可再次 pending。

### 课程 5：端到端 CLI 恢复测试

优化目标：证明两个进程式调用可以串起来。

旧代码缺陷：单元测试只覆盖内部 resume，没有覆盖 CLI state 文件。

新增能力：CLI 级别端到端恢复。

修改文件：

- `my_agent/tests/test_coding_cli.py`

实现方案：

- fake model 第一次返回需要 approval 的 shell call。
- 第一次 CLI run 生成 state 文件并返回 2。
- 第二次 CLI resume approve，确认 shell handler 被调用。
- reject 分支确认 shell handler 不被调用。

执行标准：

- 不调用真实 OpenAI。
- 不执行真实危险命令。
- Windows 路径可通过。

### 课程 6：维护项目结构文档说明

优化目标：先对比本 PLAN 完课后的当前文件与 git 上一个 commit 版本之间的 diff，再根据真实代码变化把 approve/reject resume 能力同步写入 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm`，让后续 PLAN04/PLAN05 能基于稳定的恢复流程继续扩展。

旧代码缺陷：如果文档只记录 state 文件保存，不记录 resume 参数、approval decision 应用位置和 state 生命周期，后续课程容易重复设计恢复入口或误改 `RunState`。

新增能力：项目结构文档能说明 `--resume-state`、`--approve`、`--reject`、`--approve-all`、`resume_agent_loop()` 接入点、approve/reject 对 runtime context 的影响和测试入口。

修改文件：

- `my_agent/docs/llm/STATE_AND_CONTRACTS.md`
- `my_agent/docs/llm/RUNTIME_FLOWS.md`
- `my_agent/docs/llm/MODULE_CARDS.md`
- `my_agent/docs/llm/SYMBOL_MAP.md`
- `my_agent/docs/llm/ARCHITECTURE_INDEX.md`
- `my_agent/docs/llm/MAINTENANCE_LOG.md`

实现方案：

- 先列出本 PLAN 实际改动的代码和测试文件，至少包括 `my_agent/src/agents/coding_cli.py`、`my_agent/src/agents/coding_state.py`、`my_agent/src/agents/run_context.py`、`my_agent/src/agents/run_loop.py` 以及相关测试文件中确实发生变化的文件。
- 如果本 PLAN 的代码尚未提交，使用 `git diff -- my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/run_context.py my_agent/src/agents/run_loop.py my_agent/tests/test_coding_cli.py` 查看当前文件相对 `HEAD` 的 diff。
- 如果本 PLAN 已经单独提交，使用 `git diff HEAD~1..HEAD -- my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/run_context.py my_agent/src/agents/run_loop.py my_agent/tests/test_coding_cli.py` 查看本 PLAN 相对上一个 commit 的 diff。
- 从 diff 中提取新增/改名/删除的 resume 参数、approval decision 结构、state 生命周期策略、测试入口和行为边界，禁止只凭记忆更新文档。
- 在 `RUNTIME_FLOWS.md` 增加 fresh pending -> state save -> approve/reject resume -> resumed run 的完整流程。
- 在 `STATE_AND_CONTRACTS.md` 补充 approval decision 如何按 `(tool_name, call_id)` 应用，以及 reject reason 如何进入 observation。
- 在 `MODULE_CARDS.md` 更新 `coding_cli.py`、`coding_state.py`、`run_context.py`、`run_loop.py` 的协作关系。
- 在 `SYMBOL_MAP.md` 增加 resume 参数解析 helper、decision 数据结构或等价符号入口。
- 在 `ARCHITECTURE_INDEX.md` 标记 CLI 已具备可恢复 approval 执行闭环，但审批摘要增强仍由 PLAN04 完成。
- 在 `MAINTENANCE_LOG.md` 记录本 PLAN 完课后 docs/llm 已同步，以及 approve/reject 两条测试命令。

执行标准：

- 课堂总结必须记录使用的 diff 命令，以及从 diff 归纳出的关键代码变化清单。
- 文档必须区分 approve 执行工具和 reject 不执行工具的行为。
- 文档必须明确 state 文件完成后的生命周期策略。
- 如果某个 docs/llm 文件检查后无需修改，课堂总结必须说明原因。

## 5. 本 PLAN 不做的事情

- 不新增更复杂的风险摘要；PLAN04 处理。
- 不新增 verification CLI 参数；PLAN05 处理。
- 不保证 Docker/remote sandbox。
- 不改变 `RunState` dataclass 基本结构，除非 PLAN01 已要求。

## 6. 验收标准

完成 PLAN03 后必须满足：

1. CLI 可以从 PLAN02 state 文件恢复。
2. 用户可以 approve 指定 pending tool call。
3. 用户可以 reject 指定 pending tool call 并给出原因。
4. 恢复后继续走原有 run loop、tool execution、memory、approval、session 语义。
5. 恢复完成后 state 文件生命周期明确。
6. `tests/test_coding_cli.py` 覆盖 approve 和 reject 两条路径。
7. 已完成“课程 6：维护项目结构文档说明”，`my_agent/docs/llm` 与本 PLAN 的最终代码、测试和恢复执行流程一致。
