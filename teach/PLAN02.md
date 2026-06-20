# PLAN02：CLI pending approval 状态持久化

## 1. 模块主要实现内容

本模块让 `agents.coding_cli` 在遇到 pending approval 时保存可恢复 state 文件。当前 CLI 能打印 pending approval 并返回 exit code 2，但下一次进程启动无法继续原 run。本模块只负责“保存”，不负责“approve/reject 后恢复执行”。

完成后，用户运行 coding CLI 时可以指定：

```powershell
python -m agents.coding_cli --workspace . --profile edit-local --task "..." --state-json .agent/run-state.json
```

如果 run 暂停审批，CLI 会把 state 文件写好，并打印下一步 resume 命令提示。

## 2. 参考文件与参考能力

主参考：

- `reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `RunState` interruption 后可 `to_state()`，再 approve/reject 恢复。
  - `RunContextWrapper` 不把 runtime-only context 传给 LLM，也不应直接序列化到状态文件。

补充参考：

- `reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `DefaultAgent.save()` 每轮保存 trajectory，强调可复盘。

当前 `my_agent` 相关文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/src/agents/result.py`
- `my_agent/src/agents/run_state.py`
- `my_agent/src/agents/workspace_manifest.py`
- `my_agent/tests/test_coding_cli.py`

前置 PLAN01 提供：

```python
run_state_snapshot_to_dict(snapshot: RunStateSnapshot) -> dict[str, Any]
run_state_snapshot_from_dict(data: Mapping[str, Any]) -> RunStateSnapshot
```

## 3. 计划新增内容

建议新增文件：

- `my_agent/src/agents/coding_state.py`
- `my_agent/tests/test_coding_state.py`

建议新增数据结构：

```python
@dataclass(frozen=True)
class CodingRunStateEnvelope:
    version: int
    task: str
    workspace_root: str
    profile_name: str
    model: str
    session_json: str | None
    trajectory_jsonl: str | None
    state: dict[str, Any]
    pending_approvals: tuple[str, ...]
```

可以用 dict writer 实现，不强制 dataclass，但文件结构必须稳定。

## 4. 课程拆分

### 课程 1：增加 CLI state 参数

优化目标：让 CLI 接收 `--state-json PATH`。

旧代码缺陷：`CodingCliConfig` 没有 state 文件路径，pending approval 时无法保存恢复点。

新增能力：CLI 配置携带 state path。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 在 `CodingCliConfig` 增加 `state_json: Path | None`。
- `_build_parser()` 增加 `--state-json`。
- `parse_coding_cli_args()` 解析为 `Path`。

执行标准：

- 新增代码不超过 80 行。
- 不改变未传 `--state-json` 时的现有行为。

### 课程 2：实现 CodingRunStateStore

优化目标：把保存 state 文件的逻辑从 CLI 中拆出去，避免 `coding_cli.py` 继续变大。

旧代码缺陷：CLI 当前只负责打印和 trajectory，没有持久化 run state 的独立边界。

新增能力：状态文件写入和读取有单独模块。

创建文件：

- `my_agent/src/agents/coding_state.py`
- `my_agent/tests/test_coding_state.py`

实现方案：

- `CodingRunStateStore(path)` 负责 `save_pending_result(result, config, setup)`。
- 文件内容包含 version、task、workspace_root、profile、model、session path、trajectory path、snapshot dict、pending approval summaries。
- 使用 PLAN01 的 snapshot dict helper。

执行标准：

- 新增代码不超过 80 行。
- 写入 JSON 时创建父目录。
- 不保存 API key、env value、`RunConfig.context` 原始对象。

### 课程 3：pending approval 时自动保存

优化目标：当 `RunResult.pending_approvals` 非空时，CLI 保存 state。

旧代码缺陷：CLI 返回 exit code 2 后，用户只能重新开始任务。

新增能力：pending 状态落盘。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 在 `run_coding_agent_cli()` 得到 result 后，若 pending 且 `config.state_json` 存在，调用 `CodingRunStateStore.save_pending_result(...)`。
- `_print_result()` 或新 helper 打印 state 文件路径和建议 resume 命令。
- 如果 pending 但未传 `--state-json`，保持旧行为，但提示“未保存恢复状态”。

执行标准：

- 新增代码不超过 80 行。
- pending approval 时 state 文件存在且 JSON 可解析。
- 无 pending approval 时不写 state 文件。

### 课程 4：状态文件内容回归测试

优化目标：证明 state 文件包含 PLAN03 恢复所需的全部信息。

旧代码缺陷：没有任何测试能证明 CLI pending 后能被另一个进程理解。

新增能力：state envelope 契约测试。

修改文件：

- `my_agent/tests/test_coding_state.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 用 fake model 触发需要 approval 的 tool call。
- 运行 CLI helper，不调用真实 OpenAI。
- 断言 state JSON 包含 task、workspace_root、profile_name、state、pending_approvals。

执行标准：

- 测试不依赖网络。
- 测试不执行危险 shell。
- 原 CLI exit code 语义保持：pending approval 仍为 2。

### 课程 5：维护项目结构文档说明

优化目标：先对比本 PLAN 完课后的当前文件与 git 上一个 commit 版本之间的 diff，再根据真实代码变化把 CLI pending state 保存能力同步写入 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm`，让后续 PLAN03 的实现者能直接从文档理解 state 文件职责和边界。

旧代码缺陷：如果只新增 `coding_state.py` 和 `--state-json`，但文档仍停留在“pending approval 只返回 exit code 2”，后续恢复执行课程会缺少可靠入口说明。

新增能力：项目结构文档能说明 `CodingRunStateStore`、state envelope、`--state-json`、pending approval 保存流程、敏感信息边界和测试入口。

修改文件：

- `my_agent/docs/llm/STATE_AND_CONTRACTS.md`
- `my_agent/docs/llm/RUNTIME_FLOWS.md`
- `my_agent/docs/llm/MODULE_CARDS.md`
- `my_agent/docs/llm/SYMBOL_MAP.md`
- `my_agent/docs/llm/ARCHITECTURE_INDEX.md`
- `my_agent/docs/llm/MAINTENANCE_LOG.md`

实现方案：

- 先列出本 PLAN 实际改动的代码和测试文件，至少包括 `my_agent/src/agents/coding_state.py`、`my_agent/src/agents/coding_cli.py` 以及相关测试文件中确实发生变化的文件。
- 如果本 PLAN 的代码尚未提交，使用 `git diff -- my_agent/src/agents/coding_state.py my_agent/src/agents/coding_cli.py my_agent/tests/test_coding_state.py my_agent/tests/test_coding_cli.py` 查看当前文件相对 `HEAD` 的 diff。
- 如果本 PLAN 已经单独提交，使用 `git diff HEAD~1..HEAD -- my_agent/src/agents/coding_state.py my_agent/src/agents/coding_cli.py my_agent/tests/test_coding_state.py my_agent/tests/test_coding_cli.py` 查看本 PLAN 相对上一个 commit 的 diff。
- 从 diff 中提取新增/改名/删除的 CLI 参数、state envelope 字段、保存/读取接口、测试入口和行为边界，禁止只凭记忆更新文档。
- 在 `STATE_AND_CONTRACTS.md` 增加 state JSON envelope 字段说明，包括 task、workspace_root、profile/config、snapshot、pending_approvals，以及不保存 manifest env values 的边界。
- 在 `RUNTIME_FLOWS.md` 增加 fresh CLI run 遇到 pending approval 后写 state 文件的流程。
- 在 `MODULE_CARDS.md` 增加 `coding_state.py` 职责，并更新 `coding_cli.py` 对 `--state-json` 的职责。
- 在 `SYMBOL_MAP.md` 增加 `CodingRunStateStore`、`CodingRunStateEnvelope`、`save_pending_result`、`load_envelope` 等入口。
- 在 `ARCHITECTURE_INDEX.md` 标记“pending approval 可持久化，但尚不能 approve/reject resume，后者由 PLAN03 实现”。
- 在 `MAINTENANCE_LOG.md` 记录本 PLAN 完课后 docs/llm 已同步，以及对应测试命令。

执行标准：

- 课堂总结必须记录使用的 diff 命令，以及从 diff 归纳出的关键代码变化清单。
- 文档必须明确 PLAN02 只保存 state，不实现 resume。
- 文档中的 state 字段不能暗示保存敏感 env value。
- 如果某个 docs/llm 文件检查后无需修改，课堂总结必须说明原因。

## 5. 本 PLAN 不做的事情

- 不解析 approve/reject 参数。
- 不调用 `resume_agent_loop()`。
- 不增强审批风险摘要。
- 不修改 verification 策略。
- 不改变 trajectory 事件格式。

## 6. 验收标准

完成 PLAN02 后必须满足：

1. `python -m agents.coding_cli ... --state-json .agent/run-state.json` 在 pending approval 时写出 state JSON。
2. state JSON 不包含不可序列化对象。
3. state JSON 不泄露 manifest env values。
4. CLI 未传 `--state-json` 时兼容旧行为。
5. `tests/test_coding_state.py` 和 `tests/test_coding_cli.py` 覆盖保存路径。
6. 已完成“课程 5：维护项目结构文档说明”，`my_agent/docs/llm` 与本 PLAN 的最终代码、测试和 state 文件契约一致。
