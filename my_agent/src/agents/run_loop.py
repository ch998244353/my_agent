from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import ChatMessage, RunItem, ToolCall
from .environment import LocalEnvironment
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
from .memory import session_item_to_message, session_items_from_result
from .model_settings import ModelSettings
from .model_turn import TurnInput, prepare_turn_input, run_model_turn
from .run_config import RunConfig
from .run_context import RunContextWrapper
from .run_resume import resume_pending_tool_approvals
from .run_recording import (
    record_final_output,
    record_model_error,
    record_run_stopped,
    record_tool_call,
    run_verification_after_tool,
)
from .result import RunResult
from .repo_context import build_task_repo_context
from .run_steps import (
    execute_handoff,
)
from .run_state import RunState, build_run_result
from .tool_execution import execute_tool_call, record_tool_error
from .tool_guardrails import ToolGuardrailTripwireTriggered
from .tool_planning import ToolExecutionPlan, build_tool_execution_plan
from .tool_runtime import ToolExecutionLimits
from .tracing import agent_span, guardrail_span, task_span, trace, turn_span
from .turn_resolution import (
    NextStepFinalOutput,
    NextStepHandoff,
    NextStepPendingApproval,
    NextStepRunAgain,
    NextStepStopped,
    process_model_turn,
    resolve_handoff_step,
    resolve_model_response_step,
    resolve_pending_approval_step,
    resolve_tool_final_output_step,
    resolve_tool_run_again_step,
)
from .verification import VerificationRunner

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


def _resolve_model_settings(agent: Agent, config: RunConfig | None) -> ModelSettings:
    override = config.model_settings if config is not None else None
    return agent.model_settings.resolve(override)


def _resolve_tool_execution_limits(
    config: RunConfig | None,
) -> ToolExecutionLimits | None:
    if config is not None:
        return config.tool_execution_limits
    return None


def _resolve_verification_runner(
    config: RunConfig | None,
    context_wrapper: RunContextWrapper,
) -> VerificationRunner | None:
    if config is None or config.verification is None:
        return None
    if context_wrapper.verification_runner is not None:
        return context_wrapper.verification_runner
    if context_wrapper.environment is not None:
        return VerificationRunner(context_wrapper.environment)
    if context_wrapper.workspace is not None:
        return VerificationRunner(LocalEnvironment(workspace=context_wrapper.workspace))
    return VerificationRunner(LocalEnvironment())


def _session_messages(config: RunConfig | None) -> list[ChatMessage]:
    if config is None or config.session is None:
        return []
    return [
        session_item_to_message(item)
        for item in config.session.get_items()
    ]


def _prepend_session_messages(
    turn_input: TurnInput,
    session_messages: list[ChatMessage],
) -> TurnInput:
    if not session_messages:
        return turn_input
    return TurnInput(
        messages=[*session_messages, *turn_input.messages],
        tool_specs=turn_input.tool_specs,
        model_settings=turn_input.model_settings,
    )

# 把 RunResult.to_input_list() 生成的历史消息交给 session 保存
def _save_result_to_session(config: RunConfig | None, result: RunResult) -> None:
    if config is None or config.session is None:
        return
    config.session.add_items(session_items_from_result(result))


def _build_result_and_save_session(
    config: RunConfig | None,
    run_state: RunState,
) -> RunResult:
    result = build_run_result(run_state)
    _save_result_to_session(config, result)
    return result


def _tool_calls_selected_for_execution(
    execution_plan: ToolExecutionPlan,
) -> tuple[ToolCall, ...]:
    execution_batch = execution_plan.execution_batch
    selected_call_keys = {
        (action.tool_name, action.call_id)
        for action in (
            *execution_batch.approved_tool_calls,
            *execution_batch.handoff_calls,
        )
    }
    return tuple(
        action
        for action in execution_plan.actions
        if (action.tool_name, action.call_id) in selected_call_keys
    )


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


def resume_agent_loop(
    agent: Agent,
    run_state: RunState,
    config: RunConfig | None = None,
) -> RunResult:
    task = run_state.input if isinstance(run_state.input, str) else ""
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
                return _run_agent_loop_impl(
                    agent,
                    task,
                    config=config,
                    run_state=run_state,
                )


def _run_agent_loop_impl(
    agent: Agent,
    task: str,
    config: RunConfig | None = None,
    run_state: RunState | None = None,
) -> RunResult:
    is_resuming = run_state is not None
    if not is_resuming:
        agent.memory.add_task(task)
    effective_max_steps = _resolve_max_steps(agent, config)
    effective_max_turns = _resolve_max_turns(config)
    effective_tool_use_behavior = _resolve_tool_use_behavior(agent, config)
    effective_model_settings = _resolve_model_settings(agent, config)
    effective_tool_execution_limits = _resolve_tool_execution_limits(config)
    context_wrapper = (
        run_state.context_wrapper
        if run_state is not None
        else _create_run_context(config)
    )
    verification_runner = _resolve_verification_runner(config, context_wrapper)
    lifecycle_hooks = _collect_lifecycle_hooks(agent, config)
    input_guardrails = _collect_input_guardrails(agent, config)
    output_guardrails = _collect_output_guardrails(agent, config)
    if run_state is None:
        run_state = RunState(
            input=task,
            last_agent=agent,
            max_steps=effective_max_steps,
            max_turns=effective_max_turns,
            context_wrapper=context_wrapper,
        )
    else:
        run_state.last_agent = agent
        if run_state.input is None:
            run_state.input = task
        if run_state.max_steps is None:
            run_state.max_steps = effective_max_steps
        if run_state.max_turns is None:
            run_state.max_turns = effective_max_turns
    # 每次 run 创建上下文后立刻触发 on_agent_start

    emit_agent_start(lifecycle_hooks, context_wrapper, agent)

    step_number = run_state.next_step_number()

    # 如果输入被拦截 直接终止
    if (
        not is_resuming
        and _run_input_guardrails(agent, task, run_state, step_number, input_guardrails)
    ):
        record_run_stopped(run_state, step_number, "input_guardrail_triggered")
        emit_agent_end(lifecycle_hooks, context_wrapper, agent, run_state.final_answer)
        return _build_result_and_save_session(config, run_state)
    if not is_resuming:
        build_task_repo_context(task, context_wrapper)

    session_messages = _session_messages(config)
    if is_resuming and run_state.pending_tool_calls:
        resume_result = resume_pending_tool_approvals(
            agent,
            run_state,
            tool_use_behavior=effective_tool_use_behavior,
            hooks=lifecycle_hooks,
            finalize_output=not output_guardrails,
            trace_include_sensitive_data=(
                config.trace_include_sensitive_data if config is not None else True
            ),
            default_execution_limits=effective_tool_execution_limits,
            verification_policy=config.verification if config is not None else None,
            verification_runner=verification_runner,
        )
        if resume_result.has_pending_approvals:
            record_run_stopped(
                run_state,
                run_state.next_step_number(),
                "tool_approval_required",
            )
            emit_agent_end(
                lifecycle_hooks,
                context_wrapper,
                agent,
                run_state.final_answer,
            )
            return _build_result_and_save_session(config, run_state)

    while True:
        step_number = run_state.next_step_number()
        limit_reason = run_state.next_limit_reason()
        if limit_reason is not None:
            record_run_stopped(
                run_state,
                step_number,
                limit_reason,
            )
            break

        with turn_span(run_state.current_turn + 1, agent.name):
            turn_input = prepare_turn_input(
                agent,
                run_state.context_wrapper,
                model_settings=effective_model_settings,
            )
            turn_input = _prepend_session_messages(turn_input, session_messages)

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
                processed_response = process_model_turn(
                    agent,
                    model_turn,
                    run_state,
                    step_number,
                )
                execution_plan = build_tool_execution_plan(
                    agent,
                    processed_response,
                    run_state,
                )
                response_step = resolve_pending_approval_step(
                    model_turn,
                    execution_plan,
                ) or resolve_model_response_step(processed_response)
                tool_calls = _tool_calls_selected_for_execution(execution_plan)
            except Exception as exc:
                emit_error(lifecycle_hooks, context_wrapper, agent, exc)
                record_model_error(
                    agent,
                    str(exc),
                    run_state,
                    step_number,
                )
                break


            if response_step is not None:
                if isinstance(response_step.next_step, NextStepFinalOutput):
                    if _run_output_guardrails(
                        agent,
                        response_step.next_step.final_output,
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
                if isinstance(response_step.next_step, NextStepStopped):
                    record_run_stopped(
                        run_state,
                        step_number,
                        response_step.next_step.reason,
                    )
                    break
                if isinstance(response_step.next_step, NextStepPendingApproval):
                    record_run_stopped(
                        run_state,
                        step_number,
                        response_step.next_step.reason,
                    )
                    break

            handoff_happened = False
            guardrail_happened = False
            stopped_happened = False
            handoff_outcome = None
            handoff_step = None
            run_again_step = None
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
                    handoff_step = resolve_handoff_step(
                        model_turn,
                        handoff_outcome,
                        handoff_target,
                    )
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
                        default_execution_limits=effective_tool_execution_limits,
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
                        default_execution_limits=effective_tool_execution_limits,
                    )
                    continue

                run_verification_after_tool(
                    agent,
                    action,
                    run_state,
                    step_number,
                    config.verification if config is not None else None,
                    verification_runner,
                )

                '''
                tool_outcome.result_value 是候选 final answer
                先跑 output guardrails
                如果触发：停止，并且不提交 final_answer
                如果没触发 record_final_output 正式提交 final_answer
                '''
                
                tool_final_step = resolve_tool_final_output_step(model_turn, tool_outcome)
                if tool_final_step is not None:
                    if isinstance(tool_final_step.next_step, NextStepStopped):
                        stopped_happened = True
                        break
                    if isinstance(tool_final_step.next_step, NextStepFinalOutput):
                        if output_guardrails:
                            final_output = tool_final_step.next_step.final_output
                            if _run_output_guardrails(
                                agent,
                                final_output,
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
                                final_output,
                            )
                    break

                run_again_step = resolve_tool_run_again_step(model_turn, tool_outcome)

            if (
                handoff_step is not None
                and isinstance(handoff_step.next_step, NextStepHandoff)
                and handoff_outcome is not None
            ):
                if handoff_outcome.reached_final_answer:
                    run_state.last_agent = handoff_step.next_step.target_agent
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

            # 普通工具执行后，落成 NextStepRunAgain
            if (
                run_again_step is not None
                and isinstance(run_again_step.next_step, NextStepRunAgain)
            ):
                continue

            if (
                run_state.reached_final_answer
                or handoff_happened
                or guardrail_happened
                or stopped_happened
            ):
                break

    emit_agent_end(lifecycle_hooks, context_wrapper, agent, run_state.final_answer)
    return _build_result_and_save_session(config, run_state)
