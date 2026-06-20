# PLAN05：验证策略 CLI 化与执行闭环

## 1. 模块主要实现内容

本模块让 coding CLI 可以配置 verification policy，并在 shell/edit 等工具之后自动运行验证命令。当前 `my_agent` 已经有 `VerificationPolicy`、`VerificationRunner`、`run_verification_after_tool()`，但 CLI 没有完整暴露这些能力，导致本地 coding 任务缺少“修改后自动测试”的闭环。

本模块完成后，用户可以这样运行：

```powershell
python -m agents.coding_cli --workspace . --profile edit-local --task "修复测试" --verify-command "python -m pytest" --verify-after-tool apply_patch --state-json .agent/run-state.json
```

## 2. 参考文件与参考能力

主参考：

- `reference/aider-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `Commands.cmd_test()` 和 `Coder.lint_edited()` 把 lint/test 失败回灌给模型。
  - `reflected_message` 机制说明验证失败不应只是打印，而应成为下一轮上下文。

补充参考：

- `reference/mini-swe-agent-main/PROJECT_ARCHITECTURE_ANALYSIS.md`
  - `Environment.execute()` 返回结构化 output，便于 observation。

当前 `my_agent` 相关文件：

- `my_agent/src/agents/verification.py`
- `my_agent/src/agents/run_recording.py`
- `my_agent/src/agents/run_loop.py`
- `my_agent/src/agents/coding_cli.py`
- `my_agent/src/agents/workspace_manifest.py`
- `my_agent/tests/test_verification.py`
- `my_agent/tests/test_verification_loop.py`
- `my_agent/tests/test_coding_cli.py`

## 3. 计划新增内容

CLI 新增参数建议：

```text
--verify-command COMMAND
--verify-after-tool TOOL_NAME
--verify-max-attempts N
--verify-output-chars N
```

默认不启用 verification，避免改变已有用户行为。

## 4. 课程拆分

### 课程 1：CLI 解析 verification 参数

优化目标：让用户可以通过 CLI 设置验证命令。

旧代码缺陷：`VerificationPolicy` 只能通过代码构造，不适合本地 CLI 教学。

新增能力：`CodingCliConfig` 携带 verification 参数。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- `--verify-command` 可以重复传入，形成 tuple。
- `--verify-after-tool` 可以重复传入。
- `--verify-max-attempts` 默认 1。
- `--verify-output-chars` 默认 None 或合理值。

执行标准：

- 新增代码不超过 80 行。
- 未传参数时 `RunConfig.verification_policy` 仍为空或 disabled。

### 课程 2：将 CLI verification 接入 RunConfig

优化目标：把 CLI 参数转换成 `VerificationPolicy` 并传给 `build_coding_agent` 后的 `RunConfig`。

旧代码缺陷：`build_coding_cli_setup()` 没有组装 verification。

新增能力：coding CLI run 可以自动触发验证。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 在 `build_coding_cli_setup()` 中构造 `VerificationPolicy`。
- 使用 `dataclasses.replace(setup.run_config, verification_policy=policy)` 或现有项目风格等价方式。
- 如果 verification 命令不在 manifest allowlist，要么加入 manifest allowed test commands，要么明确报错。建议复用 `WorkspaceManifest.allowed_test_commands` 规则。

执行标准：

- 新增代码不超过 80 行。
- policy.commands、auto_after_tools、max_attempts、max_output_chars 与 CLI 参数一致。

### 课程 3：验证结果输出与 exit code 语义

优化目标：用户能看出验证是否执行、是否失败。

旧代码缺陷：verification summary 主要存在于 result 内部，不一定被 CLI 清晰展示。

新增能力：CLI 输出 verification summary。

修改文件：

- `my_agent/src/agents/coding_cli.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- `_print_result()` 在 final output 后打印 verification summary。
- 如果 run stopped 且 verification failed，输出最后失败命令和裁剪后的 observation。
- exit code 不要因为单独 verification failed 直接覆盖 pending approval；pending approval 仍为 2。

执行标准：

- 新增代码不超过 80 行。
- 验证失败时 CLI 输出包含 command、returncode、status。

### 课程 4：验证失败回灌模型的行为测试

优化目标：证明 verification failure 会作为 observation 进入下一轮模型上下文。

旧代码缺陷：verification 结果存在，但端到端教学目标需要证明它能驱动修复。

新增能力：测试验证失败后模型能看到失败内容并继续输出下一步。

修改文件：

- `my_agent/tests/test_verification_loop.py`
- `my_agent/tests/test_coding_cli.py`

实现方案：

- 使用 fake environment 返回失败 `CommandResult`。
- fake model 第一次调用工具，第二次检查 memory/messages 中存在 verification observation。
- 不要求模型真的修复代码，只要求反馈进入 loop。

执行标准：

- 不运行真实测试命令。
- 不调用真实 OpenAI。

### 课程 5：维护项目结构文档说明

优化目标：先对比本 PLAN 完课后的当前文件与 git 上一个 commit 版本之间的 diff，再根据真实代码变化把 verification CLI 化与执行闭环同步写入 `C:\Users\ch\Desktop\ai agent学习\my_agent\docs\llm`，让后续 PLAN06 能把验证结果纳入 trajectory 证据链。

旧代码缺陷：如果文档只记录 verification runner 的底层能力，不记录 CLI 参数、触发时机和失败反馈路径，后续课程难以判断 verification result 应该出现在 result、memory 还是 trajectory 中。

新增能力：项目结构文档能说明 verification CLI 参数、`VerificationPolicy` 接入、shell/edit 后自动触发、失败 observation 回灌、result summary 和测试入口。

修改文件：

- `my_agent/docs/llm/STATE_AND_CONTRACTS.md`
- `my_agent/docs/llm/RUNTIME_FLOWS.md`
- `my_agent/docs/llm/MODULE_CARDS.md`
- `my_agent/docs/llm/SYMBOL_MAP.md`
- `my_agent/docs/llm/ARCHITECTURE_INDEX.md`
- `my_agent/docs/llm/MAINTENANCE_LOG.md`

实现方案：

- 先列出本 PLAN 实际改动的代码和测试文件，至少包括 `my_agent/src/agents/coding_cli.py`、`my_agent/src/agents/verification.py`、`my_agent/src/agents/run_loop.py` 以及相关测试文件中确实发生变化的文件。
- 如果本 PLAN 的代码尚未提交，使用 `git diff -- my_agent/src/agents/coding_cli.py my_agent/src/agents/verification.py my_agent/src/agents/run_loop.py my_agent/tests/test_verification.py my_agent/tests/test_verification_loop.py my_agent/tests/test_coding_cli.py` 查看当前文件相对 `HEAD` 的 diff。
- 如果本 PLAN 已经单独提交，使用 `git diff HEAD~1..HEAD -- my_agent/src/agents/coding_cli.py my_agent/src/agents/verification.py my_agent/src/agents/run_loop.py my_agent/tests/test_verification.py my_agent/tests/test_verification_loop.py my_agent/tests/test_coding_cli.py` 查看本 PLAN 相对上一个 commit 的 diff。
- 从 diff 中提取新增/改名/删除的 verification CLI 参数、policy 构造入口、失败反馈路径、result summary 字段、测试入口和行为边界，禁止只凭记忆更新文档。
- 在 `RUNTIME_FLOWS.md` 增加 tool execution -> verification -> failure feedback -> next model turn 的流程。
- 在 `STATE_AND_CONTRACTS.md` 记录 verification result 在 `RunItem`、memory observation、result summary 中的状态边界。
- 在 `MODULE_CARDS.md` 更新 `coding_cli.py`、`verification.py`、`run_loop.py` 的协作关系。
- 在 `SYMBOL_MAP.md` 增加 verification CLI 解析 helper、`VerificationPolicy` 构造入口或等价符号。
- 在 `ARCHITECTURE_INDEX.md` 标记 CLI 已具备验证闭环，但 trajectory 完整证据由 PLAN06 完成。
- 在 `MAINTENANCE_LOG.md` 记录本 PLAN 完课后 docs/llm 已同步，以及 verification 相关测试命令。

执行标准：

- 课堂总结必须记录使用的 diff 命令，以及从 diff 归纳出的关键代码变化清单。
- 文档必须明确未传 verification 参数时不自动运行验证。
- 文档必须说明验证失败不是异常终止，而是 observation 和 run evidence。
- 如果某个 docs/llm 文件检查后无需修改，课堂总结必须说明原因。

## 5. 本 PLAN 不做的事情

- 不实现 lint parser。
- 不做 test selection。
- 不做 Git checkpoint。
- 不把 verification 改成异常机制；失败仍是 observation 和 run evidence。

## 6. 验收标准

完成 PLAN05 后必须满足：

1. CLI 可配置 verification commands 和触发工具。
2. shell/edit 后按 policy 自动运行验证。
3. verification result 进入 `RunItem`、memory observation、result summary。
4. CLI 输出展示验证状态。
5. pending approval、resume 和 verification 能一起工作。
6. `tests/test_verification.py`、`tests/test_verification_loop.py`、`tests/test_coding_cli.py` 通过。
7. 已完成“课程 5：维护项目结构文档说明”，`my_agent/docs/llm` 与本 PLAN 的最终代码、测试和验证闭环一致。
