# PLAN06：trajectory 证据链贯通

## 1. 模块主要实现内容

本模块把方案 A 的所有关键事件写入 trajectory JSONL，形成可教学、可调试、可验收的证据链。当前 `my_agent` 已有 `TrajectoryEvent`、`trajectory_events_from_result()`、`write_trajectory_jsonl()` 和 CLI `--trajectory-jsonl`，但 pending/resume/approval decision/state file/verification summary 作为端到端证据还不完整。

本模块完成后，用户可以通过一个 JSONL 文件看到：

- fresh run 开始。
- model response。
- approval required。
- state file saved。
- resume started。
- approval approved/rejected。
- tool executed 或 rejected。
- verification result/skipped。
- final output 或 stopped reason。

## 2. 参考文件与参考能力

主参考：

- `reference/OpenHands-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - Action、Observation、Message、State 事件统一持久化。
  - 事件是 UI、调试、审计、恢复体验的核心。

补充参考：

- `reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `DefaultAgent.save()` 每轮保存 trajectory。
  - trajectory 先于复杂 UI 实现。

当前 `my_agent` 相关文件：

- `my_agent/src/agents/trajectory.py`
- `my_agent/src/agents/coding_cli.py`
- `my_agent/src/agents/coding_state.py`
- `my_agent/src/agents/run_resume.py`
- `my_agent/src/agents/result.py`
- `my_agent/tests/test_trajectory.py`
- `my_agent/tests/test_coding_cli.py`

## 3. 计划新增内容

建议新增 trajectory event type：

```text
state_saved
resume_started
approval_decision
```

如果不想新增 event type，也可以先使用现有 `runtime_item`，但本阶段为了教学清晰，推荐显式事件类型，并同步更新 `RunItem.item_type` 相关映射或仅在 trajectory 层生成 CLI events。

优先选择“trajectory 层 CLI events”，避免为纯 CLI evidence 修改核心 `RunItem`。

## 4. 课程拆分

### 课程 1：state_saved 事件

优化目标：pending approval 写 state 文件时，trajectory 记录 state 文件路径和 pending 数量。

旧代码缺陷：用户只能看到 state 文件存在，trajectory 无法解释为什么暂停。

新增能力：JSONL 中出现 state saved evidence。

修改文件：

- `my_agent/src/agents/trajectory.py`
- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_trajectory.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 新增 helper：`state_saved_event(run_id, state_path, pending_count)`。
- CLI pending 保存 state 后，将该事件 append 到 trajectory。
- 不把完整 snapshot 写入 trajectory，只写路径和摘要，避免重复和敏感信息。

执行标准：

- 新增代码不超过 80 行。
- trajectory JSONL 有 `event_type == "state_saved"`。

### 课程 2：resume_started 事件

优化目标：恢复执行时 trajectory 可区分 fresh run 和 resume run。

旧代码缺陷：append 到同一 JSONL 时，后续事件缺少恢复上下文。

新增能力：恢复开始事件包含 state path、decision count。

修改文件：

- `my_agent/src/agents/trajectory.py`
- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_trajectory.py`

实现方案：

- 新增 helper：`resume_started_event(run_id, state_path, approvals, rejections)`。
- CLI 读取 state 并应用 decision 前写该事件。
- trajectory append 模式必须开启，避免覆盖 fresh run 证据。

执行标准：

- 新增代码不超过 80 行。
- resume 后 JSONL 保留 fresh run 事件，并追加 resume 事件。

### 课程 3：approval_decision 事件

优化目标：用户 approve/reject 的决策进入证据链。

旧代码缺陷：rejected approval 可从 tool_result metadata 推断，但 approved 决策缺少显式 CLI 证据。

新增能力：JSONL 明确记录 approved/rejected、tool_name、call_id、reason。

修改文件：

- `my_agent/src/agents/trajectory.py`
- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_trajectory.py`

实现方案：

- 新增 helper：`approval_decision_event(run_id, tool_name, call_id, decision, reason)`。
- approve reason 可以为空字符串或 `"approved by user"`。
- reject reason 使用 CLI `--rejection-reason`。

执行标准：

- 新增代码不超过 80 行。
- approve 和 reject 都有单测。

### 课程 4：verification summary 进入最终 trajectory

优化目标：最终 JSONL 可以直接判断验证是否通过。

旧代码缺陷：trajectory 有 verification_result，但最终 summary 不一定明显。

新增能力：final_output/run_stopped payload 包含 verification summary 或最后验证状态。

修改文件：

- `my_agent/src/agents/trajectory.py`
- `my_agent/tests/test_trajectory.py`

实现方案：

- 在 `_result_summary_payload` 或 final/stopped event payload 中加入 `verification_summary`。
- 只放 attempts/skips/passed/last_status/last_command，不放完整长输出。

执行标准：

- 新增代码不超过 80 行。
- 验证失败和通过都可从 JSONL 最后一条事件判断。

### 课程 5：端到端 JSONL 场景测试

优化目标：证明方案 A 所有关键事件能串在一个 trajectory 中。

旧代码缺陷：单点 trajectory 测试不能证明 fresh -> pending -> resume -> verification 的完整链路。

新增能力：端到端证据链测试。

修改文件：

- `my_agent/tests/test_coding_cli.py`
- `my_agent/tests/test_trajectory.py`

实现方案：

- fake model 第一次触发 approval。
- fresh CLI 写 `approval_required` 和 `state_saved`。
- resume approve 写 `resume_started`、`approval_decision`、`tool_result`、`verification_result`、final/stopped。
- 读取 JSONL，按顺序断言事件类型。

执行标准：

- 不调用真实 OpenAI。
- 不执行危险命令。
- JSONL 每行都是合法 JSON。

### 课程 6：维护项目结构文档说明

优化目标：先对比本 PLAN 完课后的当前文件与 git 上一个 commit 版本之间的 diff，再根据真实代码变化把 trajectory 证据链能力同步写入 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm`，让方案 A 的最终结构说明与实际可验收行为一致。

旧代码缺陷：如果文档只记录旧 trajectory final run 能力，不记录 pending/resume/approval/verification 事件链，后续维护者无法用 docs/llm 判断 JSONL 是否足够证明完整执行闭环。

新增能力：项目结构文档能说明 `state_saved`、`resume_started`、`approval_decision`、verification summary 等事件，fresh/resume append 语义，trajectory 不是恢复状态来源的边界，以及总体验收测试入口。

修改文件：

- `my_agent/docs/llm/STATE_AND_CONTRACTS.md`
- `my_agent/docs/llm/RUNTIME_FLOWS.md`
- `my_agent/docs/llm/MODULE_CARDS.md`
- `my_agent/docs/llm/SYMBOL_MAP.md`
- `my_agent/docs/llm/ARCHITECTURE_INDEX.md`
- `my_agent/docs/llm/MAINTENANCE_LOG.md`

实现方案：

- 先列出本 PLAN 实际改动的代码和测试文件，至少包括 `my_agent/src/agents/trajectory.py`、`my_agent/src/agents/coding_cli.py`、`my_agent/src/agents/coding_state.py`、`my_agent/src/agents/verification.py` 以及相关测试文件中确实发生变化的文件。
- 如果本 PLAN 的代码尚未提交，使用 `git diff -- my_agent/src/agents/trajectory.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/verification.py my_agent/tests/test_trajectory.py my_agent/tests/test_coding_cli.py` 查看当前文件相对 `HEAD` 的 diff。
- 如果本 PLAN 已经单独提交，使用 `git diff HEAD~1..HEAD -- my_agent/src/agents/trajectory.py my_agent/src/agents/coding_cli.py my_agent/src/agents/coding_state.py my_agent/src/agents/verification.py my_agent/tests/test_trajectory.py my_agent/tests/test_coding_cli.py` 查看本 PLAN 相对上一个 commit 的 diff。
- 从 diff 中提取新增/改名/删除的 trajectory event 类型、JSONL 字段、append 语义、verification summary 写入点、测试入口和行为边界，禁止只凭记忆更新文档。
- 在 `RUNTIME_FLOWS.md` 写清 fresh pending -> state_saved -> resume_started -> approval_decision -> tool/reject -> verification -> final/stopped 的 JSONL 证据流。
- 在 `STATE_AND_CONTRACTS.md` 记录 trajectory event 字段边界，并强调 state JSON 才是恢复来源。
- 在 `MODULE_CARDS.md` 更新 `trajectory.py`、`coding_cli.py` 与 verification/state 模块的协作关系。
- 在 `SYMBOL_MAP.md` 增加新增事件构造 helper 或等价符号入口。
- 在 `ARCHITECTURE_INDEX.md` 标记方案 A 完整闭环已由 trajectory 支持验收。
- 在 `MAINTENANCE_LOG.md` 记录本 PLAN 完课后 docs/llm 已同步，以及端到端 JSONL 测试命令。

执行标准：

- 课堂总结必须记录使用的 diff 命令，以及从 diff 归纳出的关键代码变化清单。
- 文档中的事件顺序必须能支持 `verification.md` 的最终验收。
- 文档必须明确 trajectory 只做证据链，不作为 resume state 的读取来源。
- 如果某个 docs/llm 文件检查后无需修改，课堂总结必须说明原因。

## 5. 本 PLAN 不做的事情

- 不做 Web UI。
- 不做 OpenTelemetry。
- 不做远程事件服务。
- 不把 trajectory 当作恢复状态来源；恢复状态来源仍是 PLAN02 state JSON。

## 6. 验收标准

完成 PLAN06 后必须满足：

1. pending approval 时 trajectory 包含 approval_required 和 state_saved。
2. resume 时 trajectory append，不覆盖旧事件。
3. approve/reject 决策有显式 approval_decision event。
4. verification summary 能从最终 JSONL 判断。
5. JSONL 可作为方案 A 总体验收证据。
6. `tests/test_trajectory.py` 和 `tests/test_coding_cli.py` 覆盖完整链路。
7. 已完成“课程 6：维护项目结构文档说明”，`my_agent/docs/llm` 与本 PLAN 的最终代码、测试和 trajectory 证据链一致。
