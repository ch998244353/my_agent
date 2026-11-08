from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from threading import get_ident
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID, uuid4

from agents.chat_backend import (
    Citation,
    ChatInputError,
    ChatPersistenceError,
    ChatRunError,
    ChatService,
    ConversationArchivedError,
    ConversationNotFoundError,
    RetrievalBundle,
    parse_citations,
)
from agents.chat_backend.conversation import StoredMessage
from agents.chat_backend.memory_extraction import (
    MemoryCandidate,
    MemoryExtractionService,
)
from agents.contracts import ChatMessage
from agents.context_chunks import ContextChunk
from agents.run_config import RunConfig


def run_result(answer: str | None, reached: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        final_output=answer,
        reached_final_answer=reached,
        new_items=(),
        raw_responses=(),
    )


def stored_message(
    conversation_id: UUID,
    sequence: int,
    message: ChatMessage,
) -> StoredMessage:
    return StoredMessage(
        id=uuid4(),
        conversation_id=conversation_id,
        sequence=sequence,
        role=message.role,
        content=message.content,
        created_at=datetime.now(UTC),
    )


class ChatServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conversation_id = uuid4()
        self.conversations = Mock()
        self.messages = Mock()
        self.agent = Mock()
        self.agent_factory = Mock(return_value=self.agent)

    def service(
        self,
        run_config: RunConfig | None = None,
        memory_extraction: MemoryExtractionService | None = None,
        memory_retrieval=None,
        hybrid_retrieval=None,
    ) -> ChatService:
        return ChatService(
            self.agent_factory,
            self.conversations,
            self.messages,
            run_config,
            memory_extraction,
            memory_retrieval,
            hybrid_retrieval,
        )

    def test_success_commits_once_off_loop_and_preserves_run_config(self) -> None:
        main_thread = get_ident()
        read_threads: list[int] = []
        commit_threads: list[int] = []
        captured = {}
        base_config = RunConfig(
            metadata={"request": "metadata"},
            tracing_disabled=True,
            max_steps=4,
        )

        def read_conversation(conversation_id):
            read_threads.append(get_ident())
            return SimpleNamespace(id=conversation_id, status="active")

        def commit(conversation_id, items):
            commit_threads.append(get_ident())
            return tuple(
                stored_message(conversation_id, index, item)
                for index, item in enumerate(items, start=1)
            )

        async def run(agent, content, config):
            captured["agent"] = agent
            captured["content"] = content
            captured["config"] = config
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        self.conversations.get.side_effect = read_conversation
        self.messages.add_batch.side_effect = commit
        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ):
            reply = asyncio.run(
                self.service(base_config).send_message(
                    self.conversation_id,
                    "  question  ",
                )
            )

        self.agent_factory.assert_called_once_with()
        self.assertIs(captured["agent"], self.agent)
        self.assertEqual(captured["content"], "question")
        self.assertEqual(captured["config"].metadata, {"request": "metadata"})
        self.assertTrue(captured["config"].tracing_disabled)
        self.assertEqual(captured["config"].max_steps, 4)
        self.assertIsNot(captured["config"].session, base_config.session)
        self.assertEqual(reply.conversation_id, self.conversation_id)
        self.assertEqual(reply.sequence, 2)
        self.assertEqual(reply.content, "answer")
        self.assertEqual(reply.citations, ())
        self.assertNotEqual(read_threads, [main_thread])
        self.assertNotEqual(commit_threads, [main_thread])
        self.messages.add_batch.assert_called_once()

    def test_memory_runs_after_commit_and_saves_off_the_event_loop(self) -> None:
        main_thread = get_ident()
        events: list[str] = []
        save_threads: list[int] = []
        stored: tuple[StoredMessage, ...] = ()

        def commit(conversation_id, items):
            nonlocal stored
            events.append("commit")
            stored = tuple(
                stored_message(conversation_id, index, item)
                for index, item in enumerate(items, start=1)
            )
            return stored

        async def run(_agent, content, config):
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        candidates = (
            MemoryCandidate("preference", "global", "Prefers Python", (), 0.9),
        )
        memory_extraction = Mock()

        async def extract(user_text, assistant_text):
            events.append("extract")
            self.assertEqual((user_text, assistant_text), ("question", "answer"))
            return candidates

        def save(source, saved_candidates):
            events.append("save")
            save_threads.append(get_ident())
            self.assertEqual(source.conversation_id, self.conversation_id)
            self.assertEqual(source.user_message_id, stored[0].id)
            self.assertEqual(source.assistant_message_id, stored[-1].id)
            self.assertIs(saved_candidates, candidates)

        memory_extraction.extract = AsyncMock(side_effect=extract)
        memory_extraction.save_candidates.side_effect = save
        self.messages.add_batch.side_effect = commit

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ):
            reply = asyncio.run(
                self.service(memory_extraction=memory_extraction).send_message(
                    self.conversation_id,
                    "question",
                )
            )
        events.append("reply")

        self.assertEqual(events, ["commit", "extract", "save", "reply"])
        self.assertNotEqual(save_threads, [main_thread])
        self.assertEqual(reply.message_id, stored[-1].id)
        self.assertEqual(reply.content, "answer")

    def test_hybrid_retrieval_adds_context_and_parses_after_commit(self) -> None:
        main_thread = get_ident()
        events: list[str] = []
        retrieval_threads: list[int] = []
        captured = {}
        caller_chunk = ContextChunk("caller", "user", "caller context", 46, "test")
        retrieved_chunk = ContextChunk(
            "hybrid_retrieval",
            "user",
            "[K1] knowledge context",
            46,
            "hybrid.retrieval",
        )
        citation = Citation("[K1]", "knowledge", uuid4(), uuid4(), "Guide")
        hybrid_retrieval = Mock()

        def retrieve(content, conversation_id):
            events.append("retrieval")
            retrieval_threads.append(get_ident())
            self.assertEqual(
                (content, conversation_id),
                ("question", self.conversation_id),
            )
            return RetrievalBundle((retrieved_chunk,), (citation,))

        def commit(conversation_id, items):
            events.append("commit")
            return tuple(
                stored_message(conversation_id, index, item)
                for index, item in enumerate(items, start=1)
            )

        def parse(answer, allowed):
            events.append("parse")
            return parse_citations(answer, allowed)

        async def run(_agent, content, config):
            events.append("runner")
            captured["config"] = config
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer [K1]"),
                ]
            )
            return run_result("answer [K1]")

        hybrid_retrieval.retrieve.side_effect = retrieve
        self.messages.add_batch.side_effect = commit
        with (
            patch(
                "agents.chat_backend.service.Runner.run_async",
                side_effect=run,
            ) as runner,
            patch(
                "agents.chat_backend.service.parse_citations",
                side_effect=parse,
            ) as parser,
        ):
            reply = asyncio.run(
                self.service(
                    RunConfig(context_chunks=(caller_chunk,)),
                    hybrid_retrieval=hybrid_retrieval,
                ).send_message(self.conversation_id, "question")
            )

        self.assertEqual(events, ["retrieval", "runner", "commit", "parse"])
        self.assertNotEqual(retrieval_threads, [main_thread])
        self.assertEqual(
            captured["config"].context_chunks,
            (caller_chunk, retrieved_chunk),
        )
        runner.assert_awaited_once()
        parser.assert_called_once_with("answer [K1]", (citation,))
        saved_items = self.messages.add_batch.call_args.args[1]
        self.assertEqual(
            [(item.role, item.content) for item in saved_items],
            [("user", "question"), ("assistant", "answer [K1]")],
        )
        self.assertEqual(reply.citations, (citation,))

    def test_old_and_hybrid_retrieval_cannot_both_be_enabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "mutually exclusive"):
            self.service(memory_retrieval=Mock(), hybrid_retrieval=Mock())

    def test_empty_hybrid_bundle_still_runs(self) -> None:
        hybrid_retrieval = Mock()
        hybrid_retrieval.retrieve.return_value = RetrievalBundle((), ())
        captured = {}

        async def run(_agent, content, config):
            captured["context_chunks"] = config.context_chunks
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        self.messages.add_batch.side_effect = lambda conversation_id, items: tuple(
            stored_message(conversation_id, index, item)
            for index, item in enumerate(items, start=1)
        )
        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ) as runner:
            reply = asyncio.run(
                self.service(hybrid_retrieval=hybrid_retrieval).send_message(
                    self.conversation_id,
                    "question",
                )
            )

        runner.assert_awaited_once()
        self.assertEqual(captured["context_chunks"], ())
        self.assertEqual(reply.citations, ())

    def test_retrieval_adds_current_turn_chunks_without_saving_them(self) -> None:
        main_thread = get_ident()
        events: list[str] = []
        retrieval_threads: list[int] = []
        captured = {}
        caller_chunk = ContextChunk(
            "caller", "user", "caller context", 46, "test"
        )
        retrieved_chunk = ContextChunk(
            "memory", "user", "retrieved memory", 47, "memory.retrieval"
        )
        memory_retrieval = Mock()

        def read_conversation(conversation_id):
            events.append("conversation")
            return SimpleNamespace(id=conversation_id, status="active")

        def retrieve(content, conversation_id):
            events.append("retrieval")
            retrieval_threads.append(get_ident())
            self.assertEqual((content, conversation_id), ("question", self.conversation_id))
            return (retrieved_chunk,)

        async def run(_agent, content, config):
            events.append("runner")
            captured["config"] = config
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        self.conversations.get.side_effect = read_conversation
        memory_retrieval.retrieve.side_effect = retrieve
        self.messages.add_batch.side_effect = lambda conversation_id, items: tuple(
            stored_message(conversation_id, index, item)
            for index, item in enumerate(items, start=1)
        )

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ) as runner:
            reply = asyncio.run(
                self.service(
                    RunConfig(context_chunks=(caller_chunk,)),
                    memory_retrieval=memory_retrieval,
                ).send_message(self.conversation_id, "question")
            )

        self.assertEqual(events[:3], ["conversation", "retrieval", "runner"])
        self.assertNotEqual(retrieval_threads, [main_thread])
        self.assertEqual(
            captured["config"].context_chunks,
            (caller_chunk, retrieved_chunk),
        )
        runner.assert_awaited_once()
        saved_items = self.messages.add_batch.call_args.args[1]
        self.assertEqual(
            [(item.role, item.content) for item in saved_items],
            [("user", "question"), ("assistant", "answer")],
        )
        self.assertEqual(reply.content, "answer")

    def test_retrieval_failure_stops_before_runner_and_persistence(self) -> None:
        for argument in ("memory_retrieval", "hybrid_retrieval"):
            with self.subTest(argument=argument):
                retrieval = Mock()
                failure = RuntimeError("secret database detail")
                retrieval.retrieve.side_effect = failure

                with patch("agents.chat_backend.service.Runner.run_async") as runner:
                    with self.assertRaises(ChatPersistenceError) as caught:
                        asyncio.run(
                            self.service(**{argument: retrieval}).send_message(
                                self.conversation_id,
                                "question",
                            )
                        )

                self.assertIs(caught.exception.__cause__, failure)
                self.assertNotIn("secret", str(caught.exception))
                runner.assert_not_awaited()
                self.messages.add_batch.assert_not_called()

    def test_memory_empty_failures_and_cancellation_keep_clear_boundaries(
        self,
    ) -> None:
        async def run(_agent, content, config):
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        self.messages.add_batch.side_effect = lambda conversation_id, items: tuple(
            stored_message(conversation_id, index, item)
            for index, item in enumerate(items, start=1)
        )
        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ):
            empty = Mock()
            empty.extract = AsyncMock(return_value=())
            reply = asyncio.run(
                self.service(memory_extraction=empty).send_message(
                    self.conversation_id,
                    "question",
                )
            )
            self.assertEqual(reply.content, "answer")
            empty.save_candidates.assert_not_called()

            failed = Mock()
            failed.extract = AsyncMock(
                side_effect=RuntimeError("secret user and assistant text")
            )
            with self.assertLogs(
                "agents.chat_backend.service",
                level="WARNING",
            ) as logs:
                reply = asyncio.run(
                    self.service(memory_extraction=failed).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
            self.assertEqual(reply.content, "answer")
            self.assertNotIn("secret", " ".join(logs.output))
            self.assertIn("RuntimeError", " ".join(logs.output))

            save_failed = Mock()
            save_failed.extract = AsyncMock(
                return_value=(
                    MemoryCandidate("fact", "global", "safe fact", (), 1.0),
                )
            )
            save_failed.save_candidates.side_effect = RuntimeError(
                "secret database detail"
            )
            with self.assertLogs(
                "agents.chat_backend.service",
                level="WARNING",
            ) as logs:
                reply = asyncio.run(
                    self.service(memory_extraction=save_failed).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
            self.assertEqual(reply.content, "answer")
            self.assertNotIn("secret", " ".join(logs.output))
            self.assertIn("RuntimeError", " ".join(logs.output))

            cancelled = Mock()
            cancelled.extract = AsyncMock(side_effect=asyncio.CancelledError)
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(
                    self.service(memory_extraction=cancelled).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
        self.assertEqual(self.messages.add_batch.call_count, 4)

    def test_input_and_missing_conversation_stop_before_runner(self) -> None:
        memory_retrieval = Mock()
        for content in ("", "   ", "x" * 20_001):
            with self.subTest(content_length=len(content)):
                with self.assertRaises(ChatInputError):
                    asyncio.run(
                        self.service(memory_retrieval=memory_retrieval).send_message(
                            self.conversation_id,
                            content,
                        )
                    )
        self.conversations.get.assert_not_called()

        missing = ConversationNotFoundError(self.conversation_id)
        self.conversations.get.side_effect = missing
        with self.assertRaises(ConversationNotFoundError) as caught:
            asyncio.run(
                self.service(memory_retrieval=memory_retrieval).send_message(
                    self.conversation_id,
                    "question",
                )
            )
        self.assertIs(caught.exception, missing)
        memory_retrieval.retrieve.assert_not_called()
        self.messages.add_batch.assert_not_called()

    def test_archived_conversation_stops_before_retrieval_and_runner(self) -> None:
        memory_retrieval = Mock()
        self.conversations.get.return_value = SimpleNamespace(
            id=self.conversation_id,
            status="archived",
        )

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            new_callable=AsyncMock,
        ) as run:
            with self.assertRaises(ConversationArchivedError) as caught:
                asyncio.run(
                    self.service(memory_retrieval=memory_retrieval).send_message(
                        self.conversation_id,
                        "question",
                    )
                )

        self.assertEqual(caught.exception.conversation_id, self.conversation_id)
        memory_retrieval.retrieve.assert_not_called()
        self.agent_factory.assert_not_called()
        run.assert_not_awaited()
        self.messages.add_batch.assert_not_called()

    def test_no_final_answer_and_cancellation_do_not_commit(self) -> None:
        memory_extraction = Mock()
        memory_extraction.extract = AsyncMock()

        async def no_final(agent, content, config):
            config.session.add_items([ChatMessage("user", content)])
            return run_result(None, reached=False)

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=no_final,
        ):
            with self.assertRaises(ChatRunError):
                asyncio.run(
                    self.service(memory_extraction=memory_extraction).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
        self.messages.add_batch.assert_not_called()

        async def cancelled(agent, content, config):
            config.session.add_items([ChatMessage("user", content)])
            raise asyncio.CancelledError

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=cancelled,
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(
                    self.service(memory_extraction=memory_extraction).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
        self.messages.add_batch.assert_not_called()
        memory_extraction.extract.assert_not_awaited()

    def test_commit_errors_are_safe_and_keep_the_original_cause(self) -> None:
        error = RuntimeError("secret SQL and credentials")
        self.messages.add_batch.side_effect = error
        memory_extraction = Mock()
        memory_extraction.extract = AsyncMock()

        async def run(agent, content, config):
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ):
            with self.assertRaises(ChatPersistenceError) as caught:
                asyncio.run(
                    self.service(memory_extraction=memory_extraction).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
        self.assertNotIn("secret", str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)
        memory_extraction.extract.assert_not_awaited()

    def test_saved_batch_must_end_with_the_final_assistant_message(self) -> None:
        memory_extraction = Mock()
        memory_extraction.extract = AsyncMock()

        async def run(agent, content, config):
            config.session.add_items([ChatMessage("user", content)])
            return run_result("answer")

        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=run,
        ):
            with self.assertRaises(ChatPersistenceError):
                asyncio.run(
                    self.service(memory_extraction=memory_extraction).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
        self.messages.add_batch.assert_not_called()
        memory_extraction.extract.assert_not_awaited()

        async def complete_run(agent, content, config):
            config.session.add_items(
                [
                    ChatMessage("user", content),
                    ChatMessage("assistant", "answer"),
                ]
            )
            return run_result("answer")

        self.messages.add_batch.return_value = (
            stored_message(
                self.conversation_id,
                1,
                ChatMessage("assistant", "answer"),
            ),
        )
        with patch(
            "agents.chat_backend.service.Runner.run_async",
            side_effect=complete_run,
        ):
            with self.assertRaises(ChatPersistenceError):
                asyncio.run(
                    self.service(memory_extraction=memory_extraction).send_message(
                        self.conversation_id,
                        "question",
                    )
                )
        memory_extraction.extract.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
