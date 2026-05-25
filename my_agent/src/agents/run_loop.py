from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import RunItem
from .guardrails import (
    InputGuardrail,
    InputGuardrailResult,
    OutputGuardrail,
    OutputGuardrailResult,
)
from .lifecycle import (
    LifecycleHookSequence,
    emit_agent_end,
    emit_agent_start,
    emit_error,
)
from .run_config import RunConfig
from .run_context import RunContextWrapper
from .result import RunResult
from .run_steps import (
    execute_handoff,
    execute_tool_call,
    prepare_turn_input,
    record_final_output,
    record_model_error,
    record_run_stopped,
    record_tool_call,
    record_tool_error,
    run_model_turn,
)
from .run_state import RunState, build_run_result
from .tool_guardrails import ToolGuardrailTripwireTriggered
from .tracing import agent_span, guardrail_span, task_span, trace, turn_span

if TYPE_CHECKING:
    from .agent import Agent


def _resolve_max_steps(agent: Agent, config: RunConfig | None) -> int:
    if config is not None and config.max_steps is not None:
        return config.max_steps
    return agent.max_steps


def _resolve_max_turns(config: RunConfig | None) -> int | None:
    if config is not None and config.max_turns is not None:
        return config.max_turns
    return None


def _resolve_tool_use_behavior(
    agent: Agent,
    config: RunConfig | None,
) -> str | dict[str, list[str]]:
    if config is not None and config.tool_use_behavior is not None:
        return config.tool_use_behavior
    return agent.tool_use_behavior


def _create_run_context(config: RunConfig | None) -> RunContextWrapper:
    if config is None:
        return RunContextWrapper()
    return RunContextWrapper(
        context=config.context,
        metadata=dict(config.metadata or {}),
    )


# 统一合并 run-level hooks 和 agent-level hooks。顺序是 RunConfig.hooks 先触发，Agent.hooks 后触发。
def _collect_lifecycle_hooks(
    agent: Agent,
    config: RunConfig | None,
) -> LifecycleHookSequence:
    hooks = []
    if config is not None and config.hooks is not None:
        hooks.append(config.hooks)
    if agent.hooks is not None:
        hooks.append(agent.hooks)
    return tuple(hooks)


def _collect_input_guardrails(
    agent: Agent,
    config: RunConfig | None,
) -> tuple[InputGuardrail, ...]:
    guardrails = [*agent.input_guardrails]
    if config is not None and config.input_guardrails is not None:
        guardrails.extend(config.input_guardrails)
    return tuple(guardrails)


def _collect_output_guardrails(
    agent: Agent,
    config: RunConfig | None,
) -> tuple[OutputGuardrail, ...]:
    guardrails = [*agent.output_guardrails]
    if config is not None and config.output_guardrails is not None:
        guardrails.extend(config.output_guardrails)
    return tuple(guardrails)


def _record_input_guardrail_result(
    run_state: RunState,
    result: InputGuardrailResult,
    step_number: int,
) -> None:
    run_state.input_guardrail_results.append(result)
    run_state.new_items.append(
        RunItem(
            item_type="input_guardrail",
            step_number=step_number,
            payload=result,
            metadata={
                "guardrail_name": result.guardrail_name,
                "tripwire_triggered": result.output.tripwire_triggered,
            },
        )
    )


def _record_output_guardrail_result(
    run_state: RunState,
    result: OutputGuardrailResult,
    step_number: int,
) -> None:
    run_state.output_guardrail_results.append(result)
    run_state.new_items.append(
        RunItem(
            item_type="output_guardrail",
            step_number=step_number,
            payload=result,
            metadata={
                "guardrail_name": result.guardrail_name,
                "tripwire_triggered": result.output.tripwire_triggered,
            },
        )
    )


# 输入是否被拦截
def _run_input_guardrails(
    agent: Agent,
    task: str,
    run_state: RunState,
    step_number: int,
    guardrails: tuple[InputGuardrail, ...],
) -> bool:
    for guardrail in guardrails:
        result = _run_input_guardrail_with_tracing(
            guardrail,
            agent,
            task,
            run_state,
        )
        _record_input_guardrail_result(run_state, result, step_number)
        if result.output.tripwire_triggered:
            return True
    return False


def _run_output_guardrails(
    agent: Agent,
    output: object,
    run_state: RunState,
    step_number: int,
    guardrails: tuple[OutputGuardrail, ...],
) -> bool:
    for guardrail in guardrails:
        result = _run_output_guardrail_with_tracing(
            guardrail,
            agent,
            output,
            run_state,
        )
        _record_output_guardrail_result(run_state, result, step_number)
        if result.output.tripwire_triggered:
            _clear_final_output(run_state, step_number)
            return True
    return False


def _clear_final_output(run_state: RunState, step_number: int) -> None:
    run_state.final_answer = None
    run_state.reached_final_answer = False
    run_state.new_items = [
        item
        for item in run_state.new_items
        if not (item.item_type == "final_output" and item.step_number == step_number)
    ]


def _run_input_guardrail_with_tracing(
    guardrail: InputGuardrail,
    agent: Agent,
    task: str,
    run_state: RunState,
) -> InputGuardrailResult:
    with guardrail_span(guardrail.get_name(), stage="input") as guardrail_span_ctx:
        result = guardrail.run(agent, task, run_state.context_wrapper)
        guardrail_span_ctx.record.span_data.data["tripwire_triggered"] = (
            result.output.tripwire_triggered
        )
        return result


def _run_output_guardrail_with_tracing(
    guardrail: OutputGuardrail,
    agent: Agent,
    output: object,
    run_state: RunState,
) -> OutputGuardrailResult:
    with guardrail_span(guardrail.get_name(), stage="output") as guardrail_span_ctx:
        result = guardrail.run(run_state.context_wrapper, agent, output)
        guardrail_span_ctx.record.span_data.data["tripwire_triggered"] = (
            result.output.tripwire_triggered
        )
        return result


def run_agent_loop(
    agent: Agent,
    task: str,
    config: RunConfig | None = None,
) -> RunResult:
    workflow_name = (
        config.workflow_name
        if config is not None and config.workflow_name is not None
        else agent.name
    )
    trace_metadata = (
        config.trace_metadata
        if config is not None and config.trace_metadata is not None
        else (config.metadata if config is not None else None)
    )
    with trace(
        workflow_name,
        trace_id=config.trace_id if config is not None else None,
        group_id=config.group_id if config is not None else None,
        metadata=trace_metadata,
        disabled=config.tracing_disabled if config is not None else False,
        only_if_missing=True,
    ):
        with task_span(task):
            with agent_span(agent.name, task=task):
                return _run_agent_loop_impl(agent, task, config=config)


def _run_agent_loop_impl(
    agent: Agent,
    task: str,
    config: RunConfig | None = None,
) -> RunResult:
    agent.memory.add_task(task)
    effective_max_steps = _resolve_max_steps(agent, config)
    effective_max_turns = _resolve_max_turns(config)
    effective_tool_use_behavior = _resolve_tool_use_behavior(agent, config)
    context_wrapper = _create_run_context(config)
    lifecycle_hooks = _collect_lifecycle_hooks(agent, config)
    input_guardrails = _collect_input_guardrails(agent, config)
    output_guardrails = _collect_output_guardrails(agent, config)
    run_state = RunState(
        input=task,
        last_agent=agent,
        max_steps=effective_max_steps,
        max_turns=effective_max_turns,
        context_wrapper=context_wrapper,
    )
    # 每次 run 创建上下文后立刻触发 on_agent_start

    emit_agent_start(lifecycle_hooks, context_wrapper, agent)

    step_number = run_state.next_step_number()

    # 如果输入被拦截 直接终止
    if _run_input_guardrails(agent, task, run_state, step_number, input_guardrails):
        record_run_stopped(run_state, step_number, "input_guardrail_triggered")
        emit_agent_end(lifecycle_hooks, context_wrapper, agent, run_state.final_answer)
        return build_run_result(run_state)

    while True:
        step_number = run_state.next_step_number()
        if not run_state.can_execute_tool():
            record_run_stopped(
                run_state,
                step_number,
                "max_steps_reached",
            )
            break
        if not run_state.can_call_model():
            record_run_stopped(
                run_state,
                step_number,
                "max_turns_reached",
            )
            break

        with turn_span(run_state.current_turn + 1, agent.name):
            turn_input = prepare_turn_input(agent, run_state.context_wrapper)

            try:
                run_state.record_model_turn()
                model_turn = run_model_turn(
                    agent,
                    turn_input,
                    run_state,
                    step_number,
                    lifecycle_hooks,
                    trace_include_sensitive_data=(
                        config.trace_include_sensitive_data if config is not None else True
                    ),
                )
                tool_calls = model_turn.tool_calls
            except Exception as exc:
                emit_error(lifecycle_hooks, context_wrapper, agent, exc)
                record_model_error(
                    agent,
                    str(exc),
                    run_state,
                    step_number,
                )
                break


            # 如果模型直接返回 final answer，run_state.reached_final_answer 会变成 True
            if run_state.reached_final_answer:
                if _run_output_guardrails(
                    agent,
                    run_state.final_answer,
                    run_state,
                    step_number,
                    output_guardrails,
                ):
                    # 触发拦截,清空 final answer , 标记当前没有合法answer ,并退出循环
                    record_run_stopped(
                        run_state,
                        step_number,
                        "output_guardrail_triggered",
                    )
                break

            if not tool_calls:
                record_run_stopped(
                    run_state,
                    step_number,
                    "model_returned_no_tool_call",
                )
                break

            handoff_happened = False
            guardrail_happened = False
            for action in tool_calls:
                if not run_state.can_execute_tool():
                    break

                step_number = run_state.next_step_number()
                record_tool_call(run_state, action, step_number)

                handoff_target = agent._handoff_target_for(action)
                if handoff_target is not None:
                    handoff_outcome = execute_handoff(
                        agent,
                        action,
                        handoff_target,
                        run_state,
                        step_number,
                        lifecycle_hooks,
                    )
                    if handoff_outcome.reached_final_answer:
                        run_state.last_agent = handoff_target
                    if run_state.reached_final_answer and _run_output_guardrails(
                        agent,
                        handoff_outcome.final_answer,
                        run_state,
                        step_number,
                        output_guardrails,
                    ):
                        record_run_stopped(
                            run_state,
                            step_number,
                            "output_guardrail_triggered",
                        )
                        guardrail_happened = True
                    handoff_happened = True
                    break

                try:
                    tool_outcome = execute_tool_call(
                        agent,
                        action,
                        run_state,
                        step_number,
                        effective_tool_use_behavior,
                        lifecycle_hooks,
                        # 有 output guardrails 时，execute_tool_call 不能直接确认 final_answer。
                        finalize_output=not output_guardrails,
                        trace_include_sensitive_data=(
                            config.trace_include_sensitive_data if config is not None else True
                        ),
                    )
                except ToolGuardrailTripwireTriggered:
                    raise
                except Exception as exc:
                    emit_error(lifecycle_hooks, context_wrapper, agent, exc)
                    record_tool_error(
                        agent,
                        action,
                        str(exc),
                        run_state,
                        step_number,
                    )
                    continue

                '''
                tool_outcome.result_value 是候选 final answer
                先跑 output guardrails
                如果触发：停止，并且不提交 final_answer
                如果没触发 record_final_output 正式提交 final_answer
                '''
                
                if tool_outcome.should_stop:
                    if output_guardrails:
                        if _run_output_guardrails(
                            agent,
                            tool_outcome.result_value,
                            run_state,
                            step_number,
                            output_guardrails,
                        ):
                            record_run_stopped(
                                run_state,
                                step_number,
                                "output_guardrail_triggered",
                            )
                            guardrail_happened = True
                            break
                        record_final_output(
                            run_state,
                            step_number,
                            tool_outcome.result_value,
                        )
                    break

            if run_state.reached_final_answer or handoff_happened or guardrail_happened:
                break

    emit_agent_end(lifecycle_hooks, context_wrapper, agent, run_state.final_answer)
    return build_run_result(run_state)
