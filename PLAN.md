# RunResult 教学计划

本模块分多节课升级 my_agent 的运行结果系统，目标是从 AgentRunResult 过渡到 OpenAI Agents SDK 风格的 RunResult。

每节课开始前必须先阅读本文件。每节课只做小步修改，新增实现代码尽量不超过 60 行，不含测试。每节课要先说明本节目的，再展示修改代码、文件位置、行号和作用。不要删除已有注释。

递进顺序：
1. 新增 result.py，建立 RunResultBase / RunResult 外壳，兼容旧字段。
2. 给结果对象增加 final_output、last_response_id、final_output_as。
3. 在 RunState 中记录 input 和 last_agent，并让 build_run_result 返回 RunResult。
4. 在 run_loop 中写入 input、维护 last_agent，handoff 后更新最后 agent。
5. 实现 to_input_list，把 run item 转成下一轮可复用 ChatMessage 历史。
6. 补充公开导出，保证 AgentRunResult 旧 API 可用。
7. 回归测试并总结本模块对后续 Session、Approval、Streaming 的作用。

本模块暂不实现 async、streaming、human approval、SQLite session、MCP。
