# 方案 A 阶段路径：可恢复的本地 Coding 执行闭环

## 1. 本阶段最终目标

本阶段把 `my_agent` 从“已有 coding profile、shell/edit 工具、approval、verification、trajectory 的 SDK 风格运行时”升级为一个可以完成本地 coding 任务闭环的 agent：

1. 模型请求危险 shell 或真实文件写入时，运行会暂停并输出可读审批信息。
2. CLI 会把暂停状态保存到磁盘，空白记忆 agent 或用户可以在之后恢复。
3. 用户可通过 CLI approve/reject 指定 pending tool call，恢复后继续执行原 run。
4. 恢复后的 shell/edit 工具会继续走原有 workspace、manifest、policy、verification、trajectory 约束。
5. 执行后能自动运行验证命令，并把验证结果回灌给模型或最终结果。
6. 每一次 fresh run、pending approval、resume、approval decision、tool result、verification、final/stopped 都能写入 trajectory JSONL，方便教学、调试和验收。
7. 每个 PLAN 上完课并完成代码后，必须同步维护 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm` 中受影响的架构文档，让后续空白记忆 agent 读取 docs/llm 时能看到最新结构、契约、流程和符号入口。

这个阶段不是大规模重写 `my_agent`。主线是补齐“CLI 可恢复执行闭环”，并在达成这个能力时顺便加固旧的 state、approval、verification 和 trajectory 契约。

阶段级文档维护规则：

- 每个 PLAN 完课后都要检查 `my_agent/docs/llm`。
- 每个 PLAN 的课程拆分必须以“维护项目结构文档说明”作为最后一节课程，而不是只把文档维护放进验收标准。
- 这节文档维护课程必须先对比“当前文件”和“git 上一个 commit 版本”之间的 diff，再根据 diff 更新 docs/llm；未提交时使用 `git diff -- <本 PLAN 涉及文件>`，已单独提交后使用 `git diff HEAD~1..HEAD -- <本 PLAN 涉及文件>`。
- 若本 PLAN 改动了 public API、状态契约、CLI 参数、运行流程、文件职责、测试入口或关键符号位置，必须同步更新对应文档。
- 至少需要检查 `ARCHITECTURE_INDEX.md`、`STATE_AND_CONTRACTS.md`、`RUNTIME_FLOWS.md`、`MODULE_CARDS.md`、`SYMBOL_MAP.md`；没有变化也要在该 PLAN 的课堂总结中说明“docs/llm 已检查，无需变更”。
- 不允许只更新代码和测试而让 docs/llm 停留在旧架构描述。

## 2. `my_agent` 当前基线

当前项目已经具备以下基础，后续 PLAN 必须复用而不是重做：

- 主运行链路：`Agent.run()` -> `Runner.run_sync()` -> `run_agent_loop()` -> `_run_agent_loop_impl()`。
- 状态对象：`RunState`、`RunStateSnapshot`、`RunResult.to_state()`、`RunState.from_snapshot()`。
- 工具系统：`FunctionTool`、`ToolRegistry`、`ToolExecutionPlan`、`execute_tool_call()`。
- 审批：`RunContextWrapper` 内部 approval map、`ToolApprovalRequest`、`pending_approvals`、`resume_pending_tool_approvals()`。
- Coding profile：`build_coding_agent()`、`CodingAgentProfile`、`WorkspaceManifest`、shell/test/edit capability。
- 策略：`ShellCommandPolicy`、`PatchApprovalPolicy`。
- 验证：`VerificationPolicy`、`VerificationRunner`、`run_verification_after_tool()`、`RunResult.verification_summary`。
- 轨迹：`TrajectoryEvent`、`trajectory_events_from_result()`、`write_trajectory_jsonl()`。

当前缺口：

- CLI 遇到 pending approval 只返回 exit code 2，并不会保存可恢复状态。
- 没有命令行 approve/reject 后继续 run 的完整用户入口。
- `RunStateSnapshot` 已能表达一部分恢复信息，但缺少面向 CLI 文件持久化的 envelope、校验和兼容策略。
- 审批摘要偏运行时内部视角，缺少面向 coding 用户的风险说明。
- verification 已存在，但 CLI 阶段还没有形成“编辑/命令后自动验证”的教学闭环。
- trajectory 已存在，但 pending/resume/approval decision 作为端到端证据还需要加强。

## 3. 参考项目使用方式

主参考：`reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`

- 参考 `RunState`、`RunContextWrapper`、`ToolExecutionPlan`、`NextStepInterruption`、approval resume 的思想。
- 参考 `RunItem` 显式区分 message、tool call、tool output、approval、trace 的运行历史模型。
- 参考 tracing/hooks 与 runtime state 分离的设计，不把观测能力当成业务正确性来源。

补充参考：`reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md`

- 参考 `DefaultAgent.save()` 的 trajectory-first 思路。
- 参考 `InteractiveAgent` 的 confirm/yolo/human 模式，但本阶段只实现 CLI approve/reject，不做复杂终端 UI。
- 参考 `Environment.execute()` 返回统一结构的思想，当前阶段只使用现有 `LocalEnvironment`。

补充参考：`reference/aider-main/PROJECT_ARCHITECTURE_ANALYSIS.md`

- 参考 lint/test 失败反馈闭环：错误文本回灌给模型，而不是只打印给人。
- 参考终端命令系统保持用户控制权的设计。

补充参考：`reference/OpenHands-main/PROJECT_ARCHITECTURE_ANALYSIS.md`

- 参考 Action/Observation/Message/State 事件化思想。
- 参考 conversation 启动和 pending message 的状态化边界，但本阶段只做本地文件 state，不做 server。

## 4. PLAN 依赖关系

执行顺序必须如下：

1. `PLAN01.md`：运行状态快照与审批恢复契约加固。
2. `PLAN02.md`：CLI pending approval 状态持久化。
3. `PLAN03.md`：CLI approve/reject 恢复执行。
4. `PLAN04.md`：审批风险摘要与用户可读输出。
5. `PLAN05.md`：验证策略 CLI 化与执行闭环。
6. `PLAN06.md`：trajectory 证据链贯通。

依赖理由：

- PLAN02 需要 PLAN01 提供可靠的 snapshot dict/envelope 能力。
- PLAN03 需要 PLAN02 保存的 state 文件恢复原始 run。
- PLAN04 可以在 PLAN03 前后实现，但放在 PLAN03 后可以基于真实 CLI 恢复场景优化显示。
- PLAN05 需要 approve/resume 后能继续运行，才能体现验证闭环。
- PLAN06 汇总前面所有 runtime evidence，必须最后做。

## 5. 每个 PLAN 承担的职责

### PLAN01：运行状态快照与审批恢复契约加固

职责：让 `RunResult.to_state()` 输出的 `RunStateSnapshot` 适合被 CLI 保存和加载。这个 PLAN 不做 CLI，只做 state 层稳定性。

完成后为后续提供的前置接口：

- `run_state_snapshot_to_dict(snapshot: RunStateSnapshot) -> dict[str, Any]`
- `run_state_snapshot_from_dict(data: Mapping[str, Any]) -> RunStateSnapshot`
- `RunStateSnapshot` 中 pending approvals、model response states、new_items、tool approval map 可 JSON 往返。

### PLAN02：CLI pending approval 状态持久化

职责：在 fresh coding CLI run 暂停时，将 run state、CLI 配置、workspace manifest metadata、pending approval summary 保存到 state JSON 文件。

完成后为后续提供的前置接口：

- `CodingRunStateStore.save_pending_result(...)`
- `CodingRunStateStore.load_envelope(...)`
- CLI 新参数：`--state-json PATH`

### PLAN03：CLI approve/reject 恢复执行

职责：读取 PLAN02 保存的 state JSON，重建 agent/setup，应用 approve/reject 决策，并调用 `resume_agent_loop()` 继续运行。

完成后能力：

- `python -m agents.coding_cli --resume-state .agent/run-state.json --approve run_shell_command:<call_id>`
- `python -m agents.coding_cli --resume-state .agent/run-state.json --reject apply_patch:<call_id> --rejection-reason "..."`

### PLAN04：审批风险摘要与用户可读输出

职责：让审批请求从“内部工具调用信息”升级为“coding 用户能判断风险的摘要”。此 PLAN 不改变审批语义，只增强解释和 CLI 展示。

完成后能力：

- shell 审批显示 command、cwd、policy action、reason、blocked/approval fragments。
- patch 审批显示 dry_run、changed paths、add/update/delete 估计、风险类别。

### PLAN05：验证策略 CLI 化与执行闭环

职责：让 coding CLI 能配置 verification policy，并在 shell/edit 后自动运行验证命令。验证失败应成为 run evidence，并可回灌模型继续修复。

完成后能力：

- CLI 支持 `--verify-command`、`--verify-after-tool`、`--verify-max-attempts`、`--verify-output-chars`。
- 运行结果包含 verification summary，失败时 exit code 和输出语义清晰。

### PLAN06：trajectory 证据链贯通

职责：把 fresh run、pending approval、resume、approval decision、tool execution、verification、final/stopped 统一记录到 trajectory JSONL。

完成后能力：

- pending 时写 trajectory。
- resume 时 append 同一 trajectory。
- approval decision、state file path、verification summary 出现在 JSONL 事件中。

## 6. 阶段外边界

本阶段不做：

- Docker/remote sandbox。
- RepoMap/代码索引。
- Planner/Editor 多 agent。
- MCP 插件。
- UI/WebSocket。
- 大规模重构 `run_loop.py`。

这些能力应在本阶段 CLI 可恢复执行闭环稳定后再做。
