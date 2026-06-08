from __future__ import annotations

import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import agents.run_steps as run_steps  # noqa: E402
from agents import (  # noqa: E402
    Agent,
    AgentMemory,
    CodeExecutionResult,
    FunctionTool,
    ModelCallError,
    ModelResponse,
    ModelResponseParseError,
    RunItem,
    RunState,
    ToolArgument,
    ToolCall,
    ToolRegistry,
    ToolSpec,
)
from agents.run_steps import (  # noqa: E402
    HandoffOutcome,
    ModelTurnResult,
    NextStepFinalOutput,
    NextStepHandoff,
    NextStepRunAgain,
    NextStepStopped,
    ProcessedResponse,
    SingleStepResult,
    ToolExecutionPlan,
    ToolExecutionOutcome,
    execute_handoff,
    execute_tool_call,
    interpret_tool_result,
    prepare_turn_input,
    process_model_turn,
    record_model_error,
    record_run_stopped,
    record_tool_call,
    record_tool_output,
    record_tool_error,
    resolve_final_output_step,
    resolve_handoff_step,
    resolve_model_response_step,
    resolve_no_tool_call_step,
    resolve_tool_final_output_step,
    resolve_tool_run_again_step,
    run_model_turn,
)


class ResponseModel:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.last_messages = []
        self.last_tool_specs = []

    def get_response(self, messages, tool_specs):
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        return self.response


class FailingResponseModel:
    def get_response(self, messages, tool_specs):
        raise ModelResponseParseError("Could not parse response output.")


class RecordingModel:
    def __init__(self) -> None:
        self.recorded_tool_outputs = []

    def record_tool_output(self, action, output) -> None:
        self.recorded_tool_outputs.append((action, output))


class DecisionModel:
    def __init__(self, actions) -> None:
        self.actions = list(actions)

    def decide(self, messages, tool_specs):
        if not self.actions:
            return None
        return self.actions.pop(0)


def echo_tool() -> FunctionTool:
    return FunctionTool(
        spec=ToolSpec(
            name="echo_text",
            description="Return the same text.",
            arguments=[
                ToolArgument(
                    name="text",
                    description="Input text.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        ),
        handler=lambda text: text,
    )


class RunStepsTestCase(unittest.TestCase):
    def test_step_and_plan_types_document_state_machine_contracts(self) -> None:
        docs = "\n".join(
            cls.__doc__ or ""
            for cls in (
                ProcessedResponse,
                ToolExecutionPlan,
                NextStepFinalOutput,
                NextStepRunAgain,
                NextStepHandoff,
                NextStepStopped,
            )
        )

        self.assertIn("model turn", docs)
        self.assertIn("execution plan", docs)
        self.assertIn("final output", docs)
        self.assertIn("run again", docs)
        self.assertIn("handoff", docs)
        self.assertIn("stop", docs)

    def test_next_step_dataclasses_expose_state_machine_payloads(self) -> None:
        target_agent = Agent(
            memory=AgentMemory(),
            model=DecisionModel([]),
            name="Helper",
        )

        final_step = NextStepFinalOutput(final_output="done")
        run_again_step = NextStepRunAgain()
        handoff_step = NextStepHandoff(target_agent=target_agent)
        step_result = SingleStepResult(
            model_turn=None,
            next_step=final_step,
        )

        self.assertEqual(final_step.final_output, "done")
        self.assertEqual(run_again_step.reason, "tool_results")
        self.assertIs(handoff_step.target_agent, target_agent)
        self.assertIs(step_result.next_step, final_step)
        self.assertEqual(step_result.generated_items, ())

    def test_process_model_turn_splits_tool_and_handoff_calls(self) -> None:
        target_agent = Agent(
            memory=AgentMemory(),
            model=DecisionModel([]),
            name="Helper",
        )
        agent = Agent(
            memory=AgentMemory(),
            model=DecisionModel([]),
            handoffs=[target_agent],
        )
        tool_call = ToolCall("echo_text", {"text": "hello"}, "call_tool")
        handoff_call = ToolCall(
            "transfer_to_helper",
            {"task": "handle this"},
            "call_handoff",
        )
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[tool_call, handoff_call],
        )
        model_turn = ModelTurnResult(
            response=model_response,
            tool_calls=[tool_call, handoff_call],
        )
        run_state = RunState(final_answer={"ok": True}, reached_final_answer=True)

        processed = process_model_turn(agent, model_turn, run_state, step_number=1)

        self.assertIsInstance(processed, ProcessedResponse)
        self.assertIs(processed.model_turn, model_turn)
        self.assertIs(processed.model_response, model_response)
        self.assertEqual(processed.tool_calls, [tool_call])
        self.assertEqual(processed.handoff_calls, [handoff_call])
        self.assertTrue(processed.has_final_output)
        self.assertEqual(processed.final_output, {"ok": True})

    def test_process_model_turn_records_plain_text_final_output(self) -> None:
        agent = Agent(memory=AgentMemory(), model=DecisionModel([]))
        model_response = ModelResponse(
            response_id="resp_text",
            output=[],
            output_text="Hello from the model.",
            tool_calls=[],
        )
        model_turn = ModelTurnResult(response=model_response, tool_calls=[])
        run_state = RunState()

        processed = process_model_turn(agent, model_turn, run_state, step_number=2)

        self.assertTrue(processed.has_final_output)
        self.assertEqual(processed.final_output, "Hello from the model.")
        self.assertTrue(run_state.reached_final_answer)
        self.assertEqual(run_state.final_answer, "Hello from the model.")
        self.assertEqual(run_state.new_items[-1].item_type, "final_output")
        self.assertEqual(run_state.new_items[-1].step_number, 2)
        self.assertEqual(run_state.new_items[-1].payload, "Hello from the model.")
        self.assertEqual(
            run_state.new_items[-1].metadata,
            {"source": run_steps.MODEL_OUTPUT_TEXT_FINAL_SOURCE},
        )

    def test_record_model_text_final_output_uses_stable_metadata(self) -> None:
        recorder = getattr(run_steps, "record_model_text_final_output", None)
        self.assertIsNotNone(recorder)
        assert recorder is not None
        run_state = RunState()

        recorder(run_state, step_number=3, final_answer="text final")

        self.assertTrue(run_state.reached_final_answer)
        self.assertEqual(run_state.final_answer, "text final")
        self.assertEqual(run_state.new_items[-1].item_type, "final_output")
        self.assertEqual(run_state.new_items[-1].step_number, 3)
        self.assertEqual(run_state.new_items[-1].payload, "text final")
        self.assertEqual(
            run_state.new_items[-1].metadata,
            {"source": run_steps.MODEL_OUTPUT_TEXT_FINAL_SOURCE},
        )

    def test_process_model_turn_keeps_structured_final_output_priority(self) -> None:
        agent = Agent(memory=AgentMemory(), model=DecisionModel([]))
        model_response = ModelResponse(
            response_id="resp_structured",
            output=[],
            output_text='{"answer": "plain text should not replace this"}',
            tool_calls=[],
        )
        model_turn = ModelTurnResult(response=model_response, tool_calls=[])
        run_state = RunState(
            final_answer={"answer": "structured"},
            reached_final_answer=True,
        )
        run_state.new_items.append(
            RunItem(
                item_type="final_output",
                step_number=1,
                payload={"answer": "structured"},
                metadata={"source": "structured_output"},
            )
        )

        processed = process_model_turn(agent, model_turn, run_state, step_number=1)

        self.assertTrue(processed.has_final_output)
        self.assertEqual(processed.final_output, {"answer": "structured"})
        self.assertEqual(len(run_state.new_items), 1)
        self.assertEqual(run_state.new_items[-1].metadata, {"source": "structured_output"})

    def test_process_model_turn_leaves_final_answer_tool_for_tool_execution(self) -> None:
        agent = Agent(memory=AgentMemory(), model=DecisionModel([]))
        final_answer_call = ToolCall(
            "final_answer",
            {"answer": "from tool"},
            "call_final",
        )
        model_response = ModelResponse(
            response_id="resp_tool",
            output=[],
            output_text="text should not bypass final_answer tool",
            tool_calls=[final_answer_call],
        )
        model_turn = ModelTurnResult(
            response=model_response,
            tool_calls=[final_answer_call],
        )
        run_state = RunState()

        processed = process_model_turn(agent, model_turn, run_state, step_number=1)

        self.assertFalse(processed.has_final_output)
        self.assertEqual(processed.tool_calls, [final_answer_call])
        self.assertFalse(run_state.reached_final_answer)
        self.assertEqual(run_state.new_items, [])

    def test_plain_text_final_output_identifies_chat_text_only(self) -> None:
        helper = getattr(run_steps, "_plain_text_final_output", None)
        self.assertIsNotNone(helper)
        assert helper is not None

        model_response = ModelResponse(
            response_id="resp_text",
            output=[],
            output_text="Hello from the model.",
            tool_calls=[],
        )
        tool_call = ToolCall("echo_text", {"text": "hello"}, "call_tool")

        self.assertEqual(
            helper(
                model_response,
                tool_calls=[],
                handoff_calls=[],
                has_final_output=False,
            ),
            "Hello from the model.",
        )
        self.assertIsNone(
            helper(
                model_response,
                tool_calls=[tool_call],
                handoff_calls=[],
                has_final_output=False,
            )
        )
        self.assertIsNone(
            helper(
                ModelResponse(
                    response_id="resp_blank",
                    output=[],
                    output_text="   ",
                    tool_calls=[],
                ),
                tool_calls=[],
                handoff_calls=[],
                has_final_output=False,
            )
        )
        self.assertIsNone(
            helper(
                model_response,
                tool_calls=[],
                handoff_calls=[],
                has_final_output=True,
            )
        )

    def test_resolve_final_output_step_returns_final_next_step(self) -> None:
        model_turn = ModelTurnResult(response=None, tool_calls=[])
        processed = ProcessedResponse(
            model_turn=model_turn,
            model_response=None,
            tool_calls=[],
            handoff_calls=[],
            final_output={"answer": "done"},
            has_final_output=True,
        )

        step_result = resolve_final_output_step(processed)

        self.assertIsNotNone(step_result)
        assert step_result is not None
        self.assertIs(step_result.model_turn, model_turn)
        self.assertIsInstance(step_result.next_step, NextStepFinalOutput)
        self.assertEqual(step_result.next_step.final_output, {"answer": "done"})
        self.assertEqual(step_result.generated_items, ())

    def test_resolve_model_response_step_prefers_final_output(self) -> None:
        model_turn = ModelTurnResult(response=None, tool_calls=[])
        processed = ProcessedResponse(
            model_turn=model_turn,
            model_response=None,
            tool_calls=[],
            handoff_calls=[],
            final_output="done",
            has_final_output=True,
        )

        step_result = resolve_model_response_step(processed)

        self.assertIsNotNone(step_result)
        assert step_result is not None
        self.assertIs(step_result.model_turn, model_turn)
        self.assertIsInstance(step_result.next_step, NextStepFinalOutput)
        self.assertEqual(step_result.next_step.final_output, "done")

    def test_resolve_no_tool_call_step_returns_stop_reason(self) -> None:
        model_turn = ModelTurnResult(response=None, tool_calls=[])
        processed = ProcessedResponse(
            model_turn=model_turn,
            model_response=None,
            tool_calls=[],
            handoff_calls=[],
        )

        step_result = resolve_no_tool_call_step(processed)

        self.assertIsNotNone(step_result)
        assert step_result is not None
        self.assertIs(step_result.model_turn, model_turn)
        self.assertIsInstance(step_result.next_step, NextStepStopped)
        self.assertEqual(step_result.next_step.reason, "model_returned_no_tool_call")
        self.assertEqual(step_result.generated_items, ())

    def test_resolve_tool_run_again_step_returns_run_again_for_non_final_tool(self) -> None:
        tool_call = ToolCall("echo_text", {"text": "hello"}, "call_1")
        model_turn = ModelTurnResult(response=None, tool_calls=[tool_call])
        tool_outcome = ToolExecutionOutcome(
            action=tool_call,
            result="hello",
            result_value="hello",
            observation="hello",
            is_final_answer=False,
            should_stop=False,
        )

        step_result = resolve_tool_run_again_step(model_turn, tool_outcome)

        self.assertIsNotNone(step_result)
        assert step_result is not None
        self.assertIs(step_result.model_turn, model_turn)
        self.assertIsInstance(step_result.next_step, NextStepRunAgain)
        self.assertEqual(step_result.next_step.reason, "tool_results")
        self.assertEqual(step_result.generated_items, ())

    def test_resolve_tool_final_output_step_returns_final_next_step(self) -> None:
        tool_call = ToolCall("final_answer", {"answer": "done"}, "call_1")
        model_turn = ModelTurnResult(response=None, tool_calls=[tool_call])
        tool_outcome = ToolExecutionOutcome(
            action=tool_call,
            result="done",
            result_value="done",
            observation="done",
            is_final_answer=True,
            should_stop=True,
        )

        step_result = resolve_tool_final_output_step(model_turn, tool_outcome)

        self.assertIsNotNone(step_result)
        assert step_result is not None
        self.assertIs(step_result.model_turn, model_turn)
        self.assertIsInstance(step_result.next_step, NextStepFinalOutput)
        self.assertEqual(step_result.next_step.final_output, "done")
        self.assertEqual(step_result.generated_items, ())

    def test_resolve_handoff_step_returns_handoff_next_step(self) -> None:
        target_agent = Agent(
            memory=AgentMemory(),
            model=DecisionModel([]),
            name="Helper",
        )
        handoff_call = ToolCall(
            "transfer_to_helper",
            {"task": "handle this"},
            "call_1",
        )
        model_turn = ModelTurnResult(response=None, tool_calls=[handoff_call])
        handoff_outcome = HandoffOutcome(
            action=handoff_call,
            target_agent_name="Helper",
            task="handle this",
            final_answer="done",
            reached_final_answer=True,
        )

        step_result = resolve_handoff_step(
            model_turn,
            handoff_outcome,
            target_agent,
        )

        self.assertIs(step_result.model_turn, model_turn)
        self.assertIsInstance(step_result.next_step, NextStepHandoff)
        self.assertIs(step_result.next_step.target_agent, target_agent)
        self.assertEqual(step_result.generated_items, ())

    def test_prepare_turn_input_collects_messages_and_tool_specs(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=ResponseModel(
                ModelResponse(
                    response_id=None,
                    output=[],
                    output_text=None,
                    tool_calls=[],
                )
            ),
            instructions="Answer briefly.",
            tool_registry=registry,
        )
        agent.memory.add_task("Echo hello.")

        turn_input = prepare_turn_input(agent)

        self.assertEqual(turn_input.messages[0].role, "system")
        self.assertEqual(turn_input.messages[0].content, "Answer briefly.")
        self.assertEqual(turn_input.messages[1].role, "user")
        self.assertEqual(turn_input.messages[1].content, "Echo hello.")
        self.assertIn("echo_text", [spec.name for spec in turn_input.tool_specs])

    def test_run_model_turn_records_model_response_and_returns_tool_calls(self) -> None:
        model_response = ModelResponse(
            response_id="resp_1",
            output=[],
            output_text=None,
            tool_calls=[
                ToolCall(
                    tool_name="echo_text",
                    arguments={"text": "hello"},
                    call_id="call_1",
                )
            ],
        )
        agent = Agent(
            memory=AgentMemory(),
            model=ResponseModel(model_response),
        )
        agent.memory.add_task("Echo hello.")
        run_state = RunState()
        turn_input = prepare_turn_input(agent)

        model_turn = run_model_turn(
            agent,
            turn_input,
            run_state,
            step_number=1,
        )

        self.assertIs(model_turn.response, model_response)
        self.assertEqual(model_turn.tool_calls, model_response.tool_calls)
        self.assertEqual(run_state.new_items[0].item_type, "model_response")
        self.assertIs(run_state.new_items[0].payload, model_response)

    def test_run_model_turn_wraps_model_response_errors_once(self) -> None:
        agent = Agent(
            memory=AgentMemory(),
            model=FailingResponseModel(),
        )
        agent.memory.add_task("Echo hello.")
        run_state = RunState()
        turn_input = prepare_turn_input(agent)

        with self.assertRaises(ModelCallError) as raised:
            run_model_turn(
                agent,
                turn_input,
                run_state,
                step_number=1,
            )

        self.assertIsInstance(raised.exception.original, ModelResponseParseError)
        self.assertEqual(
            str(raised.exception),
            "ModelResponseParseError: Could not parse response output.",
        )

    def test_record_model_error_records_error_item_and_memory_step(self) -> None:
        agent = Agent(memory=AgentMemory(), model=DecisionModel([]))
        run_state = RunState()

        record_model_error(
            agent,
            "model unavailable",
            run_state,
            step_number=1,
        )

        self.assertEqual(run_state.steps_taken, 0)
        self.assertEqual(run_state.new_items[0].item_type, "model_error")
        self.assertEqual(run_state.new_items[0].payload, "model unavailable")
        self.assertEqual(agent.memory.steps[0].error, "model unavailable")

    def test_record_run_stopped_records_stop_reason_without_counting_a_step(self) -> None:
        run_state = RunState()

        record_run_stopped(
            run_state,
            step_number=3,
            reason="max_steps_reached",
        )

        self.assertEqual(run_state.steps_taken, 0)
        self.assertEqual(run_state.new_items[0].item_type, "run_stopped")
        self.assertEqual(run_state.new_items[0].step_number, 3)
        self.assertEqual(run_state.new_items[0].payload, "max_steps_reached")

    def test_record_tool_call_records_model_requested_action(self) -> None:
        run_state = RunState()
        action = ToolCall(
            tool_name="echo_text",
            arguments={"text": "hello"},
            call_id="call_tool",
        )

        record_tool_call(run_state, action, step_number=2)

        self.assertEqual(run_state.steps_taken, 0)
        self.assertEqual(run_state.new_items[0].item_type, "tool_call")
        self.assertIs(run_state.new_items[0].payload, action)
        self.assertEqual(run_state.new_items[0].step_number, 2)

    def test_execute_tool_call_records_result_and_final_output_when_tool_should_stop(self) -> None:
        registry = ToolRegistry()
        registry.register(echo_tool())
        agent = Agent(
            memory=AgentMemory(),
            model=RecordingModel(),
            tool_registry=registry,
        )
        run_state = RunState()
        action = ToolCall(
            tool_name="echo_text",
            arguments={"text": "direct"},
            call_id="call_1",
        )

        outcome = execute_tool_call(
            agent,
            action,
            run_state,
            step_number=1,
            tool_use_behavior="stop_on_first_tool",
        )

        self.assertEqual(outcome.result_value, "direct")
        self.assertEqual(outcome.observation, "direct")
        self.assertTrue(outcome.should_stop)
        self.assertEqual(run_state.steps_taken, 1)
        self.assertTrue(run_state.reached_final_answer)
        self.assertEqual(run_state.final_answer, "direct")
        self.assertEqual(
            [item.item_type for item in run_state.new_items],
            ["tool_result", "final_output"],
        )
        self.assertEqual(agent.memory.steps[0].observation, "direct")
        self.assertTrue(agent.memory.steps[0].is_final_answer)

    def test_interpret_tool_result_extracts_code_execution_output_and_observation(self) -> None:
        action = ToolCall(
            tool_name="python_executor",
            arguments={"code": "final_answer(8)"},
            call_id="call_code",
        )

        result_info = interpret_tool_result(
            action,
            CodeExecutionResult(
                output=8,
                logs="stored 7\n",
                is_final_answer=True,
            ),
            tool_use_behavior="run_llm_again",
        )

        self.assertEqual(result_info.result_value, 8)
        self.assertIn("Execution logs:\nstored 7\n", result_info.observation)
        self.assertIn("Last output from code snippet:\n8", result_info.observation)
        self.assertTrue(result_info.is_final_answer)
        self.assertTrue(result_info.should_stop)

    def test_interpret_tool_result_applies_tool_stop_policies(self) -> None:
        action = ToolCall(
            tool_name="echo_text",
            arguments={"text": "direct"},
            call_id="call_policy",
        )

        keep_running = interpret_tool_result(
            action,
            "direct",
            tool_use_behavior="run_llm_again",
        )
        stop_on_first = interpret_tool_result(
            action,
            "direct",
            tool_use_behavior="stop_on_first_tool",
        )
        stop_by_name = interpret_tool_result(
            action,
            "direct",
            tool_use_behavior={"stop_at_tool_names": ["echo_text"]},
        )

        self.assertFalse(keep_running.should_stop)
        self.assertTrue(stop_on_first.should_stop)
        self.assertTrue(stop_by_name.should_stop)

    def test_record_tool_output_renders_code_execution_result_for_model(self) -> None:
        model = RecordingModel()
        action = ToolCall(
            tool_name="python_executor",
            arguments={"code": "final_answer(8)"},
            call_id="call_output",
        )

        record_tool_output(
            model,
            action,
            CodeExecutionResult(
                output=8,
                logs="stored 7\n",
                is_final_answer=True,
            ),
        )

        self.assertIs(model.recorded_tool_outputs[0][0], action)
        self.assertIn(
            "Execution logs:\nstored 7\n",
            model.recorded_tool_outputs[0][1],
        )

    def test_record_tool_error_records_error_and_model_visible_output(self) -> None:
        model = RecordingModel()
        agent = Agent(memory=AgentMemory(), model=model)
        run_state = RunState()
        action = ToolCall(
            tool_name="missing_tool",
            arguments={},
            call_id="call_error",
        )

        outcome = record_tool_error(
            agent,
            action,
            "bad args",
            run_state,
            step_number=1,
        )

        formatted_error = (
            "Tool 'missing_tool' observation\n"
            "status: error\n"
            "reason: ToolError\n"
            "detail: bad args"
        )
        self.assertEqual(outcome.error, formatted_error)
        self.assertEqual(run_state.steps_taken, 1)
        self.assertEqual(run_state.new_items[0].item_type, "tool_error")
        self.assertEqual(run_state.new_items[0].payload, formatted_error)
        self.assertEqual(
            run_state.new_items[0].metadata["tool_execution"]["reason"],
            "ToolError",
        )
        self.assertEqual(agent.memory.steps[0].error, formatted_error)
        self.assertEqual(model.recorded_tool_outputs[0], (action, formatted_error))

    def test_execute_handoff_records_handoff_and_final_output(self) -> None:
        target_agent = Agent(
            memory=AgentMemory(),
            model=DecisionModel(
                [
                    ToolCall(
                        tool_name="final_answer",
                        arguments={"answer": "4"},
                        call_id="final_call",
                    )
                ]
            ),
            name="Math Agent",
        )
        agent = Agent(
            memory=AgentMemory(),
            model=DecisionModel([]),
            name="Triage Agent",
            handoffs=[target_agent],
        )
        agent.memory.add_task("Need math.")
        run_state = RunState()
        action = ToolCall(
            tool_name="transfer_to_math_agent",
            arguments={"task": "Solve 2 + 2."},
            call_id="handoff_call",
        )

        outcome = execute_handoff(
            agent,
            action,
            target_agent,
            run_state,
            step_number=1,
        )

        self.assertEqual(outcome.target_agent_name, "Math Agent")
        self.assertEqual(outcome.task, "Solve 2 + 2.")
        self.assertEqual(outcome.final_answer, "4")
        self.assertTrue(outcome.reached_final_answer)
        self.assertEqual(target_agent.memory.task, "Solve 2 + 2.")
        self.assertEqual(run_state.steps_taken, 1)
        self.assertTrue(run_state.reached_final_answer)
        self.assertEqual(
            [item.item_type for item in run_state.new_items],
            ["handoff", "final_output"],
        )
        self.assertIn("Handoff to Math Agent returned", agent.memory.steps[0].observation)


if __name__ == "__main__":
    unittest.main()
