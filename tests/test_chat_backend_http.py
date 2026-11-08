from __future__ import annotations

import asyncio
import json
import os
import unittest
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, Mock, call, patch
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg import OperationalError, connect, sql
from pydantic import ValidationError
from starlette.requests import Request

from agents import Agent, AgentMemory, ToolCall
from agents.chat_backend import (
    AppSettings,
    Citation,
    ConversationArchivedError,
    ConversationNotFoundError,
    HybridRetrievalService,
    ModelProfileStore,
    create_app,
)
from agents.chat_backend.database import Database
from agents.chat_backend.conversation import (
    Conversation,
    ConversationCursor,
    ConversationPage,
    ConversationPatch,
    MessagePage,
    StoredMessage,
)
from agents.chat_backend.http import (
    CitationResponse,
    ChatReplyResponse,
    ConversationCreateRequest,
    ConversationPageResponse,
    ConversationPatchRequest,
    ConversationResponse,
    CursorQuery,
    CursorResponse,
    ErrorResponse,
    HealthResponse,
    HistoryQuery,
    IndexReportResponse,
    KnowledgeCreateRequest,
    KnowledgeIndexUnavailableError,
    KnowledgePageResponse,
    KnowledgeResponse,
    MessageCreate,
    MessagePageResponse,
    MemoryCreateRequest,
    MemoryPageResponse,
    MemoryPatchRequest,
    MemoryResponse,
    MemorySourceRequest,
    MemorySourceResponse,
    MemoryWriteResponse,
    PlanningApprovalResponse,
    PlanningCancelRequest,
    PlanningControlRequest,
    PlanningDraftRequest,
    PlanningReviseRequest,
    PlanningSnapshotResponse,
    PlanningStepResponse,
    PlanningToolApprovalRequest,
    RunTraceEntryResponse,
    StoredMessageResponse,
    chat_reply_response,
    conversation_cursor_from_query,
    conversation_page_response,
    conversation_patch_from_request,
    conversation_response,
    health_response,
    index_report_response,
    knowledge_create_from_request,
    knowledge_cursor_from_query,
    knowledge_page_response,
    knowledge_response,
    main,
    memory_create_from_request,
    memory_cursor_from_query,
    memory_page_response,
    memory_patch_from_request,
    memory_response,
    memory_source_response,
    memory_write_response,
    message_page_response,
    register_error_handlers,
)
from agents.chat_backend.knowledge import (
    IndexReport,
    KnowledgeCursor,
    KnowledgeIndexer,
    KnowledgeNotFoundError,
    KnowledgePage,
    KnowledgeSearchHit,
    KnowledgeSource,
    KnowledgeSourceCreate,
    KnowledgeStore,
    KnowledgeValidationError,
)
from agents.chat_backend.migration import MigrationRunner
from agents.chat_backend.memory import (
    MemoryCreate,
    MemoryCursor,
    MemoryDuplicateError,
    MemoryNotFoundError,
    MemoryPage,
    MemoryPatch,
    MemoryRecord,
    MemorySearchHit,
    MemorySaveResult,
    MemorySource,
    MemoryStore,
    MemorySupersedeError,
    MemoryValidationError,
)
from agents.chat_backend.memory_extraction import (
    MemoryExtractionService,
    create_memory_extraction_agent,
)
from agents.chat_backend.model_profiles import ModelProfile
from agents.chat_backend.planning_service import (
    PlanningApprovalSnapshot,
    PlanningDraftOutcome,
    PlanningRecoveryReport,
    PlanningSnapshot,
    PlanningStepSnapshot,
)
from agents.chat_backend.planning_store import PlanningStatus
from agents.chat_backend.service import (
    ChatInputError,
    ChatPersistenceError,
    ChatReply,
    ChatRunError,
)
from agents.chat_backend.settings import HealthStatus
from agents.contracts import ChatMessage, ModelResponse
from agents.planning import PlanExecution
from agents.task_state import TaskStatus
from agents.verification import VerificationResult, VerificationRunner


_POSTGRES_DSN = os.getenv("MYAGENT_TEST_POSTGRES_DSN")


def _planning_snapshot(
    *,
    run_id: UUID | None = None,
    conversation_id: UUID | None = None,
    row_revision: int = 1,
    status: PlanningStatus = "awaiting_approval",
    allowed_actions: tuple[str, ...] = ("approve", "revise", "cancel"),
    task_status: TaskStatus = TaskStatus.PENDING,
) -> PlanningSnapshot:
    return PlanningSnapshot(
        run_id=run_id or uuid4(),
        conversation_id=conversation_id or uuid4(),
        version=1,
        row_revision=row_revision,
        status=status,
        objective="升级规划功能",
        plan_markdown="# 升级规划功能",
        current_step_id="step-1" if task_status is TaskStatus.RUNNING else None,
        completed_step_ids=("step-1",)
        if task_status is TaskStatus.COMPLETED
        else (),
        pending_approval_keys=(),
        steps=(
            PlanningStepSnapshot(
                step_id="step-1",
                title="接入真实循环",
                instruction="复用现有 Runner 执行任务。",
                completion_criteria="步骤状态已持久化。",
                dependency_ids=(),
                is_final_verification=False,
                status=task_status,
                blocked_by=(),
                result="已完成" if task_status is TaskStatus.COMPLETED else None,
                error=None,
            ),
        ),
        pending_approvals=(
            PlanningApprovalSnapshot(
                tool_name="shell",
                call_id="call-1",
                summary="python -m pytest",
                reason="命令执行需要确认",
            ),
        )
        if status == "awaiting_tool_approval"
        else (),
        allowed_actions=allowed_actions,
        last_error=None,
    )


class RecordingModel:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.message_batches: list[list[ChatMessage]] = []

    def decide(self, messages, _tool_specs):
        self.message_batches.append(list(messages))
        return ToolCall("final_answer", {"answer": self.answer}, uuid4().hex)


class StructuredMemoryModel:
    def __init__(self) -> None:
        self.response_schema = None
        self.response_schema_name = None
        self.response_schema_strict = False

    def get_response(self, _messages, _tool_specs) -> ModelResponse:
        return ModelResponse(
            response_id="memory-extraction",
            output=[],
            output_text=(
                '{"candidates":[{"kind":"preference","scope":"global",'
                '"content":"Prefers concise answers","tags":["style"],'
                '"confidence":0.95}]}'
            ),
            tool_calls=[],
        )


class SequenceResponseModel:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.response_schema = None
        self.response_schema_name = None
        self.response_schema_strict = False

    def get_response(self, _messages, _tool_specs) -> ModelResponse:
        return ModelResponse(
            response_id=f"response-{len(self._outputs)}",
            output=[],
            output_text=self._outputs.pop(0),
            tool_calls=[],
        )


class MemoryKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, account: str) -> str | None:
        return self.values.get((service, account))

    def set_password(self, service: str, account: str, value: str) -> None:
        self.values[(service, account)] = value

    def delete_password(self, service: str, account: str) -> None:
        self.values.pop((service, account), None)


class HttpSchemaTests(unittest.TestCase):
    def test_message_and_history_input_boundaries(self) -> None:
        self.assertEqual(MessageCreate(content="  hello  ").content, "hello")
        self.assertEqual(HistoryQuery().page_size, 50)
        self.assertIsNone(HistoryQuery().before_sequence)

        for content in ("", "   ", "x" * 20_001):
            with self.subTest(content_length=len(content)):
                with self.assertRaises(ValidationError):
                    MessageCreate(content=content)
        for values in (
            {"page_size": 0},
            {"page_size": 101},
            {"before_sequence": 0},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    HistoryQuery(**values)

    def test_tool_rejection_requires_a_bounded_reason(self) -> None:
        approved = PlanningToolApprovalRequest(
            expected_row_revision=1,
            tool_name="shell",
            call_id="call-1",
            decision="approve",
        )
        rejected = PlanningToolApprovalRequest(
            expected_row_revision=1,
            tool_name="shell",
            call_id="call-1",
            decision="reject",
            rejection_message="命令不符合预期",
        )

        self.assertIsNone(approved.rejection_message)
        self.assertEqual(rejected.rejection_message, "命令不符合预期")
        for rejection_message in (None, "   ", "x" * 2_001):
            with self.subTest(rejection_message=rejection_message):
                with self.assertRaises(ValidationError):
                    PlanningToolApprovalRequest(
                        expected_row_revision=1,
                        tool_name="shell",
                        call_id="call-1",
                        decision="reject",
                        rejection_message=rejection_message,
                    )

    def test_management_request_boundaries_and_cursor_pairing(self) -> None:
        created_at = datetime.now(UTC)
        item_id = uuid4()
        source = MemorySourceRequest(
            conversation_id=uuid4(),
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
        )

        self.assertEqual(ConversationCreateRequest().title, "New conversation")
        self.assertEqual(
            CursorQuery(
                before_created_at=created_at,
                before_id=item_id,
            ).before_id,
            item_id,
        )
        self.assertEqual(
            MemoryCreateRequest(
                kind="fact",
                scope="global",
                content="Remember this",
                source=source,
            ).confidence,
            1.0,
        )
        self.assertEqual(
            KnowledgeCreateRequest(
                title="Guide",
                text="first\nsecond",
                metadata={"nested": [1, True, None]},
            ).metadata["nested"],
            [1, True, None],
        )

        invalid = (
            (CursorQuery, {"page_size": 0}),
            (CursorQuery, {"page_size": 101}),
            (CursorQuery, {"before_created_at": created_at}),
            (CursorQuery, {"before_id": item_id}),
            (CursorQuery, {"unexpected": True}),
            (ConversationCreateRequest, {"title": "   "}),
            (ConversationCreateRequest, {"title": "x" * 201}),
            (ConversationCreateRequest, {"unexpected": True}),
            (ConversationPatchRequest, {}),
            (ConversationPatchRequest, {"status": "paused"}),
            (ConversationPatchRequest, {"unexpected": True}),
            (
                MemorySourceRequest,
                {
                    "conversation_id": source.conversation_id,
                    "user_message_id": source.user_message_id,
                },
            ),
            (
                MemorySourceRequest,
                {
                    "conversation_id": source.conversation_id,
                    "user_message_id": source.user_message_id,
                    "assistant_message_id": source.user_message_id,
                },
            ),
            (
                MemoryCreateRequest,
                {
                    "kind": "unknown",
                    "scope": "global",
                    "content": "remember",
                    "source": source,
                },
            ),
            (
                MemoryCreateRequest,
                {
                    "kind": "fact",
                    "scope": "global",
                    "content": "   ",
                    "source": source,
                },
            ),
            (MemoryPatchRequest, {}),
            (MemoryPatchRequest, {"scope": "global"}),
            (KnowledgeCreateRequest, {"title": "Guide", "text": "   "}),
            (
                KnowledgeCreateRequest,
                {
                    "title": "Guide",
                    "text": "content",
                    "metadata": {"bad": object()},
                },
            ),
        )
        for model, values in invalid:
            with self.subTest(model=model.__name__, values=values):
                with self.assertRaises(ValidationError):
                    model(**values)

    def test_management_requests_convert_to_domain_values(self) -> None:
        created_at = datetime.now(UTC)
        item_id = uuid4()
        cursor_query = CursorQuery(
            before_created_at=created_at,
            before_id=item_id,
        )
        source = MemorySourceRequest(
            conversation_id=uuid4(),
            user_message_id=uuid4(),
            assistant_message_id=uuid4(),
        )

        self.assertEqual(
            conversation_cursor_from_query(cursor_query),
            ConversationCursor(created_at, item_id),
        )
        self.assertEqual(
            memory_cursor_from_query(cursor_query),
            MemoryCursor(created_at, item_id),
        )
        self.assertEqual(
            knowledge_cursor_from_query(cursor_query),
            KnowledgeCursor(created_at, item_id),
        )
        self.assertIsNone(conversation_cursor_from_query(CursorQuery()))
        self.assertEqual(
            conversation_patch_from_request(
                ConversationPatchRequest(title="Renamed", status="archived")
            ),
            ConversationPatch("Renamed", "archived"),
        )
        self.assertEqual(
            memory_create_from_request(
                MemoryCreateRequest(
                    kind="preference",
                    scope="conversation",
                    content="Keep answers short",
                    source=source,
                    tags=("style",),
                    confidence=0.8,
                )
            ),
            MemoryCreate(
                "preference",
                "conversation",
                "Keep answers short",
                MemorySource(
                    source.conversation_id,
                    source.user_message_id,
                    source.assistant_message_id,
                ),
                ("style",),
                0.8,
            ),
        )
        self.assertEqual(
            memory_patch_from_request(
                MemoryPatchRequest(content="Updated", confidence=0.7)
            ),
            MemoryPatch(content="Updated", confidence=0.7),
        )
        self.assertEqual(
            knowledge_create_from_request(
                KnowledgeCreateRequest(
                    title="Guide",
                    text="content",
                    metadata={"version": 1},
                )
            ),
            KnowledgeSourceCreate("Guide", "content", {"version": 1}),
        )

    def test_domain_values_are_mapped_to_explicit_http_fields(self) -> None:
        conversation_id = uuid4()
        message_id = uuid4()
        created_at = datetime.now(UTC)
        conversation = Conversation(
            conversation_id,
            created_at,
            99,
            "Archived chat",
            "archived",
        )
        stored = StoredMessage(
            message_id,
            conversation_id,
            7,
            "assistant",
            "answer",
            created_at,
            ({"stage": "model", "message": "正在请求模型", "step_number": 1},),
        )

        conversation_body = conversation_response(conversation)
        page_body = message_page_response(MessagePage((stored,), 7))
        reply_body = chat_reply_response(
            ChatReply(
                conversation_id,
                message_id,
                7,
                "answer [K1]",
                (Citation("[K1]", "knowledge", uuid4(), uuid4(), "Guide"),),
            ),
            ({"stage": "done", "message": "回复已保存"},),
        )
        health_body = health_response(HealthStatus("postgres", "ready"))

        self.assertEqual(
            set(conversation_body.model_dump()),
            {"id", "title", "status", "mode", "created_at"},
        )
        self.assertEqual(page_body.items[0].id, message_id)
        self.assertEqual(page_body.items[0].run_trace[0].stage, "model")
        self.assertEqual(page_body.before_sequence, 7)
        self.assertEqual(reply_body.message_id, message_id)
        self.assertEqual(reply_body.run_trace[0].stage, "done")
        self.assertEqual(reply_body.citations[0].marker, "[K1]")
        self.assertEqual(
            set(reply_body.citations[0].model_dump()),
            {"marker", "source_type", "item_id", "source_id", "title"},
        )
        self.assertEqual(health_body.status, "ready")

    def test_management_responses_are_explicit_safe_projections(self) -> None:
        created_at = datetime.now(UTC)
        conversation_id = uuid4()
        conversation = Conversation(
            conversation_id,
            created_at,
            4,
            "Chat",
            "active",
        )
        memory = MemoryRecord(
            id=uuid4(),
            kind="fact",
            scope="conversation",
            status="active",
            content="Remember this",
            tags=("profile",),
            confidence=0.9,
            fingerprint="a" * 64,
            source=MemorySource(conversation_id, uuid4(), uuid4()),
            created_at=created_at,
            updated_at=created_at,
            supersedes_id=uuid4(),
            embedding_model="secret-model",
            embedded_at=created_at,
        )
        knowledge = KnowledgeSource(
            uuid4(),
            "Guide",
            "knowledge text",
            {"version": 1},
            "indexed",
            "embedding-model",
            created_at,
            created_at,
            created_at,
        )

        conversation_body = conversation_page_response(
            ConversationPage(
                (conversation,),
                ConversationCursor(created_at, conversation_id),
            )
        )
        memory_body = memory_response(memory)
        memory_write_body = memory_write_response(MemorySaveResult(memory, False))
        memory_page_body = memory_page_response(
            MemoryPage((memory,), MemoryCursor(created_at, memory.id))
        )
        knowledge_body = knowledge_response(knowledge)
        knowledge_page_body = knowledge_page_response(
            KnowledgePage((knowledge,), KnowledgeCursor(created_at, knowledge.id))
        )
        report_body = index_report_response(
            IndexReport(knowledge.id, 3, "embedding-model", created_at)
        )

        self.assertEqual(conversation_body.next_cursor.id, conversation_id)
        self.assertEqual(
            set(memory_body.model_dump()),
            {
                "id",
                "kind",
                "scope",
                "status",
                "content",
                "tags",
                "confidence",
                "source",
                "created_at",
                "updated_at",
                "supersedes_id",
            },
        )
        self.assertNotIn("fingerprint", memory_body.model_dump())
        self.assertNotIn("embedding_model", memory_body.model_dump())
        self.assertFalse(memory_write_body.created)
        self.assertEqual(memory_write_body.record.id, memory.id)
        self.assertEqual(memory_page_body.next_cursor.id, memory.id)
        self.assertEqual(knowledge_body.metadata, {"version": 1})
        self.assertEqual(knowledge_page_body.next_cursor.id, knowledge.id)
        self.assertEqual(report_body.chunk_count, 3)

    def test_json_schemas_freeze_the_expected_fields(self) -> None:
        expected = {
            CursorQuery: {
                "page_size",
                "before_created_at",
                "before_id",
            },
            CursorResponse: {"created_at", "id"},
            ConversationCreateRequest: {"title"},
            ConversationPatchRequest: {"title", "status"},
            ConversationResponse: {"id", "title", "status", "mode", "created_at"},
            ConversationPageResponse: {"items", "next_cursor"},
            MemorySourceRequest: {
                "conversation_id",
                "user_message_id",
                "assistant_message_id",
            },
            MemorySourceResponse: {
                "conversation_id",
                "user_message_id",
                "assistant_message_id",
            },
            MemoryCreateRequest: {
                "kind",
                "scope",
                "content",
                "source",
                "tags",
                "confidence",
            },
            MemoryPatchRequest: {"content", "kind", "tags", "confidence"},
            MemoryResponse: {
                "id",
                "kind",
                "scope",
                "status",
                "content",
                "tags",
                "confidence",
                "source",
                "created_at",
                "updated_at",
                "supersedes_id",
            },
            MemoryWriteResponse: {"record", "created"},
            MemoryPageResponse: {"items", "next_cursor"},
            KnowledgeCreateRequest: {"title", "text", "metadata"},
            KnowledgeResponse: {
                "id",
                "title",
                "text",
                "metadata",
                "status",
                "embedding_model",
                "created_at",
                "updated_at",
                "indexed_at",
            },
            KnowledgePageResponse: {"items", "next_cursor"},
            IndexReportResponse: {
                "source_id",
                "chunk_count",
                "embedding_model",
                "indexed_at",
            },
            StoredMessageResponse: {
                "id",
                "conversation_id",
                "sequence",
                "role",
                "content",
                "created_at",
                "run_trace",
            },
            RunTraceEntryResponse: {"stage", "message", "step_number"},
            MessagePageResponse: {"items", "before_sequence"},
            CitationResponse: {
                "marker",
                "source_type",
                "item_id",
                "source_id",
                "title",
            },
            ChatReplyResponse: {
                "conversation_id",
                "message_id",
                "sequence",
                "content",
                "citations",
                "run_trace",
            },
            PlanningDraftRequest: {"content", "expected_row_revision"},
            PlanningControlRequest: {"expected_row_revision"},
            PlanningReviseRequest: {"expected_row_revision", "feedback"},
            PlanningCancelRequest: {"expected_row_revision", "reason"},
            PlanningToolApprovalRequest: {
                "expected_row_revision",
                "tool_name",
                "call_id",
                "decision",
                "rejection_message",
            },
            PlanningStepResponse: {
                "step_id",
                "title",
                "instruction",
                "completion_criteria",
                "dependency_ids",
                "is_final_verification",
                "status",
                "blocked_by",
                "result",
                "error",
            },
            PlanningApprovalResponse: {
                "tool_name",
                "call_id",
                "summary",
                "reason",
            },
            PlanningSnapshotResponse: {
                "run_id",
                "conversation_id",
                "version",
                "row_revision",
                "status",
                "objective",
                "plan_markdown",
                "current_step_id",
                "completed_step_ids",
                "pending_approval_keys",
                "steps",
                "pending_approvals",
                "allowed_actions",
                "last_error",
            },
            HealthResponse: {"service", "status", "detail"},
            ErrorResponse: {"code", "message", "details"},
        }
        for model, fields in expected.items():
            with self.subTest(model=model.__name__):
                self.assertEqual(set(model.model_json_schema()["properties"]), fields)


class HttpErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        app = FastAPI()
        register_error_handlers(app)

        @app.post("/validate")
        def validate_message(message: MessageCreate):
            return message

        @app.get("/error/{kind}")
        def raise_error(kind: str):
            if kind == "input":
                raise ChatInputError("secret input details")
            if kind == "missing":
                raise ConversationNotFoundError(uuid4())
            if kind == "archived":
                raise ConversationArchivedError(uuid4())
            if kind == "memory-missing":
                raise MemoryNotFoundError(uuid4())
            if kind == "knowledge-missing":
                raise KnowledgeNotFoundError(uuid4())
            if kind == "memory-duplicate":
                raise MemoryDuplicateError(uuid4())
            if kind == "memory-conflict":
                raise MemorySupersedeError("secret conflict details")
            if kind == "memory-invalid":
                raise MemoryValidationError("secret validation details")
            if kind == "knowledge-invalid":
                raise KnowledgeValidationError("secret validation details")
            if kind == "embedding-unavailable":
                raise KnowledgeIndexUnavailableError("secret embedding details")
            if kind == "run":
                raise ChatRunError("secret model details")
            if kind == "database":
                raise OperationalError("secret database details")
            if kind == "nested-database":
                try:
                    raise OperationalError("secret database details")
                except OperationalError as exc:
                    raise ChatPersistenceError("safe wrapper") from exc
            if kind == "persistence":
                raise ChatPersistenceError("secret persistence details")
            raise RuntimeError("secret internal details")

        self.client = TestClient(app, raise_server_exceptions=False)

    def test_validation_details_do_not_echo_the_input(self) -> None:
        secret = "do-not-return-" * 2_000
        response = self.client.post("/validate", json={"content": secret})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "invalid_request")
        self.assertEqual(
            set(response.json()["details"][0]),
            {"location", "type"},
        )
        self.assertNotIn(secret, response.text)

    def test_known_errors_use_stable_safe_responses(self) -> None:
        cases = {
            "input": (422, "invalid_message"),
            "missing": (404, "conversation_not_found"),
            "archived": (409, "conversation_archived"),
            "memory-missing": (404, "memory_not_found"),
            "knowledge-missing": (404, "knowledge_not_found"),
            "memory-duplicate": (409, "memory_duplicate"),
            "memory-conflict": (409, "memory_conflict"),
            "memory-invalid": (422, "invalid_memory"),
            "knowledge-invalid": (422, "invalid_knowledge"),
            "embedding-unavailable": (503, "embedding_unavailable"),
            "run": (503, "chat_unavailable"),
            "database": (503, "database_unavailable"),
            "nested-database": (503, "database_unavailable"),
            "persistence": (500, "persistence_inconsistent"),
        }
        for kind, expected in cases.items():
            with self.subTest(kind=kind):
                response = self.client.get(f"/error/{kind}")
                self.assertEqual(
                    (response.status_code, response.json()["code"]),
                    expected,
                )
                self.assertEqual(set(response.json()), {"code", "message"})
                self.assertNotIn("secret", response.text)

    def test_unknown_errors_are_logged_but_not_returned(self) -> None:
        with self.assertLogs("agents.chat_backend.http", level="ERROR") as logs:
            response = self.client.get("/error/unknown")

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json()["code"], "internal_error")
        self.assertNotIn("secret", response.text)
        self.assertIn("secret internal details", "\n".join(logs.output))


class HttpAppTests(unittest.IsolatedAsyncioTestCase):
    def make_app(
        self,
        cors_origins: tuple[str, ...] = (),
        memory_agent_factory: Callable[[], Agent] | None = None,
        embedding_client=None,
    ) -> FastAPI:
        settings = AppSettings(
            postgres_dsn="postgresql://unused:unused@127.0.0.1:1/unused",
            cors_origins=cors_origins,
        )
        return create_app(
            settings,
            lambda: Agent(),
            memory_agent_factory=memory_agent_factory,
            embedding_client=embedding_client,
        )

    def test_optional_memory_and_retrieval_composition_is_deferred(self) -> None:
        disabled = self.make_app()
        self.assertIsInstance(disabled.state.memory_store, MemoryStore)
        self.assertIsInstance(disabled.state.knowledge_store, KnowledgeStore)
        self.assertIsNone(disabled.state.memory_extraction)
        self.assertIsNone(disabled.state.hybrid_retrieval)
        self.assertIsNone(disabled.state.knowledge_indexer)

        memory_agent_factory = Mock()
        enabled = self.make_app(memory_agent_factory=memory_agent_factory)
        self.assertIsInstance(enabled.state.memory_store, MemoryStore)
        self.assertIsInstance(
            enabled.state.memory_extraction,
            MemoryExtractionService,
        )
        self.assertIs(
            enabled.state.chat_service._memory_extraction,
            enabled.state.memory_extraction,
        )
        memory_agent_factory.assert_not_called()

        embedding_client = Mock()
        retrieval_enabled = self.make_app(embedding_client=embedding_client)
        self.assertIsInstance(
            retrieval_enabled.state.hybrid_retrieval,
            HybridRetrievalService,
        )
        self.assertIs(
            retrieval_enabled.state.hybrid_retrieval._memory_store,
            retrieval_enabled.state.memory_store,
        )
        self.assertIs(
            retrieval_enabled.state.hybrid_retrieval._knowledge_store,
            retrieval_enabled.state.knowledge_store,
        )
        self.assertIs(
            retrieval_enabled.state.chat_service._hybrid_retrieval,
            retrieval_enabled.state.hybrid_retrieval,
        )
        self.assertIsNone(retrieval_enabled.state.chat_service._memory_retrieval)
        self.assertIsInstance(
            retrieval_enabled.state.knowledge_indexer,
            KnowledgeIndexer,
        )
        self.assertIs(
            retrieval_enabled.state.knowledge_indexer._store,
            retrieval_enabled.state.knowledge_store,
        )
        embedding_client.embed.assert_not_called()

    async def test_m05_gate_runs_hybrid_context_and_returns_current_citations(
        self,
    ) -> None:
        conversation_id = uuid4()
        source_id = uuid4()
        memory_id = UUID(int=2)
        chunk_id = UUID(int=1)
        created_at = datetime.now(UTC)
        memory_hit = MemorySearchHit(
            MemoryRecord(
                memory_id,
                "fact",
                "conversation",
                "active",
                "Remember the blue preference.",
                (),
                1.0,
                "a" * 64,
                MemorySource(conversation_id, uuid4(), uuid4()),
                created_at,
                created_at,
            )
        )
        knowledge_hit = KnowledgeSearchHit(
            source_id,
            chunk_id,
            "Guide",
            "The guide recommends green.",
            0,
            0,
            27,
        )
        answer = (
            "Use the guide [K1], then memory [M1]. "
            "Ignore [K9], [k1], and duplicate [K1]."
        )
        model = RecordingModel(answer)
        embedding_client = Mock()
        embedding_client.embed.return_value = ((0.0,) * 1024,)
        settings = AppSettings(
            postgres_dsn="postgresql://unused:unused@127.0.0.1:1/unused",
        )
        app = create_app(
            settings,
            lambda: Agent(memory=AgentMemory(), model=model),
            embedding_client=embedding_client,
        )
        app.state.conversations.get = Mock(
            return_value=Conversation(conversation_id, created_at, 0)
        )
        app.state.messages.list = Mock(return_value=())
        saved_items: list[ChatMessage] = []

        def save_messages(saved_conversation_id, items):
            self.assertEqual(saved_conversation_id, conversation_id)
            saved_items.extend(items)
            return tuple(
                StoredMessage(
                    uuid4(),
                    conversation_id,
                    sequence,
                    item.role,
                    item.content,
                    created_at,
                )
                for sequence, item in enumerate(items, start=1)
            )

        app.state.messages.add_batch = Mock(side_effect=save_messages)
        app.state.memory_store.search_keyword = Mock(return_value=(memory_hit,))
        app.state.memory_store.search_vector = Mock(return_value=(memory_hit,))
        app.state.knowledge_store.search_keyword = Mock(return_value=(knowledge_hit,))
        app.state.knowledge_store.search_vector = Mock(return_value=(knowledge_hit,))

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.post(
                f"/conversations/{conversation_id}/messages",
                json={"content": "question"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [(item["marker"], item["source_type"]) for item in response.json()["citations"]],
            [("[K1]", "knowledge"), ("[M1]", "memory")],
        )
        embedding_client.embed.assert_called_once_with(("question",))
        app.state.memory_store.search_keyword.assert_called_once_with(
            "question", conversation_id, settings.retrieval_top_k
        )
        app.state.memory_store.search_vector.assert_called_once_with(
            (0.0,) * 1024,
            settings.embedding_model,
            conversation_id,
            settings.retrieval_top_k,
        )
        app.state.knowledge_store.search_keyword.assert_called_once()
        app.state.knowledge_store.search_vector.assert_called_once()
        model_contents = [message.content for batch in model.message_batches for message in batch]
        self.assertTrue(
            any(
                "Remember the blue preference." in content
                and "The guide recommends green." in content
                for content in model_contents
            )
        )
        self.assertEqual(saved_items[0], ChatMessage("user", "question"))
        self.assertEqual(saved_items[-1], ChatMessage("assistant", answer))
        self.assertNotIn(
            "Remember the blue preference.",
            [item.content for item in saved_items],
        )
        self.assertNotIn(
            "The guide recommends green.",
            [item.content for item in saved_items],
        )

    async def test_factory_defers_io_and_lifespan_pairs_resources(self) -> None:
        with (
            patch.object(Database, "open") as open_database,
            patch.object(Database, "close") as close_database,
            patch.object(MigrationRunner, "upgrade") as upgrade,
        ):
            app = self.make_app()
            app.state.planning_service.recover_running = AsyncMock(
                return_value=PlanningRecoveryReport(0, (), (), ())
            )
            open_database.assert_not_called()
            upgrade.assert_not_called()

            async with app.router.lifespan_context(app):
                await asyncio.sleep(0)
                open_database.assert_called_once_with()
                upgrade.assert_called_once_with()
                app.state.planning_service.recover_running.assert_awaited_once_with()
                close_database.assert_not_called()
            close_database.assert_called_once_with()

    async def test_lifespan_closes_database_when_migration_fails(self) -> None:
        with (
            patch.object(Database, "open"),
            patch.object(Database, "close") as close_database,
            patch.object(
                MigrationRunner,
                "upgrade",
                side_effect=RuntimeError("migration failed"),
            ),
        ):
            app = self.make_app()
            with self.assertRaisesRegex(RuntimeError, "migration failed"):
                async with app.router.lifespan_context(app):
                    self.fail("startup must not accept requests")
            close_database.assert_called_once_with()

    async def test_browser_preflight_uses_the_exact_local_cors_contract(
        self,
    ) -> None:
        origins = (
            "http://localhost:3000",
            "https://127.0.0.1:8443",
        )
        app = self.make_app(origins)
        app.state.database.health = lambda: HealthStatus("postgres", "ready")
        cases = (
            ("/health", "GET"),
            ("/conversations", "POST"),
            (f"/conversations/{uuid4()}", "PATCH"),
            (f"/knowledge/{uuid4()}", "DELETE"),
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            allowed = []
            for origin in origins:
                for path, method in cases:
                    allowed.append(
                        await client.options(
                            path,
                            headers={
                                "Origin": origin,
                                "Access-Control-Request-Method": method,
                                "Access-Control-Request-Headers": "Content-Type",
                            },
                        )
                    )
            denied_origin = await client.options(
                "/knowledge",
                headers={
                    "Origin": "http://localhost:4000",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Content-Type",
                },
            )
            denied_method = await client.options(
                "/knowledge",
                headers={
                    "Origin": origins[0],
                    "Access-Control-Request-Method": "PUT",
                    "Access-Control-Request-Headers": "Content-Type",
                },
            )
            denied_header = await client.options(
                "/knowledge",
                headers={
                    "Origin": origins[0],
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
            denied_actual = await client.get(
                "/health",
                headers={"Origin": "http://localhost:4000"},
            )

        for response, origin in zip(
            allowed,
            tuple(origin for origin in origins for _case in cases),
        ):
            with self.subTest(origin=origin, headers=response.headers):
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    response.headers["access-control-allow-origin"],
                    origin,
                )
                self.assertEqual(
                    response.headers["access-control-allow-methods"],
                    "GET, POST, PATCH, DELETE",
                )
                self.assertIn(
                    "content-type",
                    response.headers["access-control-allow-headers"].casefold(),
                )
                self.assertNotIn(
                    "authorization",
                    response.headers["access-control-allow-headers"].casefold(),
                )
                self.assertNotIn(
                    "access-control-allow-credentials",
                    response.headers,
                )
        for response in (denied_origin, denied_method, denied_header):
            self.assertEqual(response.status_code, 400)
        self.assertNotIn(
            "access-control-allow-origin",
            denied_origin.headers,
        )
        self.assertNotIn(
            "PUT",
            denied_method.headers["access-control-allow-methods"],
        )
        self.assertNotIn(
            "authorization",
            denied_header.headers["access-control-allow-headers"].casefold(),
        )
        self.assertNotIn(
            "access-control-allow-origin",
            denied_actual.headers,
        )

    async def test_conversation_management_routes_delegate_to_existing_store(
        self,
    ) -> None:
        app = self.make_app()
        conversation_id = uuid4()
        cursor_id = uuid4()
        missing_id = uuid4()
        created_at = datetime.now(UTC)
        default_conversation = Conversation(conversation_id, created_at, 1)
        titled_conversation = Conversation(
            conversation_id,
            created_at,
            1,
            "Project chat",
        )
        renamed_conversation = Conversation(
            conversation_id,
            created_at,
            1,
            "Renamed chat",
        )
        archived_conversation = Conversation(
            conversation_id,
            created_at,
            1,
            "Renamed chat",
            "archived",
        )

        app.state.conversations.create = Mock(
            side_effect=(default_conversation, titled_conversation)
        )
        app.state.conversations.list = Mock(
            return_value=ConversationPage(
                (archived_conversation,),
                ConversationCursor(created_at, conversation_id),
            )
        )
        app.state.conversations.get = Mock(
            side_effect=(titled_conversation, ConversationNotFoundError(missing_id))
        )
        app.state.conversations.update = Mock(
            side_effect=(
                renamed_conversation,
                archived_conversation,
                renamed_conversation,
            )
        )
        app.state.conversations.delete = Mock()
        app.state.chat_service.send_message = AsyncMock(
            side_effect=ConversationArchivedError(conversation_id)
        )

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            default_created = await client.post("/conversations")
            titled_created = await client.post(
                "/conversations",
                json={"title": "  Project chat  "},
            )
            listed = await client.get(
                "/conversations",
                params={
                    "page_size": 1,
                    "before_created_at": created_at.isoformat(),
                    "before_id": str(cursor_id),
                    "status": "archived",
                },
            )
            fetched = await client.get(f"/conversations/{conversation_id}")
            missing = await client.get(f"/conversations/{missing_id}")
            renamed = await client.patch(
                f"/conversations/{conversation_id}",
                json={"title": "Renamed chat"},
            )
            archived = await client.patch(
                f"/conversations/{conversation_id}",
                json={"status": "archived"},
            )
            restored = await client.patch(
                f"/conversations/{conversation_id}",
                json={"status": "active"},
            )
            empty_patch = await client.patch(
                f"/conversations/{conversation_id}",
                json={},
            )
            incomplete_cursor = await client.get(
                "/conversations",
                params={"before_id": str(cursor_id)},
            )
            extra_query = await client.get(
                "/conversations",
                params={"unexpected": "value"},
            )
            archived_send = await client.post(
                f"/conversations/{conversation_id}/messages",
                json={"content": "question"},
            )
            deleted = await client.delete(f"/conversations/{conversation_id}")

        self.assertEqual(default_created.status_code, 201)
        self.assertEqual(default_created.json()["title"], "New conversation")
        self.assertEqual(titled_created.status_code, 201)
        self.assertEqual(titled_created.json()["title"], "Project chat")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["status"], "archived")
        next_cursor = listed.json()["next_cursor"]
        self.assertEqual(next_cursor["id"], str(conversation_id))
        self.assertEqual(
            datetime.fromisoformat(next_cursor["created_at"].replace("Z", "+00:00")),
            created_at,
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "conversation_not_found")
        self.assertEqual(renamed.json()["title"], "Renamed chat")
        self.assertEqual(archived.json()["status"], "archived")
        self.assertEqual(restored.json()["status"], "active")
        self.assertEqual(empty_patch.status_code, 422)
        self.assertEqual(incomplete_cursor.status_code, 422)
        self.assertEqual(extra_query.status_code, 422)
        self.assertEqual(archived_send.status_code, 409)
        self.assertEqual(archived_send.json()["code"], "conversation_archived")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.content, b"")
        self.assertEqual(
            app.state.conversations.create.call_args_list,
            [call(), call("Project chat")],
        )
        app.state.conversations.list.assert_called_once_with(
            page_size=1,
            before=ConversationCursor(created_at, cursor_id),
            status="archived",
        )
        self.assertEqual(
            app.state.conversations.update.call_args_list,
            [
                call(conversation_id, ConversationPatch(title="Renamed chat")),
                call(conversation_id, ConversationPatch(status="archived")),
                call(conversation_id, ConversationPatch(status="active")),
            ],
        )
        app.state.conversations.delete.assert_called_once_with(conversation_id)

    async def test_memory_management_routes_delegate_to_existing_store(self) -> None:
        app = self.make_app()
        memory_id = uuid4()
        cursor_id = uuid4()
        missing_id = uuid4()
        conversation_id = uuid4()
        user_message_id = uuid4()
        assistant_message_id = uuid4()
        created_at = datetime.now(UTC)
        source = MemorySource(
            conversation_id,
            user_message_id,
            assistant_message_id,
        )
        active = MemoryRecord(
            memory_id,
            "fact",
            "conversation",
            "active",
            "Remember this",
            ("profile",),
            0.9,
            "a" * 64,
            source,
            created_at,
            created_at,
            embedding_model="private-model",
            embedded_at=created_at,
        )
        updated = MemoryRecord(
            memory_id,
            "preference",
            "conversation",
            "active",
            "Updated fact",
            ("style",),
            0.8,
            "b" * 64,
            source,
            created_at,
            created_at,
        )
        disabled = MemoryRecord(
            memory_id,
            "preference",
            "conversation",
            "disabled",
            "Updated fact",
            ("style",),
            0.8,
            "b" * 64,
            source,
            created_at,
            created_at,
        )

        app.state.memory_store.list = Mock(
            return_value=MemoryPage(
                (active,),
                MemoryCursor(created_at, memory_id),
            )
        )
        app.state.memory_store.create = Mock(
            side_effect=(
                MemorySaveResult(active, True),
                MemorySaveResult(active, False),
            )
        )
        app.state.memory_store.get = Mock(return_value=active)
        app.state.memory_store.update = Mock(return_value=updated)
        app.state.memory_store.disable = Mock(side_effect=(disabled, disabled))
        app.state.memory_store.delete = Mock(side_effect=(True, False))
        payload = {
            "kind": "fact",
            "scope": "conversation",
            "content": "Remember this",
            "source": {
                "conversation_id": str(conversation_id),
                "user_message_id": str(user_message_id),
                "assistant_message_id": str(assistant_message_id),
            },
            "tags": ["profile"],
            "confidence": 0.9,
        }

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            listed = await client.get(
                "/memories",
                params={
                    "page_size": 1,
                    "before_created_at": created_at.isoformat(),
                    "before_id": str(cursor_id),
                    "status": "active",
                    "scope": "conversation",
                    "source_conversation_id": str(conversation_id),
                    "source_message_id": str(user_message_id),
                },
            )
            created = await client.post("/memories", json=payload)
            duplicate = await client.post("/memories", json=payload)
            fetched = await client.get(f"/memories/{memory_id}")
            patched = await client.patch(
                f"/memories/{memory_id}",
                json={
                    "content": "Updated fact",
                    "kind": "preference",
                    "tags": ["style"],
                    "confidence": 0.8,
                },
            )
            empty_patch = await client.patch(f"/memories/{memory_id}", json={})
            incomplete_cursor = await client.get(
                "/memories",
                params={"before_id": str(cursor_id)},
            )
            extra_query = await client.get(
                "/memories",
                params={"unexpected": "value"},
            )
            first_disabled = await client.post(f"/memories/{memory_id}/disable")
            second_disabled = await client.post(f"/memories/{memory_id}/disable")
            deleted = await client.delete(f"/memories/{memory_id}")
            missing = await client.delete(f"/memories/{missing_id}")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], str(memory_id))
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["created"])
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(patched.json()["content"], "Updated fact")
        self.assertEqual(empty_patch.status_code, 422)
        self.assertEqual(incomplete_cursor.status_code, 422)
        self.assertEqual(extra_query.status_code, 422)
        self.assertEqual(first_disabled.json()["status"], "disabled")
        self.assertEqual(second_disabled.json()["status"], "disabled")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.content, b"")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "memory_not_found")
        for body in (
            listed.json()["items"][0],
            created.json()["record"],
            fetched.json(),
        ):
            with self.subTest(body=body):
                self.assertNotIn("fingerprint", body)
                self.assertNotIn("embedding", body)
                self.assertNotIn("embedding_model", body)
                self.assertNotIn("embedded_at", body)

        app.state.memory_store.list.assert_called_once_with(
            page_size=1,
            before=MemoryCursor(created_at, cursor_id),
            status="active",
            scope="conversation",
            source_conversation_id=conversation_id,
            source_message_id=user_message_id,
        )
        expected_create = MemoryCreate(
            "fact",
            "conversation",
            "Remember this",
            source,
            ("profile",),
            0.9,
        )
        self.assertEqual(
            app.state.memory_store.create.call_args_list,
            [call(expected_create), call(expected_create)],
        )
        app.state.memory_store.get.assert_called_once_with(memory_id)
        app.state.memory_store.update.assert_called_once_with(
            memory_id,
            MemoryPatch(
                content="Updated fact",
                kind="preference",
                tags=("style",),
                confidence=0.8,
            ),
        )
        self.assertEqual(
            app.state.memory_store.disable.call_args_list,
            [call(memory_id), call(memory_id)],
        )
        self.assertEqual(
            app.state.memory_store.delete.call_args_list,
            [call(memory_id), call(missing_id)],
        )

    async def test_knowledge_management_routes_reuse_store_and_indexer(self) -> None:
        embedding_client = Mock()
        app = self.make_app(embedding_client=embedding_client)
        source_id = uuid4()
        cursor_id = uuid4()
        missing_id = uuid4()
        created_at = datetime.now(UTC)
        pending = KnowledgeSource(
            source_id,
            "Project notes",
            "Stable knowledge text",
            {"section": 1},
            "pending",
            None,
            created_at,
            created_at,
            None,
        )
        report = IndexReport(
            source_id,
            1,
            "text-embedding-3-small",
            created_at,
        )
        app.state.knowledge_store.list_sources = Mock(
            return_value=KnowledgePage(
                (pending,),
                KnowledgeCursor(created_at, source_id),
            )
        )
        app.state.knowledge_store.create_source = Mock(return_value=pending)
        app.state.knowledge_store.get_source = Mock(
            side_effect=(pending, KnowledgeNotFoundError(missing_id))
        )
        app.state.knowledge_store.delete_source = Mock(
            side_effect=(None, KnowledgeNotFoundError(missing_id))
        )
        app.state.knowledge_indexer.reindex = Mock(return_value=report)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            listed = await client.get(
                "/knowledge",
                params={
                    "page_size": 1,
                    "before_created_at": created_at.isoformat(),
                    "before_id": str(cursor_id),
                },
            )
            created = await client.post(
                "/knowledge",
                json={
                    "title": "  Project notes  ",
                    "text": "Stable knowledge text",
                    "metadata": {"section": 1},
                },
            )
            fetched = await client.get(f"/knowledge/{source_id}")
            missing = await client.get(f"/knowledge/{missing_id}")
            reindexed = await client.post(f"/knowledge/{source_id}/reindex")
            incomplete_cursor = await client.get(
                "/knowledge",
                params={"before_id": str(cursor_id)},
            )
            extra_query = await client.get(
                "/knowledge",
                params={"unexpected": "value"},
            )
            deleted = await client.delete(f"/knowledge/{source_id}")
            missing_delete = await client.delete(f"/knowledge/{missing_id}")

        unavailable_app = self.make_app()
        unavailable_transport = httpx.ASGITransport(app=unavailable_app)
        async with httpx.AsyncClient(
            transport=unavailable_transport,
            base_url="http://test",
        ) as client:
            unavailable = await client.post(f"/knowledge/{source_id}/reindex")

        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json()["items"][0]["id"], str(source_id))
        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["title"], "Project notes")
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["code"], "knowledge_not_found")
        self.assertEqual(reindexed.status_code, 200)
        self.assertEqual(reindexed.json()["chunk_count"], 1)
        self.assertEqual(incomplete_cursor.status_code, 422)
        self.assertEqual(extra_query.status_code, 422)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted.content, b"")
        self.assertEqual(missing_delete.status_code, 404)
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["code"], "embedding_unavailable")
        for body in (listed.json()["items"][0], created.json(), fetched.json()):
            with self.subTest(body=body):
                self.assertNotIn("chunks", body)
                self.assertNotIn("embedding", body)

        app.state.knowledge_store.list_sources.assert_called_once_with(
            1,
            KnowledgeCursor(created_at, cursor_id),
        )
        app.state.knowledge_store.create_source.assert_called_once_with(
            KnowledgeSourceCreate(
                "Project notes",
                "Stable knowledge text",
                {"section": 1},
            )
        )
        self.assertEqual(
            app.state.knowledge_store.get_source.call_args_list,
            [call(source_id), call(missing_id)],
        )
        app.state.knowledge_indexer.reindex.assert_called_once_with(source_id)
        self.assertEqual(
            app.state.knowledge_store.delete_source.call_args_list,
            [call(source_id), call(missing_id)],
        )

    async def test_planning_query_cancel_and_projection_use_public_contract(
        self,
    ) -> None:
        app = self.make_app()
        conversation_id = uuid4()
        run_id = uuid4()
        created_at = datetime.now(UTC)
        current = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=4,
            status="awaiting_tool_approval",
            allowed_actions=("decide_tool_approval", "cancel"),
            task_status=TaskStatus.RUNNING,
        )
        cancelled = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=5,
            status="cancelled",
            allowed_actions=(),
            task_status=TaskStatus.CANCELLED,
        )
        app.state.conversations.get = Mock(
            return_value=Conversation(
                conversation_id,
                created_at,
                0,
                mode="plan",
            )
        )
        app.state.planning_service.get_latest_snapshot = Mock(return_value=current)
        app.state.planning_service.cancel = AsyncMock(return_value=cancelled)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            fetched = await client.get(
                f"/conversations/{conversation_id}/planning"
            )
            response = await client.post(
                f"/planning-runs/{run_id}/cancel",
                json={"expected_row_revision": 4, "reason": "用户取消"},
            )

        self.assertEqual(fetched.status_code, 200)
        body = fetched.json()
        self.assertEqual(body["steps"][0]["status"], "running")
        self.assertEqual(body["pending_approvals"][0]["tool_name"], "shell")
        self.assertEqual(
            body["pending_approvals"][0]["summary"],
            "python -m pytest",
        )
        self.assertNotIn("run_state", fetched.text.casefold())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "cancelled")
        app.state.planning_service.cancel.assert_awaited_once_with(
            run_id,
            4,
            "用户取消",
        )

    async def test_planning_sse_reports_done_and_rejects_stale_page(self) -> None:
        app = self.make_app()
        conversation_id = uuid4()
        run_id = uuid4()
        awaiting = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=7,
        )
        completed = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=8,
            status="completed",
            allowed_actions=(),
            task_status=TaskStatus.COMPLETED,
        )
        app.state.planning_service.get_snapshot = Mock(return_value=awaiting)
        app.state.planning_service.approve = AsyncMock(return_value=completed)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            stale = await client.post(
                f"/planning-runs/{run_id}/approve/stream",
                json={"expected_row_revision": 6},
            )
            streamed = await client.post(
                f"/planning-runs/{run_id}/approve/stream",
                json={"expected_row_revision": 7},
            )

        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "planning_conflict")
        self.assertEqual(streamed.status_code, 200)
        self.assertEqual(
            streamed.headers["content-type"].split(";")[0],
            "text/event-stream",
        )
        self.assertIn("event: start", streamed.text)
        self.assertIn("event: snapshot", streamed.text)
        self.assertIn("event: done", streamed.text)
        self.assertIn('"status":"completed"', streamed.text)
        app.state.planning_service.approve.assert_awaited_once()

    async def test_planning_disconnect_keeps_server_operation_running(self) -> None:
        app = self.make_app()
        conversation_id = uuid4()
        run_id = uuid4()
        awaiting = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=7,
        )
        completed = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=8,
            status="completed",
            allowed_actions=(),
            task_status=TaskStatus.COMPLETED,
        )
        started = asyncio.Event()
        release = asyncio.Event()

        async def approve(*_args) -> PlanningSnapshot:
            started.set()
            await release.wait()
            return completed

        async def disconnected() -> dict[str, object]:
            return {"type": "http.disconnect"}

        app.state.planning_service.get_snapshot = Mock(return_value=awaiting)
        app.state.planning_service.approve = AsyncMock(side_effect=approve)
        route = next(
            item
            for item in app.routes
            if item.path == "/planning-runs/{run_id}/approve/stream"
        )
        request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": f"/planning-runs/{run_id}/approve/stream",
                "headers": [],
                "query_string": b"",
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "root_path": "",
                "http_version": "1.1",
            },
            disconnected,
        )
        response = route.endpoint(
            run_id,
            PlanningControlRequest(expected_row_revision=7),
            request,
        )
        iterator = response.body_iterator

        first = await anext(iterator)
        self.assertIn("event: start", first)
        second = await anext(iterator)
        self.assertIn("正在继续执行规划任务", second)
        with self.assertRaises(StopAsyncIteration):
            await anext(iterator)
        await asyncio.wait_for(started.wait(), timeout=1)
        self.assertEqual(len(app.state.background_tasks), 1)
        operation = next(iter(app.state.background_tasks))
        self.assertFalse(operation.cancelled())
        release.set()
        await asyncio.wait_for(operation, timeout=1)
        await asyncio.sleep(0)
        self.assertFalse(app.state.background_tasks)

    async def test_planning_draft_preflight_and_question_stream(self) -> None:
        app = self.make_app()
        conversation_id = uuid4()
        run_id = uuid4()
        created_at = datetime.now(UTC)
        active = _planning_snapshot(
            run_id=run_id,
            conversation_id=conversation_id,
            row_revision=3,
            status="drafting",
            allowed_actions=("cancel",),
        )
        app.state.conversations.get = Mock(
            return_value=Conversation(conversation_id, created_at, 0)
        )
        app.state.planning_service.get_latest_snapshot = Mock(return_value=active)

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            missing_revision = await client.post(
                f"/conversations/{conversation_id}/planning/draft/stream",
                json={"content": "补充目标"},
            )
            stale = await client.post(
                f"/conversations/{conversation_id}/planning/draft/stream",
                json={"content": "补充目标", "expected_row_revision": 2},
            )

            app.state.planning_service.get_latest_snapshot.return_value = None
            question_snapshot = _planning_snapshot(
                run_id=run_id,
                conversation_id=conversation_id,
                row_revision=1,
                status="drafting",
                allowed_actions=("cancel",),
            )
            assistant_message_id = uuid4()
            app.state.messages.set_run_trace = Mock()
            app.state.planning_service.draft = AsyncMock(
                return_value=PlanningDraftOutcome(
                    "question",
                    "还需要确认哪些完成标准？",
                    question_snapshot,
                    assistant_message_id,
                )
            )
            streamed = await client.post(
                f"/conversations/{conversation_id}/planning/draft/stream",
                json={"content": "制定升级计划"},
            )

        self.assertEqual(missing_revision.status_code, 422)
        self.assertEqual(
            missing_revision.json()["code"],
            "invalid_planning_request",
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["code"], "planning_conflict")
        self.assertEqual(streamed.status_code, 200)
        self.assertIn('"kind":"question"', streamed.text)
        self.assertIn("还需要确认哪些完成标准？", streamed.text)
        self.assertIn("event: done", streamed.text)
        self.assertIn('"run_trace":[', streamed.text)
        self.assertIn("规划结果已解析并保存", streamed.text)
        app.state.messages.set_run_trace.assert_called_once()
        self.assertEqual(
            app.state.messages.set_run_trace.call_args.args[0],
            assistant_message_id,
        )

    async def test_health_validation_and_openapi_use_frozen_models(self) -> None:
        app = self.make_app()
        app.state.database.health = lambda: HealthStatus(
            "postgres",
            "unavailable",
            "postgres unavailable",
        )
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            unavailable = await client.get("/health")
            invalid_path = await client.get("/conversations/not-a-uuid/messages")
            invalid_page = await client.get(
                f"/conversations/{uuid4()}/messages",
                params={"page_size": 101},
            )
            missing = await client.get("/not-a-real-route")
            wrong_method = await client.put("/health")
            openapi_response = await client.get("/openapi.json")
            docs = await client.get("/docs")
            redoc = await client.get("/redoc")

        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.json()["status"], "unavailable")
        for response in (invalid_path, invalid_page):
            with self.subTest(body=response.json()):
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["code"], "invalid_request")
        self.assertEqual(
            (missing.status_code, missing.json()),
            (
                404,
                {
                    "code": "not_found",
                    "message": "Resource was not found.",
                },
            ),
        )
        self.assertEqual(
            (wrong_method.status_code, wrong_method.json()),
            (
                405,
                {
                    "code": "method_not_allowed",
                    "message": "Method is not allowed.",
                },
            ),
        )
        self.assertEqual(docs.status_code, 200)
        self.assertEqual(redoc.status_code, 200)

        schema = openapi_response.json()
        self.assertEqual(schema, app.openapi())
        self.assertEqual(
            set(schema["paths"]),
            {
                "/health",
                "/conversations",
                "/conversations/{conversation_id}",
                "/conversations/{conversation_id}/messages",
                "/memories",
                "/memories/{memory_id}",
                "/memories/{memory_id}/disable",
                "/knowledge",
                "/knowledge/{source_id}",
                "/knowledge/{source_id}/reindex",
                "/model-providers",
                "/model-profiles",
                "/model-profiles/models",
                "/model-profiles/test",
                "/model-profiles/{profile_id}",
                "/model-profiles/{profile_id}/activate",
                "/conversations/{conversation_id}/messages/stream",
                "/conversations/{conversation_id}/planning",
                "/conversations/{conversation_id}/planning/draft/stream",
                "/planning-runs/{run_id}/approve/stream",
                "/planning-runs/{run_id}/tool-approval/stream",
                "/planning-runs/{run_id}/retry/stream",
                "/planning-runs/{run_id}/revise/stream",
                "/planning-runs/{run_id}/cancel",
            },
        )
        self.assertEqual(
            set(schema["paths"]["/conversations"]),
            {"get", "post"},
        )
        self.assertEqual(set(schema["paths"]["/health"]), {"get"})
        self.assertEqual(
            set(schema["paths"]["/conversations/{conversation_id}"]),
            {"get", "patch", "delete"},
        )
        self.assertEqual(
            set(schema["paths"]["/conversations/{conversation_id}/messages"]),
            {"get", "post"},
        )
        create_operation = schema["paths"]["/conversations"]["post"]
        self.assertFalse(create_operation["requestBody"].get("required", False))
        self.assertEqual(
            create_operation["responses"]["201"]["content"]["application/json"][
                "schema"
            ]["$ref"],
            "#/components/schemas/ConversationResponse",
        )
        self.assertEqual(
            schema["paths"]["/conversations"]["get"]["responses"]["200"][
                "content"
            ]["application/json"]["schema"]["$ref"],
            "#/components/schemas/ConversationPageResponse",
        )
        self.assertNotIn(
            "content",
            schema["paths"]["/conversations/{conversation_id}"]["delete"][
                "responses"
            ]["204"],
        )
        self.assertIn(
            "409",
            schema["paths"]["/conversations/{conversation_id}/messages"]["post"][
                "responses"
            ],
        )
        self.assertEqual(
            set(schema["paths"]["/memories"]),
            {"get", "post"},
        )
        self.assertEqual(
            set(schema["paths"]["/memories/{memory_id}"]),
            {"get", "patch", "delete"},
        )
        self.assertEqual(
            set(schema["paths"]["/memories/{memory_id}/disable"]),
            {"post"},
        )
        self.assertIn(
            "200",
            schema["paths"]["/memories"]["post"]["responses"],
        )
        self.assertNotIn(
            "content",
            schema["paths"]["/memories/{memory_id}"]["delete"]["responses"][
                "204"
            ],
        )
        self.assertEqual(set(schema["paths"]["/knowledge"]), {"get", "post"})
        self.assertEqual(
            set(schema["paths"]["/knowledge/{source_id}"]),
            {"get", "delete"},
        )
        self.assertEqual(
            set(schema["paths"]["/knowledge/{source_id}/reindex"]),
            {"post"},
        )
        self.assertNotIn(
            "content",
            schema["paths"]["/knowledge/{source_id}"]["delete"]["responses"][
                "204"
            ],
        )
        self.assertIn("ConversationCreateRequest", schema["components"]["schemas"])
        self.assertIn("ConversationPatchRequest", schema["components"]["schemas"])
        self.assertIn("ConversationPageResponse", schema["components"]["schemas"])
        self.assertIn("MemoryCreateRequest", schema["components"]["schemas"])
        self.assertIn("MemoryPatchRequest", schema["components"]["schemas"])
        self.assertIn("MemoryPageResponse", schema["components"]["schemas"])
        self.assertIn("MemoryWriteResponse", schema["components"]["schemas"])
        self.assertIn("KnowledgeCreateRequest", schema["components"]["schemas"])
        self.assertIn("KnowledgePageResponse", schema["components"]["schemas"])
        self.assertIn("IndexReportResponse", schema["components"]["schemas"])
        self.assertIn("MessageCreate", schema["components"]["schemas"])
        self.assertIn("MessagePageResponse", schema["components"]["schemas"])
        self.assertIn("ErrorResponse", schema["components"]["schemas"])

        required_bodies = (
            ("/conversations/{conversation_id}", "patch"),
            ("/conversations/{conversation_id}/messages", "post"),
            ("/memories", "post"),
            ("/memories/{memory_id}", "patch"),
            ("/knowledge", "post"),
        )
        for path, method in required_bodies:
            with self.subTest(path=path, method=method):
                self.assertTrue(
                    schema["paths"][path][method]["requestBody"]["required"]
                )
        self.assertNotIn(
            "requestBody",
            schema["paths"]["/memories/{memory_id}/disable"]["post"],
        )
        self.assertNotIn(
            "requestBody",
            schema["paths"]["/knowledge/{source_id}/reindex"]["post"],
        )

        success_models = {
            ("/health", "get", "200"): "HealthResponse",
            ("/conversations", "get", "200"): "ConversationPageResponse",
            ("/conversations", "post", "201"): "ConversationResponse",
            (
                "/conversations/{conversation_id}",
                "get",
                "200",
            ): "ConversationResponse",
            (
                "/conversations/{conversation_id}",
                "patch",
                "200",
            ): "ConversationResponse",
            (
                "/conversations/{conversation_id}/messages",
                "get",
                "200",
            ): "MessagePageResponse",
            (
                "/conversations/{conversation_id}/messages",
                "post",
                "200",
            ): "ChatReplyResponse",
            ("/memories", "get", "200"): "MemoryPageResponse",
            ("/memories", "post", "201"): "MemoryWriteResponse",
            ("/memories/{memory_id}", "get", "200"): "MemoryResponse",
            ("/memories/{memory_id}", "patch", "200"): "MemoryResponse",
            (
                "/memories/{memory_id}/disable",
                "post",
                "200",
            ): "MemoryResponse",
            ("/knowledge", "get", "200"): "KnowledgePageResponse",
            ("/knowledge", "post", "201"): "KnowledgeResponse",
            ("/knowledge/{source_id}", "get", "200"): "KnowledgeResponse",
            (
                "/knowledge/{source_id}/reindex",
                "post",
                "200",
            ): "IndexReportResponse",
        }
        for (path, method, status), model in success_models.items():
            with self.subTest(path=path, method=method, status=status):
                self.assertEqual(
                    schema["paths"][path][method]["responses"][status]["content"][
                        "application/json"
                    ]["schema"]["$ref"],
                    f"#/components/schemas/{model}",
                )

        for path, operations in schema["paths"].items():
            for method, operation in operations.items():
                for status, response_schema in operation["responses"].items():
                    if status[0] not in "45" or (path, status) == (
                        "/health",
                        "503",
                    ):
                        continue
                    with self.subTest(path=path, method=method, status=status):
                        self.assertEqual(
                            response_schema["content"]["application/json"]["schema"][
                                "$ref"
                            ],
                            "#/components/schemas/ErrorResponse",
                        )

    def test_main_uses_settings_host_and_port(self) -> None:
        settings = AppSettings(
            postgres_dsn="postgresql://unused:unused@127.0.0.1:1/unused",
            http_host="127.0.0.1",
            http_port=8123,
        )
        model_client = object()
        agent_config = Mock(model="lesson-08-model", model_client=model_client)
        memory_model = Mock()
        memory_agent = Mock()
        with (
            patch.object(AppSettings, "from_env", return_value=settings),
            patch("agents.chat_backend.http.ModelProfileStore") as profile_store,
            patch(
                "agents.chat_backend.http.ChatRuntimeConfig",
                return_value=agent_config,
            ),
            patch(
                "agents.chat_backend.http.OpenAIResponsesModel",
                return_value=memory_model,
            ) as model_factory,
            patch(
                "agents.chat_backend.http.create_memory_extraction_agent",
                return_value=memory_agent,
            ) as extraction_factory,
            patch("agents.chat_backend.http.uvicorn.run") as run_server,
        ):
            profile_store.return_value.active.return_value = None
            main()
            app = run_server.call_args.args[0]
            built_memory_agent = app.state.memory_extraction._agent_factory()

        _, kwargs = run_server.call_args
        self.assertEqual(kwargs, {"host": "127.0.0.1", "port": 8123})
        self.assertIsNotNone(app.state.memory_extraction)
        self.assertIsNone(app.state.hybrid_retrieval)
        self.assertIs(built_memory_agent, memory_agent)
        model_factory.assert_called_once_with(
            model="lesson-08-model",
            client=model_client,
        )
        extraction_factory.assert_called_once_with(memory_model)

    def test_main_builds_retrieval_only_when_explicitly_enabled(self) -> None:
        settings = AppSettings(
            postgres_dsn="postgresql://unused:unused@127.0.0.1:1/unused",
            memory_retrieval_enabled=True,
        )
        agent_config = Mock(model="chat-model", model_client=object())
        sdk_client = object()
        embedding_client = Mock()
        with (
            patch.object(AppSettings, "from_env", return_value=settings),
            patch(
                "agents.chat_backend.http.ChatRuntimeConfig",
                return_value=agent_config,
            ),
            patch(
                "agents.chat_backend.http.OpenAI",
                return_value=sdk_client,
            ) as openai_factory,
            patch(
                "agents.chat_backend.http.OpenAIEmbeddingClient",
                return_value=embedding_client,
            ) as embedding_factory,
            patch("agents.chat_backend.http.uvicorn.run") as run_server,
        ):
            main()
            app = run_server.call_args.args[0]

        openai_factory.assert_called_once_with(max_retries=0)
        embedding_factory.assert_called_once_with(
            sdk_client,
            settings.embedding_model,
            settings.embedding_dimensions,
        )
        self.assertIsInstance(app.state.hybrid_retrieval, HybridRetrievalService)
        self.assertIs(
            app.state.hybrid_retrieval._embedding_client,
            embedding_client,
        )


@unittest.skipUnless(_POSTGRES_DSN, "requires MYAGENT_TEST_POSTGRES_DSN")
class HttpPostgresRestartTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        assert _POSTGRES_DSN is not None
        parts = urlsplit(_POSTGRES_DSN)
        self.database_name = f"myagent_http_{uuid4().hex}"
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

    async def test_planning_persists_refreshes_and_recovers_after_restart(
        self,
    ) -> None:
        workspace = Path(__file__).resolve().parents[1]
        settings = AppSettings(
            postgres_dsn=self.test_dsn,
            workspace_root=workspace,
        )
        keyring = MemoryKeyring()
        profiles = ModelProfileStore(keyring)
        profile = profiles.activate(
            profiles.create(
                ModelProfile(
                    id=uuid4(),
                    name="Planning test model",
                    provider="ollama",
                    protocol="chat_completions",
                    base_url="http://127.0.0.1:11434/v1",
                    model="test-model",
                    api_key=None,
                    is_active=False,
                )
            ).id
        )
        definition = {
            "schema_version": 1,
            "objective": "验证 Planning 持久化与恢复",
            "steps": [
                {
                    "step_id": "implement",
                    "title": "执行步骤",
                    "instruction": "返回步骤结果。",
                    "completion_criteria": "步骤结果已经保存。",
                    "dependency_ids": [],
                    "is_final_verification": False,
                },
                {
                    "step_id": "verify",
                    "title": "最终验证",
                    "instruction": "运行冻结验证命令。",
                    "completion_criteria": "冻结验证命令通过。",
                    "dependency_ids": ["implement"],
                    "is_final_verification": True,
                },
            ],
        }
        draft_model = SequenceResponseModel(
            [
                json.dumps(
                    {"kind": "plan", "question": None, "definition": definition},
                    ensure_ascii=False,
                )
            ]
        )
        first_app = create_app(
            settings,
            lambda: Agent(),
            model_profile_store=profiles,
        )
        first_app.state.planning_service.recover_running = AsyncMock(
            return_value=PlanningRecoveryReport(0, (), (), ())
        )
        with patch(
            "agents.chat_backend.planning_service.model_for_profile",
            return_value=draft_model,
        ):
            async with first_app.router.lifespan_context(first_app):
                transport = httpx.ASGITransport(app=first_app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test",
                ) as client:
                    created = await client.post("/conversations")
                    conversation_id = UUID(created.json()["id"])
                    drafted = await client.post(
                        f"/conversations/{conversation_id}/planning/draft/stream",
                        json={"content": "请制定验证计划"},
                    )
                    refreshed = await client.get(
                        f"/conversations/{conversation_id}/planning"
                    )
                    run_id = UUID(refreshed.json()["run_id"])
                    running = first_app.state.plans.approve(
                        run_id,
                        refreshed.json()["row_revision"],
                    )
                    assert running.definition is not None
                    execution = PlanExecution.from_events(
                        running.definition,
                        running.task_events,
                        owner_id=str(run_id),
                    )
                    implement = execution.task_store.get("implement")
                    execution.task_store.transition(
                        "implement",
                        expected_revision=implement.revision,
                        to_status=TaskStatus.RUNNING,
                    )
                    first_app.state.plans.save_execution(
                        run_id,
                        running.row_revision,
                        execution=execution,
                        status="running",
                    )

        self.assertEqual(drafted.status_code, 200)
        self.assertIn('"kind":"plan"', drafted.text)
        self.assertEqual(refreshed.json()["status"], "awaiting_approval")
        self.assertEqual(
            [step["step_id"] for step in refreshed.json()["steps"]],
            ["implement", "verify"],
        )
        second_app = create_app(
            settings,
            lambda: Agent(),
            model_profile_store=profiles,
        )
        recovery_model = SequenceResponseModel(["重启后已继续原步骤"])
        verification = VerificationResult(
            "python -m pytest",
            0,
            True,
            "passed",
        )
        with (
            patch(
                "agents.chat_backend.planning_service.model_for_profile",
                return_value=recovery_model,
            ),
            patch.object(
                VerificationRunner,
                "run",
                return_value=(verification,),
            ),
        ):
            async with second_app.router.lifespan_context(second_app):
                for _ in range(50):
                    if not second_app.state.background_tasks:
                        break
                    await asyncio.sleep(0.02)
                transport = httpx.ASGITransport(app=second_app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test",
                ) as client:
                    after_restart = await client.get(
                        f"/conversations/{conversation_id}/planning"
                    )

        self.assertEqual(after_restart.status_code, 200)
        self.assertEqual(after_restart.json()["status"], "completed")
        self.assertEqual(
            [step["status"] for step in after_restart.json()["steps"]],
            ["completed", "completed"],
        )
        self.assertEqual(
            after_restart.json()["steps"][0]["result"],
            "重启后已继续原步骤",
        )
        self.assertTrue(profile.is_active)

    async def test_http_chat_recovers_history_after_app_rebuild(self) -> None:
        first_models: list[RecordingModel] = []
        second_models: list[RecordingModel] = []

        def first_agent() -> Agent:
            model = RecordingModel("first answer")
            first_models.append(model)
            return Agent(memory=AgentMemory(), model=model)

        def second_agent() -> Agent:
            model = RecordingModel("second answer")
            second_models.append(model)
            return Agent(memory=AgentMemory(), model=model)

        settings = AppSettings(postgres_dsn=self.test_dsn)
        first_app = create_app(settings, first_agent)
        async with first_app.router.lifespan_context(first_app):
            transport = httpx.ASGITransport(app=first_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                created = await client.post("/conversations")
                conversation_id = created.json()["id"]
                first_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "first question"},
                )

        second_app = create_app(settings, second_agent)
        async with second_app.router.lifespan_context(second_app):
            transport = httpx.ASGITransport(app=second_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                second_reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "second question"},
                )
                history = await client.get(
                    f"/conversations/{conversation_id}/messages",
                    params={"page_size": 100},
                )

        self.assertEqual(first_reply.json()["content"], "first answer")
        self.assertEqual(second_reply.json()["content"], "second answer")
        self.assertEqual(
            [item["sequence"] for item in history.json()["items"]],
            list(range(1, 9)),
        )
        second_input = second_models[0].message_batches[0]
        self.assertEqual(second_input[0].content, "first question")
        self.assertEqual(second_input[-1].content, "second question")
        self.assertEqual(len(first_models), 1)
        self.assertEqual(len(second_models), 1)

    async def test_conversation_management_survives_rebuild_and_delete(self) -> None:
        settings = AppSettings(postgres_dsn=self.test_dsn)

        def agent_factory() -> Agent:
            return Agent(
                memory=AgentMemory(),
                model=RecordingModel("managed answer"),
            )

        first_app = create_app(settings, agent_factory)
        async with first_app.router.lifespan_context(first_app):
            transport = httpx.ASGITransport(app=first_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                created = await client.post(
                    "/conversations",
                    json={"title": "Persistent chat"},
                )
                conversation_id = created.json()["id"]
                archived = await client.patch(
                    f"/conversations/{conversation_id}",
                    json={"status": "archived"},
                )
                blocked = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "must not be saved"},
                )
                restored = await client.patch(
                    f"/conversations/{conversation_id}",
                    json={"status": "active"},
                )
                reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "saved question"},
                )

        second_app = create_app(settings, agent_factory)
        async with second_app.router.lifespan_context(second_app):
            transport = httpx.ASGITransport(app=second_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                fetched = await client.get(f"/conversations/{conversation_id}")
                listed = await client.get(
                    "/conversations",
                    params={"status": "active"},
                )
                history = await client.get(
                    f"/conversations/{conversation_id}/messages",
                )
                deleted = await client.delete(
                    f"/conversations/{conversation_id}",
                )
                missing = await client.get(f"/conversations/{conversation_id}")
                missing_history = await client.get(
                    f"/conversations/{conversation_id}/messages",
                )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(archived.json()["status"], "archived")
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(restored.json()["status"], "active")
        self.assertEqual(reply.status_code, 200)
        self.assertEqual(fetched.json()["title"], "Persistent chat")
        self.assertEqual(
            [item["id"] for item in listed.json()["items"]],
            [conversation_id],
        )
        self.assertTrue(history.json()["items"])
        self.assertNotIn(
            "must not be saved",
            [item["content"] for item in history.json()["items"]],
        )
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing_history.status_code, 404)

    async def test_memory_management_preserves_source_dedup_epoch_and_search(
        self,
    ) -> None:
        settings = AppSettings(postgres_dsn=self.test_dsn)

        def agent_factory() -> Agent:
            return Agent(
                memory=AgentMemory(),
                model=RecordingModel("source answer"),
            )

        app = create_app(settings, agent_factory)
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                conversation = await client.post("/conversations")
                conversation_id = UUID(conversation.json()["id"])
                reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "remember this source"},
                )
                history = await client.get(
                    f"/conversations/{conversation_id}/messages",
                )
                items = history.json()["items"]
                user_message = next(item for item in items if item["role"] == "user")
                assistant_message = next(
                    item for item in reversed(items) if item["role"] == "assistant"
                )
                source = {
                    "conversation_id": str(conversation_id),
                    "user_message_id": user_message["id"],
                    "assistant_message_id": assistant_message["id"],
                }
                payload = {
                    "kind": "fact",
                    "scope": "conversation",
                    "content": "Remember this source",
                    "source": source,
                    "tags": ["manual"],
                    "confidence": 0.9,
                }
                initial_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                created = await client.post("/memories", json=payload)
                memory_id = UUID(created.json()["record"]["id"])
                created_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                duplicate = await client.post("/memories", json=payload)
                duplicate_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                listed = await client.get(
                    "/memories",
                    params={
                        "status": "active",
                        "scope": "conversation",
                        "source_conversation_id": str(conversation_id),
                        "source_message_id": user_message["id"],
                    },
                )
                invalid_payload = {
                    **payload,
                    "content": "Invalid source",
                    "source": {
                        **source,
                        "user_message_id": str(uuid4()),
                    },
                }
                invalid_source = await client.post(
                    "/memories",
                    json=invalid_payload,
                )
                invalid_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                patched = await client.patch(
                    f"/memories/{memory_id}",
                    json={"content": "Updated memory"},
                )
                updated_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                active_hits = await asyncio.to_thread(
                    app.state.memory_store.search_keyword,
                    "Updated",
                    conversation_id,
                    10,
                )
                disabled = await client.post(f"/memories/{memory_id}/disable")
                disabled_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                disabled_again = await client.post(
                    f"/memories/{memory_id}/disable"
                )
                repeated_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                hidden_hits = await asyncio.to_thread(
                    app.state.memory_store.search_keyword,
                    "Updated",
                    conversation_id,
                    10,
                )
                deleted = await client.delete(f"/memories/{memory_id}")
                deleted_epoch = await asyncio.to_thread(
                    app.state.memory_store.get_epoch
                )
                missing = await client.get(f"/memories/{memory_id}")

        self.assertEqual(reply.status_code, 200)
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["created"])
        self.assertEqual(created_epoch, initial_epoch + 1)
        self.assertEqual(duplicate.status_code, 200)
        self.assertFalse(duplicate.json()["created"])
        self.assertEqual(duplicate.json()["record"]["id"], str(memory_id))
        self.assertEqual(duplicate_epoch, created_epoch)
        self.assertEqual(
            [item["id"] for item in listed.json()["items"]],
            [str(memory_id)],
        )
        self.assertEqual(invalid_source.status_code, 422)
        self.assertEqual(invalid_source.json()["code"], "invalid_memory")
        self.assertEqual(invalid_epoch, duplicate_epoch)
        self.assertEqual(patched.json()["content"], "Updated memory")
        self.assertEqual(updated_epoch, created_epoch + 1)
        self.assertEqual([hit.memory.id for hit in active_hits], [memory_id])
        self.assertEqual(disabled.json()["status"], "disabled")
        self.assertEqual(disabled_epoch, updated_epoch + 1)
        self.assertEqual(disabled_again.json()["status"], "disabled")
        self.assertEqual(repeated_epoch, disabled_epoch)
        self.assertEqual(hidden_hits, ())
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted_epoch, disabled_epoch + 1)
        self.assertEqual(missing.status_code, 404)

    async def test_knowledge_management_keeps_old_index_when_reindex_fails(
        self,
    ) -> None:
        settings = AppSettings(postgres_dsn=self.test_dsn)
        embedding_client = Mock()
        vector = (0.25,) * 1024
        embedding_client.embed.side_effect = (
            (vector,),
            RuntimeError("secret provider failure"),
        )
        app = create_app(
            settings,
            lambda: Agent(),
            embedding_client=embedding_client,
        )

        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(
                app=app,
                raise_app_exceptions=False,
            )
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                initial_epoch = await asyncio.to_thread(
                    app.state.knowledge_store.get_epoch
                )
                created = await client.post(
                    "/knowledge",
                    json={
                        "title": "Stable source",
                        "text": "Stable knowledge text",
                        "metadata": {"section": 1},
                    },
                )
                source_id = UUID(created.json()["id"])
                created_epoch = await asyncio.to_thread(
                    app.state.knowledge_store.get_epoch
                )
                listed = await client.get("/knowledge")
                reindexed = await client.post(f"/knowledge/{source_id}/reindex")
                indexed_epoch = await asyncio.to_thread(
                    app.state.knowledge_store.get_epoch
                )
                indexed = await client.get(f"/knowledge/{source_id}")
                old_hits = await asyncio.to_thread(
                    app.state.knowledge_store.search_keyword,
                    "Stable",
                    10,
                )
                with self.assertLogs("agents.chat_backend.http", level="ERROR"):
                    failed = await client.post(
                        f"/knowledge/{source_id}/reindex"
                    )
                failed_epoch = await asyncio.to_thread(
                    app.state.knowledge_store.get_epoch
                )
                after_failure = await client.get(f"/knowledge/{source_id}")
                current_hits = await asyncio.to_thread(
                    app.state.knowledge_store.search_keyword,
                    "Stable",
                    10,
                )
                deleted = await client.delete(f"/knowledge/{source_id}")
                deleted_epoch = await asyncio.to_thread(
                    app.state.knowledge_store.get_epoch
                )
                missing = await client.get(f"/knowledge/{source_id}")

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json()["status"], "pending")
        self.assertEqual(created_epoch, initial_epoch + 1)
        self.assertEqual(listed.json()["items"][0]["id"], str(source_id))
        self.assertEqual(reindexed.status_code, 200)
        self.assertEqual(reindexed.json()["chunk_count"], 1)
        self.assertEqual(indexed_epoch, created_epoch + 1)
        self.assertEqual(indexed.json()["status"], "indexed")
        self.assertEqual(len(old_hits), 1)
        self.assertEqual(failed.status_code, 500)
        self.assertEqual(failed.json()["code"], "internal_error")
        self.assertNotIn("secret", failed.text)
        self.assertEqual(failed_epoch, indexed_epoch)
        self.assertEqual(after_failure.json(), indexed.json())
        self.assertEqual(current_hits, old_hits)
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(deleted_epoch, indexed_epoch + 1)
        self.assertEqual(missing.status_code, 404)

    async def test_http_chat_saves_memory_and_disabled_rebuild_reads_it(self) -> None:
        settings = AppSettings(postgres_dsn=self.test_dsn)

        def chat_agent() -> Agent:
            return Agent(
                memory=AgentMemory(),
                model=RecordingModel("concise answer"),
            )

        def memory_agent() -> Agent:
            return create_memory_extraction_agent(StructuredMemoryModel())

        first_app = create_app(
            settings,
            chat_agent,
            memory_agent_factory=memory_agent,
        )
        async with first_app.router.lifespan_context(first_app):
            transport = httpx.ASGITransport(app=first_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                created = await client.post("/conversations")
                conversation_id = UUID(created.json()["id"])
                reply = await client.post(
                    f"/conversations/{conversation_id}/messages",
                    json={"content": "Please be concise"},
                )

            memories = await asyncio.to_thread(
                first_app.state.memory_store.list,
                page_size=10,
            )
            messages = await asyncio.to_thread(
                first_app.state.messages.list,
                conversation_id,
            )

        self.assertEqual(reply.status_code, 200)
        self.assertEqual(len(memories.items), 1)
        memory = memories.items[0]
        user_message = next(item for item in messages if item.role == "user")
        self.assertEqual(memory.content, "Prefers concise answers")
        self.assertEqual(memory.source.conversation_id, conversation_id)
        self.assertEqual(memory.source.user_message_id, user_message.id)
        self.assertEqual(memory.source.assistant_message_id, messages[-1].id)

        second_app = create_app(settings, chat_agent)
        self.assertIsNone(second_app.state.memory_extraction)
        async with second_app.router.lifespan_context(second_app):
            restored = await asyncio.to_thread(
                second_app.state.memory_store.list,
                page_size=10,
            )
        self.assertEqual(restored.items, memories.items)


if __name__ == "__main__":
    unittest.main()
