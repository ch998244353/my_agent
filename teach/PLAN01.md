# PLAN01：运行状态快照与审批恢复契约加固

## 1. 模块主要实现内容

本模块只处理运行状态契约，不处理 CLI。目标是让 `RunResult.to_state()` 产生的 `RunStateSnapshot` 可以被可靠保存到 JSON，再恢复为 `RunState`，并保持 pending approval、tool call、model response state、verification item 等关键数据不丢失。

当前 `my_agent` 已经有 `RunResult.to_state()`、`RunStateSnapshot`、`RunState.from_snapshot()`、`RunContextWrapper.export_tool_approvals()`。缺陷是：这些能力主要面向内存恢复，缺少明确的 JSON dict 往返接口；`result.py` 对未知 payload 仍有浅序列化风险；CLI 后续无法判断 state 文件 schema 和兼容性。

本 PLAN 完成后，后续 PLAN02 可以安全把 snapshot 写入 state 文件。

## 2. 参考文件与参考能力

主参考：

- `reference/openaiagent/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `src/agents/run_state.py`：可序列化恢复状态，保存 approvals、trace、sandbox resume state。
  - `src/agents/result.py`：`RunResult.to_state()` 用于 interruption 后恢复。
  - `src/agents/run_context.py`：approval 状态集中在 runtime context，不直接进入 LLM prompt。

当前 `my_agent` 相关文件：

- `my_agent/src/agents/run_state.py`
- `my_agent/src/agents/result.py`
- `my_agent/src/agents/contracts.py`
- `my_agent/src/agents/run_context.py`
- `my_agent/tests/test_run_state.py`
- `my_agent/tests/test_result.py`
- `my_agent/tests/test_run_context_approvals.py`

## 3. 计划新增内容

建议新增或补强以下接口：

```python
def run_state_snapshot_to_dict(snapshot: RunStateSnapshot) -> dict[str, Any]: ...
def run_state_snapshot_from_dict(data: Mapping[str, Any]) -> RunStateSnapshot: ...
```

如果更符合现有风格，也可以作为 `RunStateSnapshot.to_dict()` 和 `RunStateSnapshot.from_dict()` 方法实现。不要同时实现两套公开 API。

必须明确 state schema version，例如：

```python
RUN_STATE_SNAPSHOT_SCHEMA_VERSION = 1
```

这个版本只表示本地 JSON 文件结构，不替代现有 `RunStateSnapshot` 的 Python dataclass 结构。

## 4. 课程拆分

### 课程 1：建立 snapshot JSON envelope 基础

优化目标：让 `RunStateSnapshot` 可以被转成 JSON-safe dict。

旧代码缺陷：`RunResult.to_state()` 返回 dataclass，后续 CLI 直接 `asdict()` 容易绕开 payload 转换规则，也没有 schema version。

新增能力：提供单一 JSON-safe 转换入口。

修改文件：

- `my_agent/src/agents/run_state.py`
- `my_agent/tests/test_run_state.py`

实现方案：

- 在 `run_state.py` 增加 schema 常量。
- 增加 `run_state_snapshot_to_dict()`，内部处理 dataclass、tuple、approval snapshot、model response state。
- 禁止把 `RunContextWrapper.context` 放入 JSON，因为 workspace/environment 对象不可序列化。

执行标准：

- 新增代码不超过 80 行，不含测试。
- `json.dumps(run_state_snapshot_to_dict(snapshot))` 不报错。
- 单测覆盖空 run state、含 approval snapshot 的 run state。

### 课程 2：恢复 JSON dict 到 RunStateSnapshot

优化目标：从 JSON dict 恢复 `RunStateSnapshot`，为 CLI resume 做准备。

旧代码缺陷：`RunState.from_snapshot()` 需要 dataclass，但 CLI 只能从 JSON 文件得到 dict。

新增能力：提供 dict -> dataclass 的入口，并验证必要字段。

修改文件：

- `my_agent/src/agents/run_state.py`
- `my_agent/tests/test_run_state.py`

实现方案：

- 增加 `run_state_snapshot_from_dict()`。
- 对缺失字段使用当前 dataclass 默认值或显式 `ValueError`，不要静默吞掉关键字段。
- 恢复 `new_items` 时继续复用现有 `_tool_approval_request_from_state` 等 helper，避免重复解析逻辑。

执行标准：

- 新增代码不超过 80 行。
- snapshot dict 往返后，`pending_tool_calls` 能被 `RunState.from_snapshot()` 还原。

### 课程 3：加固 payload JSON 安全边界

优化目标：保证 approval、verification、model_response 等 payload 在 snapshot 中不会变成不可 JSON 序列化对象。

旧代码缺陷：`result.py` 已有 `_run_item_payload_to_state()`，但未知 payload 可能原样穿透。

新增能力：对本阶段需要保存的 payload 类型明确转换；未知 payload 至少转换成 JSON-safe repr 或保守 dict。

修改文件：

- `my_agent/src/agents/result.py`
- `my_agent/tests/test_result.py`

实现方案：

- 补充 `_run_item_payload_to_state()` 对 `VerificationResult` 或相关 verification payload 的处理。
- 对 `ToolApprovalRequest` 保持现有字段完整：`tool_name`、`call_id`、`arguments`、`reason`。
- 对未知 payload 不新增复杂框架，只保证不会破坏 CLI state 文件。

执行标准：

- 新增代码不超过 80 行。
- 包含 `tool_approval_required`、`verification_result`、`model_response` 的 result 可以 JSON 序列化。

### 课程 4：审批恢复契约回归测试

优化目标：用测试证明“暂停审批 -> 保存 snapshot -> 加载 snapshot -> approve/reject -> resume 所需状态存在”。

旧代码缺陷：现有测试覆盖 runtime approval，但没有覆盖“文件持久化前置契约”。

新增能力：为 PLAN02/PLAN03 提供保护网。

修改文件：

- `my_agent/tests/test_run_state.py`
- `my_agent/tests/test_result.py`
- `my_agent/tests/test_run_context_approvals.py`

实现方案：

- 使用已有 fake/tool call 构造 pending approval。
- 调用 `RunResult.to_state()`。
- 调用本 PLAN 新增的 snapshot dict 往返接口。
- 用 `RunState.from_snapshot()` 恢复。

执行标准：

- 不调用真实 OpenAI API。
- 不运行 shell。
- 测试能证明 approval key 仍为 `(tool_name, call_id)`。

### 课程 5：维护项目结构文档说明

优化目标：先对比本 PLAN 完课后的当前文件与 git 上一个 commit 版本之间的 diff，再根据真实代码变化把状态快照契约同步写入 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm`，让后续空白记忆 agent 不需要重新读全部源码也能理解新的 state/resume 前置能力。

旧代码缺陷：`docs/llm` 是后续教学和记忆压缩后的项目入口，如果只改 `run_state.py`、`result.py` 和测试，不更新文档，后续 PLAN02/PLAN03 的实现者会误以为 snapshot 仍只有内存恢复能力。

新增能力：项目结构文档能明确说明 snapshot JSON-safe 导出/导入接口、schema version、approval key、payload 安全边界和对应测试入口。

修改文件：

- `my_agent/docs/llm/STATE_AND_CONTRACTS.md`
- `my_agent/docs/llm/RUNTIME_FLOWS.md`
- `my_agent/docs/llm/MODULE_CARDS.md`
- `my_agent/docs/llm/SYMBOL_MAP.md`
- `my_agent/docs/llm/ARCHITECTURE_INDEX.md`
- `my_agent/docs/llm/MAINTENANCE_LOG.md`

实现方案：

- 先列出本 PLAN 实际改动的代码和测试文件，至少包括 `my_agent/src/agents/run_state.py`、`my_agent/src/agents/result.py`、`my_agent/src/agents/contracts.py`、`my_agent/src/agents/run_context.py` 以及相关测试文件中确实发生变化的文件。
- 如果本 PLAN 的代码尚未提交，使用 `git diff -- my_agent/src/agents/run_state.py my_agent/src/agents/result.py my_agent/src/agents/contracts.py my_agent/src/agents/run_context.py my_agent/tests/test_run_state.py my_agent/tests/test_result.py my_agent/tests/test_run_context_approvals.py` 查看当前文件相对 `HEAD` 的 diff。
- 如果本 PLAN 已经单独提交，使用 `git diff HEAD~1..HEAD -- my_agent/src/agents/run_state.py my_agent/src/agents/result.py my_agent/src/agents/contracts.py my_agent/src/agents/run_context.py my_agent/tests/test_run_state.py my_agent/tests/test_result.py my_agent/tests/test_run_context_approvals.py` 查看本 PLAN 相对上一个 commit 的 diff。
- 从 diff 中提取新增/改名/删除的 public API、dataclass 字段、状态字段、测试入口和行为边界，禁止只凭记忆更新文档。
- 在 `STATE_AND_CONTRACTS.md` 记录 `RunStateSnapshot` 的 JSON-safe 往返契约、schema version 和“不保存 runtime context 对象”的边界。
- 在 `RUNTIME_FLOWS.md` 补充 pending approval 之后“可被文件持久化”的状态流，但不要写 PLAN02 尚未实现的 CLI state 文件细节。
- 在 `MODULE_CARDS.md` 更新 `run_state.py`、`result.py`、`run_context.py` 的职责说明。
- 在 `SYMBOL_MAP.md` 增加或修正 `run_state_snapshot_to_dict`、`run_state_snapshot_from_dict`、`RUN_STATE_SNAPSHOT_SCHEMA_VERSION` 等符号入口。
- 在 `ARCHITECTURE_INDEX.md` 增加本阶段已完成的状态契约能力索引。
- 在 `MAINTENANCE_LOG.md` 记录本 PLAN 完课后 docs/llm 已同步，以及对应测试命令。

执行标准：

- 课堂总结必须记录使用的 diff 命令，以及从 diff 归纳出的关键代码变化清单。
- 文档只描述 PLAN01 已实现能力，不提前声明 PLAN02/PLAN03 的 CLI 行为已经存在。
- 文档中的函数名、文件名、测试名必须与实际代码一致。
- 如果某个 docs/llm 文件检查后无需修改，课堂总结必须说明原因。

## 5. 本 PLAN 不做的事情

- 不新增 CLI 参数。
- 不写 state 文件。
- 不处理 approve/reject 用户输入。
- 不改 shell/edit approval policy。
- 不改 trajectory。

这些分别由 PLAN02、PLAN03、PLAN04、PLAN06 处理。

## 6. 验收标准

完成 PLAN01 后必须满足：

1. `RunStateSnapshot` 有一个明确的 JSON-safe 导出/导入入口。
2. 含 pending approval 的 snapshot 能 JSON 往返。
3. `RunState.from_snapshot()` 能从恢复后的 snapshot 找到 pending tool calls。
4. 所有新增行为有单测。
5. 现有 `tests/test_run_state.py`、`tests/test_result.py`、`tests/test_run_context_approvals.py` 通过。
6. 已完成“课程 5：维护项目结构文档说明”，`my_agent/docs/llm` 与本 PLAN 的最终代码、测试和状态契约一致。
