from __future__ import annotations

import asyncio
import unittest

from agents.events import RunCompletedEvent, RunEvent, RunStartedEvent, utc_now
from agents.run_config import RunConfig
from agents.run_control import _EventChannel, _create_run_runtime


class EventChannelTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.runner = asyncio.Runner()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.runner.close()

    def test_requires_positive_integer_capacity(self) -> None:
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _EventChannel(value)  # type: ignore[arg-type]

    def test_rejects_non_event(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)
            with self.assertRaises(TypeError):
                await channel.put("not an event")  # type: ignore[arg-type]

        self.runner.run(scenario())

    def test_yields_events_in_order_and_stops_after_terminal(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(2)
            started = RunStartedEvent(
                run_id="run-1", sequence=1, timestamp=utc_now(), agent_name="Agent"
            )
            completed = RunCompletedEvent(
                run_id="run-1",
                sequence=2,
                timestamp=utc_now(),
                reached_final_answer=True,
            )

            await channel.put(started)
            await channel.put(completed)

            received = [event async for event in channel]

            self.assertEqual(received, [started, completed])
            with self.assertRaises(RuntimeError):
                await channel.put(started)

        self.runner.run(scenario())

    def test_full_channel_waits_until_consumer_takes_an_event(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)
            started = RunStartedEvent(
                run_id="run-1", sequence=1, timestamp=utc_now(), agent_name="Agent"
            )
            completed = RunCompletedEvent(
                run_id="run-1",
                sequence=2,
                timestamp=utc_now(),
                reached_final_answer=True,
            )
            producer_started = asyncio.Event()
            producer_finished = asyncio.Event()

            await channel.put(started)

            async def put_terminal() -> None:
                producer_started.set()
                await channel.put(completed)
                producer_finished.set()

            producer = asyncio.create_task(put_terminal())
            await producer_started.wait()

            self.assertFalse(producer_finished.is_set())
            self.assertEqual(channel.maxsize, 1)
            self.assertEqual(channel.qsize, 1)

            iterator = aiter(channel)
            self.assertIs(await anext(iterator), started)
            await producer
            self.assertIs(await anext(iterator), completed)
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)

        self.runner.run(scenario())

    def test_accepts_run_event_base_instance(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)
            event = RunEvent(run_id="run-1", sequence=1, timestamp=utc_now())
            await channel.put(event)
            self.assertEqual(channel.qsize, 1)

        self.runner.run(scenario())

    def test_closing_iterator_wakes_blocked_producer(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)
            events = [
                RunEvent(run_id="run-1", sequence=sequence, timestamp=utc_now())
                for sequence in range(1, 4)
            ]
            second_sent = asyncio.Event()
            third_started = asyncio.Event()

            await channel.put(events[0])

            async def produce() -> None:
                await channel.put(events[1])
                second_sent.set()
                third_started.set()
                await channel.put(events[2])

            producer = asyncio.create_task(produce())
            iterator = aiter(channel)
            self.assertIs(await anext(iterator), events[0])
            await second_sent.wait()
            await third_started.wait()

            await iterator.aclose()

            with self.assertRaisesRegex(RuntimeError, "consumer is closed"):
                await producer

        self.runner.run(scenario())

    def test_close_ends_consumer_and_is_idempotent(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)

            self.assertTrue(await channel.close())
            self.assertFalse(await channel.close())
            with self.assertRaisesRegex(RuntimeError, "channel is closed"):
                await channel.put(
                    RunEvent(run_id="run-1", sequence=1, timestamp=utc_now())
                )
            self.assertEqual([event async for event in channel], [])

        self.runner.run(scenario())

    def test_close_waits_for_space_without_leaking_sentinel(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)
            event = RunEvent(run_id="run-1", sequence=1, timestamp=utc_now())
            close_started = asyncio.Event()
            close_finished = asyncio.Event()

            await channel.put(event)

            async def close_channel() -> None:
                close_started.set()
                await channel.close()
                close_finished.set()

            closer = asyncio.create_task(close_channel())
            await close_started.wait()
            self.assertFalse(close_finished.is_set())

            received = [item async for item in channel]
            await closer

            self.assertEqual(received, [event])
            self.assertTrue(close_finished.is_set())

        self.runner.run(scenario())

    def test_terminal_does_not_add_close_sentinel(self) -> None:
        async def scenario() -> None:
            channel = _EventChannel(1)
            completed = RunCompletedEvent(
                run_id="run-1",
                sequence=1,
                timestamp=utc_now(),
                reached_final_answer=True,
            )

            await channel.put(completed)

            self.assertFalse(await channel.close())
            self.assertEqual([event async for event in channel], [completed])
            self.assertEqual(channel.qsize, 0)

        self.runner.run(scenario())

    def test_runtime_finishes_only_after_backpressured_terminal_is_queued(self) -> None:
        async def scenario() -> None:
            runtime = _create_run_runtime(RunConfig(event_queue_size=1))
            started = await runtime.emit(RunStartedEvent, agent_name="Agent")
            terminal_started = asyncio.Event()

            async def emit_terminal() -> RunCompletedEvent:
                terminal_started.set()
                return await runtime.emit(
                    RunCompletedEvent,
                    reached_final_answer=True,
                )

            terminal_task = asyncio.create_task(emit_terminal())
            await terminal_started.wait()
            self.assertFalse(runtime.controller.finished)

            iterator = aiter(runtime.events)
            self.assertIs(await anext(iterator), started)
            completed = await terminal_task

            self.assertTrue(runtime.controller.finished)
            self.assertIs(await anext(iterator), completed)
            with self.assertRaises(StopAsyncIteration):
                await anext(iterator)

        self.runner.run(scenario())


if __name__ == "__main__":
    unittest.main()
