# 架构文档维护

## 触发条件

模块边界、入口、调用链、公共接口、状态、配置、持久化或跨模块依赖变化时，必须更新本目录文档并追加 `CHANGELOG.md`。

## 首次全量分析（BOOTSTRAP）

1. 读取项目指令、目录、依赖、入口、配置、imports、测试和 Git 状态。
2. 按逻辑能力（不是按文件）建立模块卡片，再从真实入口追踪流程。
3. 每条结论标为 `[FACT]`、`[INFERENCE]` 或 `[UNKNOWN]`；FACT 必须给 `路径::符号`，INFERENCE 必须说明源码依据。
4. 依次维护 `MODULE_CARDS`、`RUNTIME_FLOWS`、`STATE_AND_CONTRACTS`、`SYMBOL_MAP`、`ARCHITECTURE_INDEX`、本文件和 `CHANGELOG`。

## 增量分析（INCREMENTAL）

1. 先读本目录全部文档与 `ARCHITECTURE_INDEX.md` 的最近验证 revision。
2. 检查 `git status --short`、该 revision 到 HEAD 的 diff，以及 staged/unstaged diff。
3. 对每个变更文件反查：直接调用者、imports、注册表、配置、所属模块、关联流程和测试。
4. CodeGraph 查询不到当前磁盘已存在的符号时，先执行全量重建再下结论；`sync` 显示 up to date 不能证明未跟踪新文件已入库。
5. 只重查受影响路径；旧结论若无法确认，改为 `[NEEDS_REVIEW]`，不要凭旧文档保留。
6. 更新最近验证 revision，追加 changelog，并检查链接和引用是否仍存在。

## 影响范围速查

| 变更位置 | 最少需复查 |
|---|---|
| `agent/run_loop/run_state` | 核心 loop、状态/快照、所有入口、结果与 session |
| `models/model_turn/model_settings/model_events/model_retry` | 请求构造、事件聚合、retry/fallback、工具提交边界、RunState/RunResult/snapshot、model span |
| `tools/tool_*/handoffs` | Agent schema、审批/恢复、guardrail、执行与测试 |
| `coding_* / workspace* / environment` | Coding CLI、profile 注册、manifest、持久化、shell/patch 风险 |
| `memory/context_*/repo_*` | fresh/resume 上下文、session、token/字符预算、相关测试 |
| `tracing/verification/guardrails` | 运行步骤、异常/停止语义、敏感数据与持久化 |
| `events/event_compat` | 事件字段与终态、sequence、序列化、attempt notice 投影、旧记录映射、run loop 接入状态 |
| `task_state/task_events/task_tools/run_config` | Store 唯一事实、TaskEvent 原子提交、run 起止校验、RunState/RunResult 标量关联、snapshot 版本、Planning 注册方式，以及 Session/CLI/SubAgent 未实现边界 |

fallback 观测变更还必须同时核对公共 `ModelFallbackEvent`、相邻 `ModelAttemptRecord`、snapshot attempt 字典和 `_record_model_execution_span()`；四处的 reason、目标 attempt id、from/to model 与 from/to mode 必须一致。

## 完成条件

- [ ] 所有新增/修改架构结论都有源码 `路径::符号`，或明确不确定。
- [ ] 入口可追到结果/停止路径；模块名和调用方向在各文档一致。
- [ ] CodeGraph 已包含当前工作树符号；动态边界已用直接源码补核并在文档中说明。
- [ ] snapshot writer/reader 版本、公共/私有导出和未实现边界均与测试一致。
- [ ] task runtime 变更没有把 `TaskStore`、`TaskNode` 或 `TaskEvent` 塞进 RunState、RunResult、snapshot 或 Session。
- [ ] Git diff 未被误写为业务事实；未修改业务源码。
- [ ] 相对链接有效，关键符号和路径仍存在。
- [ ] `CHANGELOG.md` 已记录 revision 范围、影响、验证和遗留项。
