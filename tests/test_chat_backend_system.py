from __future__ import annotations

import asyncio
import os
import socket
import threading
import time
import unittest
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
import uvicorn
from psycopg import OperationalError, connect, sql

from agents import Agent, AgentMemory, ToolCall
from agents.chat_backend import AppSettings, create_app
from agents.chat_backend.embedding import EMBEDDING_DIMENSIONS, EmbeddingBatch
from agents.chat_backend.memory import embed_missing_memories
from agents.contracts import ChatMessage


_POSTGRES_DSN = os.getenv("MYAGENT_TEST_POSTGRES_DSN")
_EMBEDDING = (1.0,) + (0.0,) * (EMBEDDING_DIMENSIONS - 1)


class RecordingModel:
    def __init__(self, answers: Sequence[str]) -> None:
        self._answers = iter(answers)
        self.message_batches: list[list[ChatMessage]] = []

    def decide(self, messages, _tool_specs):
        self.message_batches.append(list(messages))
        return ToolCall(
            "final_answer",
            {"answer": next(self._answers)},
            uuid4().hex,
        )


class DeterministicEmbeddingClient:
    def __init__(self) -> None:
        self.batches: list[tuple[str, ...]] = []

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        batch = tuple(texts)
        self.batches.append(batch)
        return tuple(_EMBEDDING for _ in batch)


class FailingModel:
    def decide(self, _messages, _tool_specs):
        raise RuntimeError("secret model failure")


def _real_service_smoke_enabled() -> bool:
    return os.getenv("MYAGENT_RUN_REAL_SERVICE_SMOKE") == "1"


def _free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


@contextmanager
def _run_uvicorn(app) -> Iterator[str]:
    port = _free_local_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.run()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    failed = False
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if errors or not thread.is_alive():
                raise RuntimeError("Uvicorn failed to start") from (
                    errors[0] if errors else None
                )
            if time.monotonic() >= deadline:
                raise TimeoutError("Uvicorn did not start within 10 seconds")
            time.sleep(0.01)
        yield f"http://127.0.0.1:{port}"
    except BaseException:
        failed = True
        raise
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        if not failed:
            if thread.is_alive():
                raise RuntimeError("Uvicorn did not stop within 10 seconds")
            if errors:
                raise RuntimeError("Uvicorn stopped with an error") from errors[0]


def _make_app(
    dsn: str,
    model: object,
    embedding_client: DeterministicEmbeddingClient | None = None,
    *,
    memory_agent_factory: Callable[[], Agent] | None = None,
):
    settings = AppSettings(postgres_dsn=dsn)
    app = create_app(
        settings,
        lambda: Agent(memory=AgentMemory(), model=model),
        memory_agent_factory=memory_agent_factory,
        embedding_client=embedding_client,
    )
    return app


@unittest.skipUnless(_POSTGRES_DSN, "requires MYAGENT_TEST_POSTGRES_DSN")
class OfflineSystemScenarioTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        assert _POSTGRES_DSN is not None
        parts = urlsplit(_POSTGRES_DSN)
        self.database_name = f"myagent_system_{uuid4().hex}"
        self.admin_dsn = urlunsplit(parts._replace(path="/postgres"))
        self.test_dsn = urlunsplit(parts._replace(path=f"/{self.database_name}"))
        with connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(self.database_name)
                )
            )
        self.addCleanup(self.drop_database)

    def drop_database(self) -> None:
        with connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database_name)
                )
            )

    @unittest.skipUnless(
        _real_service_smoke_enabled(),
        "requires MYAGENT_RUN_REAL_SERVICE_SMOKE=1",
    )
    async def test_m06_real_postgres_http_smoke(self) -> None:
        embedding_client = DeterministicEmbeddingClient()
        first_model = RecordingModel(("first answer", "second answer"))
        first_app = _make_app(self.test_dsn, first_model, embedding_client)

        with _run_uvicorn(first_app) as base_url:
            async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
                health = await client.get("/health")
                self.assertEqual(health.status_code, 200)
                self.assertEqual(health.json()["status"], "ready")

                created = await client.post(
                    "/conversations",
                    json={"title": "Before real restart"},
                )
                self.assertEqual(created.status_code, 201)
                conversation_id = UUID(created.json()["id"])

                renamed = await client.patch(
                    f"/conversations/{conversation_id}",
                    json={"title": "Real service conversation"},
                )
                self.assertEqual(renamed.status_code, 200)
                self.assertEqual(
                    renamed.json()["title"],
                    "Real service conversation",
                )

                first_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "first real request"},
                )
                second_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "second real request"},
                )
                self.assertEqual(first_reply.json()["content"], "first answer")
                self.assertEqual(second_reply.json()["content"], "second answer")

        memory_text = "M06SERVICE-MEMORY：紫罗兰方案来自长期记忆。"
        knowledge_text = "M06SERVICE-KNOWLEDGE：紫罗兰方案来自知识原文。"
        second_model = RecordingModel(
            (
                "restart answer",
                "真实 TCP 同时引用记忆 [M1] 和知识 [K1]。",
                "恢复后只引用知识 [K1]。",
            )
        )
        second_app = _make_app(self.test_dsn, second_model, embedding_client)

        with _run_uvicorn(second_app) as base_url:
            async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
                health = await client.get("/health")
                self.assertEqual(health.status_code, 200)

                restarted = await client.get(f"/conversations/{conversation_id}")
                self.assertEqual(restarted.status_code, 200)
                self.assertEqual(
                    restarted.json()["title"],
                    "Real service conversation",
                )
                restart_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "request after restart"},
                )
                self.assertEqual(restart_reply.json()["content"], "restart answer")

                history = await client.get(
                    f"/conversations/{conversation_id}/messages",
                    params={"page_size": 100},
                )
                history_items = history.json()["items"]
                sequences = [item["sequence"] for item in history_items]
                self.assertEqual(
                    sequences,
                    list(range(1, len(sequences) + 1)),
                )
                self.assertGreater(
                    restart_reply.json()["sequence"],
                    second_reply.json()["sequence"],
                )
                user_message = next(
                    item
                    for item in reversed(history_items)
                    if item["role"] == "user"
                )
                assistant_message = next(
                    item
                    for item in reversed(history_items)
                    if item["role"] == "assistant"
                )

                memory_created = await client.post(
                    "/memories",
                    json={
                        "kind": "fact",
                        "scope": "global",
                        "content": "M06SERVICE-MEMORY：紫罗兰初始记忆。",
                        "source": {
                            "conversation_id": str(conversation_id),
                            "user_message_id": user_message["id"],
                            "assistant_message_id": assistant_message["id"],
                        },
                        "tags": ["m06-service"],
                        "confidence": 0.9,
                    },
                )
                self.assertEqual(memory_created.status_code, 201)
                memory_id = UUID(memory_created.json()["record"]["id"])
                memory_updated = await client.patch(
                    f"/memories/{memory_id}",
                    json={"content": memory_text, "confidence": 1.0},
                )
                self.assertEqual(memory_updated.status_code, 200)
                self.assertEqual(memory_updated.json()["content"], memory_text)

                knowledge_created = await client.post(
                    "/knowledge",
                    json={
                        "title": "Real TCP violet guide",
                        "text": knowledge_text,
                        "metadata": {"lesson": 9},
                    },
                )
                self.assertEqual(knowledge_created.status_code, 201)
                knowledge_id = UUID(knowledge_created.json()["id"])
                reindexed = await client.post(
                    f"/knowledge/{knowledge_id}/reindex"
                )
                self.assertEqual(reindexed.status_code, 200)
                self.assertEqual(reindexed.json()["chunk_count"], 1)

                retrieval_conversation = await client.post(
                    "/conversations",
                    json={"title": "Real retrieval conversation"},
                )
                retrieval_id = UUID(retrieval_conversation.json()["id"])
                retrieval_reply = await client.post(
                    f"/conversations/{retrieval_id}/messages",
                    json={"content": "紫罗兰"},
                )
                self.assertEqual(retrieval_reply.status_code, 200)
                self.assertEqual(
                    [
                        (citation["marker"], citation["source_type"])
                        for citation in retrieval_reply.json()["citations"]
                    ],
                    [("[M1]", "memory"), ("[K1]", "knowledge")],
                )
                retrieval_input = "\n".join(
                    message.content for message in second_model.message_batches[1]
                )
                self.assertIn(memory_text, retrieval_input)
                self.assertIn(knowledge_text, retrieval_input)

                disabled = await client.post(f"/memories/{memory_id}/disable")
                self.assertEqual(disabled.status_code, 200)
                self.assertEqual(disabled.json()["status"], "disabled")

                archived = await client.patch(
                    f"/conversations/{retrieval_id}",
                    json={"status": "archived"},
                )
                self.assertEqual(archived.json()["status"], "archived")
                blocked = await client.post(
                    f"/conversations/{retrieval_id}/messages",
                    json={"content": "must not run"},
                )
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(blocked.json()["code"], "conversation_archived")

                restored = await client.patch(
                    f"/conversations/{retrieval_id}",
                    json={"status": "active"},
                )
                self.assertEqual(restored.json()["status"], "active")
                restored_reply = await client.post(
                    f"/conversations/{retrieval_id}/messages",
                    json={"content": "紫罗兰"},
                )
                self.assertEqual(restored_reply.status_code, 200)
                self.assertEqual(
                    [
                        citation["source_type"]
                        for citation in restored_reply.json()["citations"]
                    ],
                    ["knowledge"],
                )

                deleted = await client.delete(f"/conversations/{retrieval_id}")
                self.assertEqual(deleted.status_code, 204)
                missing = await client.get(f"/conversations/{retrieval_id}")
                self.assertEqual(missing.status_code, 404)

    async def test_m06_offline_system_scenario(self) -> None:
        memory_text = "M06MEMORY-UPDATED：紫罗兰方案来自长期记忆。"
        knowledge_text = "M06KNOWLEDGE-ONLY：紫罗兰方案来自知识原文。"
        model = RecordingModel(
            (
                "first answer",
                "second answer",
                "restart answer",
                "先采用记忆 [M1]，再核对知识 [K1]；忽略 [M9]。",
                "停用记忆后只引用知识 [K1]，忽略 [M1]。",
                "恢复后继续引用知识 [K1]。",
            )
        )
        embedding_client = DeterministicEmbeddingClient()

        first_app = _make_app(self.test_dsn, model, embedding_client)
        async with first_app.router.lifespan_context(first_app):
            transport = httpx.ASGITransport(app=first_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                created = await client.post(
                    "/conversations",
                    json={"title": "Before restart"},
                )
                conversation_id = UUID(created.json()["id"])
                renamed = await client.patch(
                    f"/conversations/{conversation_id}",
                    json={"title": "Persistent conversation"},
                )
                first_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "first question"},
                )
                second_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "second question"},
                )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(renamed.json()["title"], "Persistent conversation")
        self.assertEqual(first_reply.json()["content"], "first answer")
        self.assertEqual(second_reply.json()["content"], "second answer")

        second_app = _make_app(self.test_dsn, model, embedding_client)
        async with second_app.router.lifespan_context(second_app):
            transport = httpx.ASGITransport(app=second_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                restarted = await client.get(f"/conversations/{conversation_id}")
                restart_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "restart question"},
                )
                history = await client.get(
                    f"/conversations/{conversation_id}/messages",
                    params={"page_size": 100},
                )
                history_items = history.json()["items"]
                user_message = next(
                    item
                    for item in reversed(history_items)
                    if item["role"] == "user"
                )
                assistant_message = next(
                    item
                    for item in reversed(history_items)
                    if item["role"] == "assistant"
                )

                memory_created = await client.post(
                    "/memories",
                    json={
                        "kind": "fact",
                        "scope": "global",
                        "content": "M06MEMORY-ONLY：紫罗兰初始记忆。",
                        "source": {
                            "conversation_id": str(conversation_id),
                            "user_message_id": user_message["id"],
                            "assistant_message_id": assistant_message["id"],
                        },
                        "tags": ["m06"],
                        "confidence": 0.9,
                    },
                )
                memory_id = UUID(memory_created.json()["record"]["id"])
                memory_updated = await client.patch(
                    f"/memories/{memory_id}",
                    json={"content": memory_text, "confidence": 1.0},
                )
                embedded_count = await asyncio.to_thread(
                    embed_missing_memories,
                    second_app.state.memory_store,
                    embedding_client,
                    second_app.state.settings.embedding_model,
                )

                knowledge_created = await client.post(
                    "/knowledge",
                    json={
                        "title": "Violet guide",
                        "text": knowledge_text,
                        "metadata": {"lesson": 7},
                    },
                )
                knowledge_id = UUID(knowledge_created.json()["id"])
                reindexed = await client.post(
                    f"/knowledge/{knowledge_id}/reindex"
                )
                indexed_source = await client.get(f"/knowledge/{knowledge_id}")

                active = await client.post(
                    "/conversations",
                    json={"title": "Retrieval conversation"},
                )
                active_id = UUID(active.json()["id"])

                memory_keyword = Mock(
                    wraps=second_app.state.memory_store.search_keyword
                )
                memory_vector = Mock(
                    wraps=second_app.state.memory_store.search_vector
                )
                knowledge_keyword = Mock(
                    wraps=second_app.state.knowledge_store.search_keyword
                )
                knowledge_vector = Mock(
                    wraps=second_app.state.knowledge_store.search_vector
                )
                second_app.state.memory_store.search_keyword = memory_keyword
                second_app.state.memory_store.search_vector = memory_vector
                second_app.state.knowledge_store.search_keyword = knowledge_keyword
                second_app.state.knowledge_store.search_vector = knowledge_vector

                embedding_calls = len(embedding_client.batches)
                retrieval_reply = await client.post(
                    f"/conversations/{active_id}/messages",
                    json={"content": "紫罗兰"},
                )
                first_query_batches = embedding_client.batches[embedding_calls:]
                first_route_counts = (
                    memory_keyword.call_count,
                    memory_vector.call_count,
                    knowledge_keyword.call_count,
                    knowledge_vector.call_count,
                )
                retrieval_history = await client.get(
                    f"/conversations/{active_id}/messages",
                    params={"page_size": 100},
                )

                disabled = await client.post(f"/memories/{memory_id}/disable")
                disabled_reply = await client.post(
                    f"/conversations/{active_id}/messages",
                    json={"content": "紫罗兰"},
                )
                archived = await client.patch(
                    f"/conversations/{active_id}",
                    json={"status": "archived"},
                )
                blocked = await client.post(
                    f"/conversations/{active_id}/messages",
                    json={"content": "must not run"},
                )
                blocked_embedding_calls = len(embedding_client.batches)
                restored = await client.patch(
                    f"/conversations/{active_id}",
                    json={"status": "active"},
                )
                restored_reply = await client.post(
                    f"/conversations/{active_id}/messages",
                    json={"content": "紫罗兰"},
                )

        self.assertEqual(restarted.json()["title"], "Persistent conversation")
        self.assertEqual(restart_reply.json()["content"], "restart answer")
        sequences = [item["sequence"] for item in history_items]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
        self.assertGreater(
            restart_reply.json()["sequence"],
            second_reply.json()["sequence"],
        )

        self.assertEqual(memory_created.status_code, 201)
        self.assertEqual(memory_updated.json()["content"], memory_text)
        self.assertEqual(
            memory_updated.json()["source"]["conversation_id"],
            str(conversation_id),
        )
        self.assertEqual(embedded_count, 1)
        self.assertEqual(reindexed.status_code, 200)
        self.assertEqual(reindexed.json()["chunk_count"], 1)
        self.assertEqual(indexed_source.json()["status"], "indexed")

        self.assertEqual(retrieval_reply.status_code, 200)
        self.assertEqual(
            [
                (item["marker"], item["source_type"])
                for item in retrieval_reply.json()["citations"]
            ],
            [("[M1]", "memory"), ("[K1]", "knowledge")],
        )
        self.assertEqual(
            retrieval_reply.json()["citations"][0]["item_id"],
            str(memory_id),
        )
        self.assertEqual(
            retrieval_reply.json()["citations"][1]["source_id"],
            str(knowledge_id),
        )
        self.assertEqual(first_query_batches, [("紫罗兰",)])
        self.assertEqual(first_route_counts, (1, 1, 1, 1))

        first_retrieval_input = "\n".join(
            message.content for message in model.message_batches[3]
        )
        self.assertIn(memory_text, first_retrieval_input)
        self.assertIn(knowledge_text, first_retrieval_input)
        self.assertIn("[M1]", first_retrieval_input)
        self.assertIn("[K1]", first_retrieval_input)
        persisted_contents = [
            item["content"] for item in retrieval_history.json()["items"]
        ]
        self.assertNotIn(memory_text, persisted_contents)
        self.assertNotIn(knowledge_text, persisted_contents)

        self.assertEqual(disabled.json()["status"], "disabled")
        self.assertEqual(
            [
                (item["marker"], item["source_type"])
                for item in disabled_reply.json()["citations"]
            ],
            [("[K1]", "knowledge")],
        )
        disabled_input = "\n".join(
            message.content for message in model.message_batches[4]
        )
        self.assertNotIn(memory_text, disabled_input)
        self.assertIn(knowledge_text, disabled_input)

        self.assertEqual(archived.json()["status"], "archived")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["code"], "conversation_archived")
        self.assertEqual(blocked_embedding_calls, embedding_calls + 2)
        self.assertEqual(restored.json()["status"], "active")
        self.assertEqual(restored_reply.status_code, 200)
        self.assertEqual(
            [item["source_type"] for item in restored_reply.json()["citations"]],
            ["knowledge"],
        )
        self.assertEqual(len(model.message_batches), 6)

    async def test_m06_fault_boundaries_keep_committed_state_consistent(
        self,
    ) -> None:
        failing_app = _make_app(self.test_dsn, FailingModel())
        async with failing_app.router.lifespan_context(failing_app):
            transport = httpx.ASGITransport(
                app=failing_app,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                created = await client.post("/conversations")
                conversation_id = UUID(created.json()["id"])
                failed_chat = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "must not be committed"},
                )
                empty_history = await client.get(
                    f"/conversations/{conversation_id}/messages"
                )

        extraction_app = _make_app(
            self.test_dsn,
            RecordingModel(("committed answer",)),
            memory_agent_factory=lambda: Agent(
                memory=AgentMemory(),
                model=FailingModel(),
            ),
        )
        extraction = extraction_app.state.memory_extraction
        assert extraction is not None
        extraction.extract = AsyncMock(
            side_effect=RuntimeError("secret extraction failure")
        )

        async with extraction_app.router.lifespan_context(extraction_app):
            transport = httpx.ASGITransport(
                app=extraction_app,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                with self.assertLogs(
                    "agents.chat_backend.service",
                    level="WARNING",
                ) as logs:
                    committed_chat = await client.post(
                        f"/conversations/{conversation_id}/messages",
                        json={"content": "keep this successful turn"},
                    )
                committed_history = await client.get(
                    f"/conversations/{conversation_id}/messages"
                )
                memories = await client.get("/memories")

                list_conversations = extraction_app.state.conversations.list
                extraction_app.state.conversations.list = Mock(
                    side_effect=OperationalError("secret database failure")
                )
                database_failure = await client.get("/conversations")
                extraction_app.state.conversations.list = list_conversations
                recovered = await client.get("/conversations")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(failed_chat.status_code, 503)
        self.assertEqual(failed_chat.json()["code"], "chat_unavailable")
        self.assertEqual(empty_history.json()["items"], [])

        self.assertEqual(committed_chat.status_code, 200)
        self.assertEqual(committed_chat.json()["content"], "committed answer")
        self.assertTrue(committed_history.json()["items"])
        self.assertEqual(memories.json()["items"], [])
        self.assertNotIn("secret", " ".join(logs.output))
        self.assertIn("RuntimeError", " ".join(logs.output))

        self.assertEqual(database_failure.status_code, 503)
        self.assertEqual(
            database_failure.json()["code"],
            "database_unavailable",
        )
        self.assertNotIn("secret", database_failure.text)
        self.assertEqual(
            [item["id"] for item in recovered.json()["items"]],
            [str(conversation_id)],
        )
