from __future__ import annotations

import asyncio
import math
import threading
import unittest
from datetime import timedelta

from agents.events import (
    RunAlreadyFinishedError,
    RunCancelledEvent,
    RunCancelledError,
    RunCompletedEvent,
    RunEventReducer,
    RunStartedEvent,
)
from agents.run_config import RunConfig
from agents.run_control import (
    CancellationToken,
    RunController,
    SteerMessage,
    _create_run_runtime,
)


class CancellationTokenTests(unittest.TestCase):
    def test_cancel_is_idempotent_and_keeps_first_request(self) -> None:
        token = CancellationToken("run-1")

        self.assertTrue(token.cancel("user"))
        requested_at = token.requested_at
        self.assertFalse(token.cancel("timeout"))

        self.assertTrue(token.cancelled)
        self.assertEqual(token.run_id, "run-1")
        self.assertEqual(token.reason, "user")
        self.assertEqual(token.requested_at, requested_at)
        self.assertIsNotNone(requested_at)
        self.assertEqual(requested_at.utcoffset(), timedelta(0))

    def test_wait_observes_reason_from_another_thread(self) -> None:
        token = CancellationToken("run-1")
        waiting = threading.Event()
        observed: list[tuple[bool, str | None]] = []

        def worker() -> None:
            waiting.set()
            observed.append((token.wait(1), token.reason))

        thread = threading.Thread(target=worker)
        thread.start()
        self.assertTrue(waiting.wait(1))
        token.cancel("user")
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(observed, [(True, "user")])

    def test_raise_if_cancelled_keeps_run_context(self) -> None:
        token = CancellationToken("run-7")
        token.raise_if_cancelled()
        token.cancel("user")

        with self.assertRaises(RunCancelledError) as raised:
            token.raise_if_cancelled()

        self.assertEqual(
            (raised.exception.run_id, raised.exception.reason),
            ("run-7", "user"),
        )

    def test_wait_times_out_without_cancelling(self) -> None:
        token = CancellationToken("run-1")

        self.assertFalse(token.wait(0))
        self.assertFalse(token.cancelled)

    def test_rejects_invalid_inputs(self) -> None:
        for run_id in (None, "", "   "):
            with self.subTest(run_id=run_id):
                with self.assertRaises(ValueError):
                    CancellationToken(run_id)  # type: ignore[arg-type]

        token = CancellationToken("run-1")
        for reason in (None, "", "   "):
            with self.subTest(reason=reason):
                with self.assertRaises(ValueError):
                    token.cancel(reason)  # type: ignore[arg-type]

        for timeout in (True, -1, math.nan, math.inf, "1"):
            with self.subTest(timeout=timeout):
                with self.assertRaises(ValueError):
                    token.wait(timeout)  # type: ignore[arg-type]


class SteerMessageTests(unittest.TestCase):
    def test_rejects_empty_fields(self) -> None:
        for steer_id, message in (("", "message"), ("steer-1", "   ")):
            with self.subTest(steer_id=steer_id, message=message):
                with self.assertRaises(ValueError):
                    SteerMessage(steer_id, message)


class RunControllerTests(unittest.TestCase):
    @staticmethod
    def terminal(run_id: str = "run-1") -> RunCompletedEvent:
        return RunEventReducer(run_id).emit(
            RunCompletedEvent,
            reached_final_answer=True,
        )

    def test_steers_are_numbered_and_drained_in_fifo_batches(self) -> None:
        controller = RunController("run-1", CancellationToken("run-1"))

        first = controller.steer("first")
        second = controller.steer("second")

        self.assertEqual(
            controller._drain_steers(),
            (first, second),
        )
        self.assertEqual(controller._drain_steers(), ())
        third = controller.steer("third")
        self.assertEqual(third.steer_id, "steer-3")
        self.assertEqual(controller._drain_steers(), (third,))

    def test_steer_after_drain_stays_for_the_next_batch(self) -> None:
        controller = RunController("run-1", CancellationToken("run-1"))
        first = controller.steer("first")
        ready = threading.Event()
        proceed = threading.Event()

        def enqueue_later() -> None:
            ready.set()
            proceed.wait()
            controller.steer("later")

        thread = threading.Thread(target=enqueue_later, daemon=True)
        thread.start()
        self.assertTrue(ready.wait(1))
        self.assertEqual(controller._drain_steers(), (first,))
        proceed.set()
        thread.join(1)

        self.assertFalse(thread.is_alive())
        self.assertEqual(
            [steer.message for steer in controller._drain_steers()],
            ["later"],
        )

    def test_cancel_forwards_to_the_token_while_active(self) -> None:
        token = CancellationToken("run-1")
        controller = RunController("run-1", token)

        self.assertTrue(controller.cancel())
        self.assertFalse(controller.cancel("timeout"))
        self.assertEqual(token.reason, "user")

    def test_cancel_wakeup_runs_once_and_covers_early_cancel(self) -> None:
        controller = RunController("run-1", CancellationToken("run-1"))
        wakeups: list[str] = []
        controller._bind_cancel_wakeup(lambda: wakeups.append("wake"))

        self.assertTrue(controller.cancel("user"))
        self.assertFalse(controller.cancel("again"))
        self.assertEqual(wakeups, ["wake"])
        with self.assertRaisesRegex(RuntimeError, "already bound"):
            controller._bind_cancel_wakeup(lambda: None)

        early = RunController("run-2", CancellationToken("run-2"))
        self.assertTrue(early.cancel("early"))
        early_wakeups: list[str] = []
        early._bind_cancel_wakeup(lambda: early_wakeups.append("wake"))
        self.assertEqual(early_wakeups, ["wake"])

    def test_finish_is_idempotent_and_rejects_late_operations(self) -> None:
        token = CancellationToken("run-1")
        controller = RunController("run-1", token)
        controller.steer("pending")
        terminal = self.terminal()

        self.assertTrue(controller._finish(terminal))
        self.assertFalse(controller._finish(terminal))
        self.assertTrue(controller.finished)
        self.assertIs(controller.terminal_event, terminal)
        self.assertEqual(controller.pending_steer_count, 0)
        self.assertFalse(controller.cancel())
        self.assertFalse(token.cancelled)
        with self.assertRaises(RunAlreadyFinishedError):
            controller.steer("late")
        with self.assertRaises(RuntimeError):
            controller._drain_steers()

    def test_validates_run_ownership_and_terminal_type(self) -> None:
        token = CancellationToken("run-1")
        with self.assertRaises(ValueError):
            RunController("run-2", token)
        with self.assertRaises(TypeError):
            RunController("run-1", object())  # type: ignore[arg-type]

        controller = RunController("run-1", token)
        with self.assertRaises(TypeError):
            controller._finish(object())  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            controller._finish(self.terminal("run-2"))

    def test_controllers_do_not_share_steer_numbers(self) -> None:
        first = RunController("run-1", CancellationToken("run-1"))
        second = RunController("run-2", CancellationToken("run-2"))

        self.assertEqual(first.steer("one").steer_id, "steer-1")
        self.assertEqual(second.steer("two").steer_id, "steer-1")


class RunRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = asyncio.Runner()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.runner.close()

    def test_factory_isolates_runs_and_uses_config(self) -> None:
        async def scenario() -> None:
            first = _create_run_runtime(
                RunConfig(event_queue_size=2, cancellation_grace_period=0.25)
            )
            second = _create_run_runtime()

            self.assertNotEqual(first.run_id, second.run_id)
            self.assertIsNot(first.reducer, second.reducer)
            self.assertIsNot(first.token, second.token)
            self.assertIsNot(first.controller, second.controller)
            self.assertIsNot(first.events, second.events)
            self.assertIs(first.controller.token, first.token)
            self.assertEqual(first.events.maxsize, 2)
            self.assertEqual(first.cancellation_grace_period, 0.25)
            self.assertEqual(second.events.maxsize, 100)
            self.assertEqual(second.cancellation_grace_period, 0.1)

            first_event = await first.emit(RunStartedEvent, agent_name="First")
            second_event = await second.emit(RunStartedEvent, agent_name="Second")
            self.assertEqual(first_event.sequence, 1)
            self.assertEqual(second_event.sequence, 1)

        self.runner.run(scenario())

    def test_emit_numbers_events_and_finishes_after_terminal_is_queued(self) -> None:
        async def scenario() -> None:
            runtime = _create_run_runtime(RunConfig(event_queue_size=2))

            started = await runtime.emit(RunStartedEvent, agent_name="Agent")
            self.assertFalse(runtime.controller.finished)
            completed = await runtime.emit(
                RunCompletedEvent,
                reached_final_answer=True,
            )

            self.assertEqual((started.sequence, completed.sequence), (1, 2))
            self.assertTrue(runtime.controller.finished)
            self.assertIs(runtime.controller.terminal_event, completed)
            self.assertEqual(
                [event async for event in runtime.events],
                [started, completed],
            )

        self.runner.run(scenario())

    def test_terminal_finishes_controller_when_consumer_is_closed(self) -> None:
        async def scenario() -> None:
            runtime = _create_run_runtime(RunConfig(event_queue_size=1))
            started = await runtime.emit(RunStartedEvent, agent_name="Agent")
            iterator = aiter(runtime.events)
            self.assertIs(await anext(iterator), started)
            await iterator.aclose()

            with self.assertRaisesRegex(RuntimeError, "channel is closed"):
                await runtime.emit(
                    RunCompletedEvent,
                    reached_final_answer=True,
                )

            self.assertTrue(runtime.controller.finished)
            self.assertIs(
                runtime.controller.terminal_event,
                runtime.reducer.terminal_event,
            )

        self.runner.run(scenario())

    def test_shutdown_requires_cancellation(self) -> None:
        async def scenario() -> None:
            runtime = _create_run_runtime()
            producer = asyncio.create_task(asyncio.sleep(0))
            with self.assertRaisesRegex(RuntimeError, "must be cancelled"):
                await runtime.shutdown_after_cancel(producer)
            await producer

        self.runner.run(scenario())

    def test_shutdown_allows_cooperative_exit_and_emits_terminal(self) -> None:
        async def scenario() -> None:
            runtime = _create_run_runtime(
                RunConfig(event_queue_size=1, cancellation_grace_period=1)
            )
            started = asyncio.Event()
            proceed = asyncio.Event()

            async def produce() -> None:
                started.set()
                await proceed.wait()
                runtime.token.raise_if_cancelled()

            producer = asyncio.create_task(produce())
            await started.wait()
            self.assertTrue(runtime.controller.cancel("user"))
            proceed.set()

            await runtime.shutdown_after_cancel(producer)

            self.assertTrue(producer.done())
            self.assertFalse(producer.cancelled())
            self.assertIsInstance(runtime.reducer.terminal_event, RunCancelledEvent)
            self.assertEqual(
                [event async for event in runtime.events],
                [runtime.reducer.terminal_event],
            )
            self.assertTrue(runtime.controller.finished)

        self.runner.run(scenario())

    def test_shutdown_cancels_task_after_zero_grace_period(self) -> None:
        async def scenario() -> None:
            runtime = _create_run_runtime(
                RunConfig(event_queue_size=1, cancellation_grace_period=0)
            )
            started = asyncio.Event()
            cleaned_up = asyncio.Event()

            async def produce() -> None:
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cleaned_up.set()

            producer = asyncio.create_task(produce())
            await started.wait()
            runtime.controller.cancel("timeout")

            await runtime.shutdown_after_cancel(producer)

            self.assertTrue(producer.cancelled())
            self.assertTrue(cleaned_up.is_set())
            terminal = runtime.reducer.terminal_event
            self.assertIsInstance(terminal, RunCancelledEvent)
            assert isinstance(terminal, RunCancelledEvent)
            self.assertEqual(terminal.reason, "timeout")

        self.runner.run(scenario())


if __name__ == "__main__":
    unittest.main()
