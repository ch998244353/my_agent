from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from openai import OpenAI
from psycopg import Error
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from starlette.exceptions import HTTPException as StarletteHTTPException

from ..agent import Agent
from ..chat_runtime import ChatRuntimeConfig, build_chat_agent
from ..contracts import ChatMessage, MessageRole, ModelRequest
from ..events import (
    ModelAttemptStartedEvent,
    ModelCompletedEvent,
    ModelFallbackEvent,
    ModelRetryScheduledEvent,
    ModelStartedEvent,
    ModelTextDeltaEvent,
    RunCompletedEvent,
    RunEvent,
    ToolCompletedEvent,
    ToolFailedEvent,
    ToolRequestedEvent,
    ToolStartedEvent,
)
from ..model_retry import run_model_request
from ..model_settings import ModelSettings
from ..models import (
    ModelErrorKind,
    OpenAIResponsesModel,
    classify_openai_model_error,
    run_non_stream_model_response,
)
from ..run_config import RunConfig
from ..workspace_manifest import WorkspaceManifest
from .conversation import (
    Conversation,
    ConversationArchivedError,
    ConversationCursor,
    ConversationMode,
    ConversationNotFoundError,
    ConversationPage,
    ConversationPatch,
    ConversationStatus,
    ConversationStore,
    MessagePage,
    MessageStore,
    StoredMessage,
)
from .database import Database
from .embedding import EmbeddingClient, OpenAIEmbeddingClient
from .knowledge import (
    IndexReport,
    KnowledgeCursor,
    KnowledgeIndexer,
    KnowledgeNotFoundError,
    KnowledgePage,
    KnowledgeSource,
    KnowledgeSourceCreate,
    KnowledgeStatus,
    KnowledgeStore,
    KnowledgeValidationError,
)
from .migration import MigrationRunner
from .memory import (
    MemoryCreate,
    MemoryCursor,
    MemoryDuplicateError,
    MemoryKind,
    MemoryNotFoundError,
    MemoryPage,
    MemoryPatch,
    MemoryRecord,
    MemorySaveResult,
    MemoryScope,
    MemorySource,
    MemoryStatus,
    MemoryStore,
    MemorySupersedeError,
    MemoryValidationError,
)
from .memory_extraction import (
    MemoryExtractionService,
    create_memory_extraction_agent,
)
from .model_profiles import (
    PROVIDER_PRESETS,
    ModelProfile,
    ModelProfileError,
    ModelProfileNotFoundError,
    ModelProfileStore,
    ModelProfileStoreError,
    model_for_profile,
    provider_public_dict,
    validate_profile,
)
from .planning_service import (
    PlanningDraftOutcome,
    PlanningInputError,
    PlanningProgressEvent,
    PlanningRunError,
    PlanningService,
    PlanningSnapshot,
)
from .planning_store import (
    ActivePlanningRunError,
    CorruptPlanningRunError,
    InvalidPlanningTransition,
    PlanStore,
    PlanningConflictError,
    PlanningRunNotFoundError,
    PlanningStatus,
)
from .retrieval import Citation, HybridRetrievalService
from .service import (
    ChatInputError,
    ChatPersistenceError,
    ChatReply,
    ChatRunError,
    ChatService,
)
from .settings import AppSettings, HealthStatus


_LOGGER = logging.getLogger(__name__)
MessageContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]
Title = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
]
MemoryContent = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
]
MemoryTag = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40),
]
MemoryTags = Annotated[tuple[MemoryTag, ...], Field(max_length=20)]
KnowledgeText = Annotated[str, StringConstraints(min_length=1, max_length=200_000)]


class _StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


# 检查用户发送的正文
class MessageCreate(BaseModel):
    content: MessageContent


# 检查历史消息查询参数
class HistoryQuery(BaseModel):
    page_size: int = Field(default=50, ge=1, le=100)
    before_sequence: int | None = Field(default=None, gt=0)


class CursorQuery(_StrictRequest):
    page_size: int = Field(default=50, ge=1, le=100)
    before_created_at: datetime | None = None
    before_id: UUID | None = None

    @model_validator(mode="after")
    def require_complete_cursor(self) -> CursorQuery:
        if (self.before_created_at is None) != (self.before_id is None):
            raise ValueError(
                "before_created_at and before_id must be provided together"
            )
        return self


class ConversationListQuery(CursorQuery):
    status: ConversationStatus | None = None


class MemoryListQuery(CursorQuery):
    status: MemoryStatus | None = None
    scope: MemoryScope | None = None
    source_conversation_id: UUID | None = None
    source_message_id: UUID | None = None


class CursorResponse(BaseModel):
    created_at: datetime
    id: UUID


class ConversationCreateRequest(_StrictRequest):
    title: Title = "New conversation"


class ConversationPatchRequest(_StrictRequest):
    title: Title | None = None
    status: ConversationStatus | None = None

    @model_validator(mode="after")
    def require_change(self) -> ConversationPatchRequest:
        if self.title is None and self.status is None:
            raise ValueError("title or status is required")
        return self


class ConversationResponse(BaseModel):
    id: UUID
    title: str
    status: ConversationStatus
    mode: ConversationMode
    created_at: datetime


class ConversationPageResponse(BaseModel):
    items: list[ConversationResponse]
    next_cursor: CursorResponse | None


class MemorySourceRequest(_StrictRequest):
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID

    @model_validator(mode="after")
    def require_distinct_messages(self) -> MemorySourceRequest:
        if self.user_message_id == self.assistant_message_id:
            raise ValueError("user and assistant message ids must differ")
        return self


class MemorySourceResponse(BaseModel):
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID


class MemoryCreateRequest(_StrictRequest):
    kind: MemoryKind
    scope: MemoryScope
    content: MemoryContent
    source: MemorySourceRequest
    tags: MemoryTags = ()
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, allow_inf_nan=False)


class MemoryPatchRequest(_StrictRequest):
    content: MemoryContent | None = None
    kind: MemoryKind | None = None
    tags: MemoryTags | None = None
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def require_change(self) -> MemoryPatchRequest:
        if all(
            value is None
            for value in (self.content, self.kind, self.tags, self.confidence)
        ):
            raise ValueError("content, kind, tags, or confidence is required")
        return self


class MemoryResponse(BaseModel):
    id: UUID
    kind: MemoryKind
    scope: MemoryScope
    status: MemoryStatus
    content: str
    tags: list[str]
    confidence: float
    source: MemorySourceResponse
    created_at: datetime
    updated_at: datetime
    supersedes_id: UUID | None


class MemoryWriteResponse(BaseModel):
    record: MemoryResponse
    created: bool


class MemoryPageResponse(BaseModel):
    items: list[MemoryResponse]
    next_cursor: CursorResponse | None


class KnowledgeCreateRequest(_StrictRequest):
    title: Title
    text: KnowledgeText
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if value.isspace():
            raise ValueError("text must not be blank")
        return value


class KnowledgeResponse(BaseModel):
    id: UUID
    title: str
    text: str
    metadata: dict[str, JsonValue]
    status: KnowledgeStatus
    embedding_model: str | None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None


class KnowledgePageResponse(BaseModel):
    items: list[KnowledgeResponse]
    next_cursor: CursorResponse | None


class IndexReportResponse(BaseModel):
    source_id: UUID
    chunk_count: int
    embedding_model: str
    indexed_at: datetime


class RunTraceEntryResponse(BaseModel):
    stage: Literal[
        "retrieval", "model", "retry", "tool", "saving", "done", "error"
    ]
    message: str
    step_number: int | None = None


# 这表示一条已经保存的消息，包含消息身份、所属会话、顺序、角色、正文和数据库写入时间
class StoredMessageResponse(BaseModel):
    id: UUID
    conversation_id: UUID
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime
    run_trace: list[RunTraceEntryResponse] = Field(default_factory=list)


class MessagePageResponse(BaseModel):
    items: list[StoredMessageResponse]
    before_sequence: int | None


class CitationResponse(BaseModel):
    marker: str
    source_type: Literal["memory", "knowledge"]
    item_id: UUID
    source_id: UUID | None
    title: str | None


class ChatReplyResponse(BaseModel):
    conversation_id: UUID
    message_id: UUID
    sequence: int
    content: str
    citations: list[CitationResponse] = Field(default_factory=list)
    run_trace: list[RunTraceEntryResponse] = Field(default_factory=list)


class PlanningDraftRequest(_StrictRequest):
    content: MessageContent
    expected_row_revision: int | None = Field(default=None, gt=0)


class PlanningControlRequest(_StrictRequest):
    expected_row_revision: int = Field(gt=0)


class PlanningReviseRequest(PlanningControlRequest):
    feedback: MessageContent


class PlanningCancelRequest(PlanningControlRequest):
    reason: MessageContent


class PlanningToolApprovalRequest(PlanningControlRequest):
    tool_name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ]
    call_id: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    ]
    decision: Literal["approve", "reject"]
    rejection_message: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000),
    ] | None = None

    @model_validator(mode="after")
    def require_rejection_message(self) -> PlanningToolApprovalRequest:
        if self.decision == "reject" and self.rejection_message is None:
            raise ValueError("rejection_message is required when decision is reject")
        return self


class PlanningStepResponse(BaseModel):
    step_id: str
    title: str
    instruction: str
    completion_criteria: str
    dependency_ids: list[str]
    is_final_verification: bool
    status: Literal[
        "pending", "running", "completed", "failed", "blocked", "cancelled"
    ]
    blocked_by: list[str]
    result: str | None
    error: str | None


class PlanningApprovalResponse(BaseModel):
    tool_name: str
    call_id: str
    summary: str
    reason: str | None


class PlanningSnapshotResponse(BaseModel):
    run_id: UUID
    conversation_id: UUID
    version: int
    row_revision: int
    status: PlanningStatus
    objective: str
    plan_markdown: str | None
    current_step_id: str | None
    completed_step_ids: list[str]
    pending_approval_keys: list[tuple[str, str]]
    steps: list[PlanningStepResponse]
    pending_approvals: list[PlanningApprovalResponse]
    allowed_actions: list[str]
    last_error: str | None


class HealthResponse(BaseModel):
    service: str
    status: Literal["ready", "unavailable", "disabled"]
    detail: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[dict[str, object]] | None = None


_PLANNING_ERROR_RESPONSES = {
    404: {"model": ErrorResponse},
    409: {"model": ErrorResponse},
    422: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


class ModelProviderResponse(BaseModel):
    id: str
    name: str
    protocol: Literal["responses", "chat_completions"]
    base_url: str
    api_key_required: bool
    compatibility_note: str | None = None


class ModelProfileResponse(BaseModel):
    id: UUID
    name: str
    provider: str
    protocol: Literal["responses", "chat_completions"]
    base_url: str
    model: str
    is_active: bool
    has_api_key: bool


class ModelProfileCreateRequest(_StrictRequest):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    provider: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)] | None = None
    api_key: Annotated[str, StringConstraints(min_length=1, max_length=20_000)] | None = None


class ModelProfilePatchRequest(_StrictRequest):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)] | None = None
    provider: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)] | None = None
    base_url: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2_000)] | None = None
    api_key: Annotated[str, StringConstraints(min_length=1, max_length=20_000)] | None = None

    @model_validator(mode="after")
    def require_change(self) -> ModelProfilePatchRequest:
        if all(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("at least one profile field is required")
        return self


class ModelProfileTestRequest(_StrictRequest):
    profile_id: UUID | None = None
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
    ] | None = None
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def require_profile_or_settings(self) -> ModelProfileTestRequest:
        if self.profile_id is None and (self.provider is None or self.model is None):
            raise ValueError("profile_id or provider and model are required")
        return self


class ModelProfileTestResponse(BaseModel):
    ok: bool
    message: str


class ModelProfileModelsRequest(_StrictRequest):
    profile_id: UUID | None = None
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None

    @model_validator(mode="after")
    def require_profile_or_provider(self) -> ModelProfileModelsRequest:
        if self.profile_id is None and self.provider is None:
            raise ValueError("profile_id or provider is required")
        return self


class ModelProfileModelsResponse(BaseModel):
    ok: bool
    models: list[str] = Field(default_factory=list)
    message: str


class KnowledgeIndexUnavailableError(RuntimeError):
    pass


_PROFILE_ERROR_MESSAGES = {
    ModelErrorKind.AUTHENTICATION: "API Key 无效或没有调用权限。",
    ModelErrorKind.QUOTA: "API 额度不足或账户未启用计费。",
    ModelErrorKind.RATE_LIMIT: "请求过于频繁，请稍后重试。",
    ModelErrorKind.TRANSPORT: "无法连接厂商地址，请检查 Base URL 和网络。",
    ModelErrorKind.SERVER: "厂商服务暂时不可用，请稍后重试。",
    ModelErrorKind.INVALID_REQUEST: "厂商拒绝了请求，请检查模型 ID 和 Base URL。",
}


def _profile_error_message(exc: BaseException) -> str:
    error = classify_openai_model_error(exc)
    return _PROFILE_ERROR_MESSAGES.get(
        error.kind,
        "连接失败，请检查地址、模型名称和 API Key。",
    )


def _list_model_ids(client: object) -> list[str]:
    page = client.with_options(max_retries=0, timeout=10).models.list()
    model_ids: list[str] = []
    seen: set[str] = set()
    for item in page:
        model_id = getattr(item, "id", None)
        if not isinstance(model_id, str) or not model_id.strip():
            continue
        # ponytail: 兼容接口没有统一能力字段；有统一元数据后替换名称过滤。
        if any(
            marker in model_id.lower()
            for marker in (
                "embed",
                "image",
                "moderation",
                "rerank",
                "whisper",
                "tts",
                "speech",
                "audio",
                "transcri",
                "bge",
                "gte-",
                "e5-",
            )
        ):
            continue
        if model_id not in seen:
            seen.add(model_id)
            model_ids.append(model_id)
    return model_ids


def conversation_response(conversation: Conversation) -> ConversationResponse:
    return ConversationResponse(
        id=conversation.id,
        title=conversation.title,
        status=conversation.status,
        mode=conversation.mode,
        created_at=conversation.created_at,
    )


def _cursor_response(created_at: datetime, item_id: UUID) -> CursorResponse:
    return CursorResponse(created_at=created_at, id=item_id)


def conversation_cursor_from_query(
    query: CursorQuery,
) -> ConversationCursor | None:
    if query.before_created_at is None:
        return None
    assert query.before_id is not None
    return ConversationCursor(query.before_created_at, query.before_id)


def memory_cursor_from_query(query: CursorQuery) -> MemoryCursor | None:
    if query.before_created_at is None:
        return None
    assert query.before_id is not None
    return MemoryCursor(query.before_created_at, query.before_id)


def knowledge_cursor_from_query(query: CursorQuery) -> KnowledgeCursor | None:
    if query.before_created_at is None:
        return None
    assert query.before_id is not None
    return KnowledgeCursor(query.before_created_at, query.before_id)


def conversation_patch_from_request(
    request: ConversationPatchRequest,
) -> ConversationPatch:
    return ConversationPatch(title=request.title, status=request.status)


def conversation_page_response(page: ConversationPage) -> ConversationPageResponse:
    cursor = page.next_cursor
    return ConversationPageResponse(
        items=[conversation_response(item) for item in page.items],
        next_cursor=(
            None
            if cursor is None
            else _cursor_response(cursor.created_at, cursor.conversation_id)
        ),
    )


def memory_create_from_request(request: MemoryCreateRequest) -> MemoryCreate:
    source = request.source
    return MemoryCreate(
        kind=request.kind,
        scope=request.scope,
        content=request.content,
        source=MemorySource(
            source.conversation_id,
            source.user_message_id,
            source.assistant_message_id,
        ),
        tags=request.tags,
        confidence=request.confidence,
    )


def memory_patch_from_request(request: MemoryPatchRequest) -> MemoryPatch:
    return MemoryPatch(
        content=request.content,
        kind=request.kind,
        tags=request.tags,
        confidence=request.confidence,
    )


def memory_source_response(source: MemorySource) -> MemorySourceResponse:
    return MemorySourceResponse(
        conversation_id=source.conversation_id,
        user_message_id=source.user_message_id,
        assistant_message_id=source.assistant_message_id,
    )


def memory_response(memory: MemoryRecord) -> MemoryResponse:
    return MemoryResponse(
        id=memory.id,
        kind=memory.kind,
        scope=memory.scope,
        status=memory.status,
        content=memory.content,
        tags=list(memory.tags),
        confidence=memory.confidence,
        source=memory_source_response(memory.source),
        created_at=memory.created_at,
        updated_at=memory.updated_at,
        supersedes_id=memory.supersedes_id,
    )


def memory_write_response(result: MemorySaveResult) -> MemoryWriteResponse:
    return MemoryWriteResponse(
        record=memory_response(result.record),
        created=result.created,
    )


def memory_page_response(page: MemoryPage) -> MemoryPageResponse:
    cursor = page.next_cursor
    return MemoryPageResponse(
        items=[memory_response(item) for item in page.items],
        next_cursor=(
            None
            if cursor is None
            else _cursor_response(cursor.created_at, cursor.memory_id)
        ),
    )


def knowledge_create_from_request(
    request: KnowledgeCreateRequest,
) -> KnowledgeSourceCreate:
    return KnowledgeSourceCreate(
        title=request.title,
        text=request.text,
        metadata=dict(request.metadata),
    )


def knowledge_response(source: KnowledgeSource) -> KnowledgeResponse:
    return KnowledgeResponse(
        id=source.id,
        title=source.title,
        text=source.text,
        metadata=source.metadata,
        status=source.status,
        embedding_model=source.embedding_model,
        created_at=source.created_at,
        updated_at=source.updated_at,
        indexed_at=source.indexed_at,
    )


def knowledge_page_response(page: KnowledgePage) -> KnowledgePageResponse:
    cursor = page.next_cursor
    return KnowledgePageResponse(
        items=[knowledge_response(item) for item in page.items],
        next_cursor=(
            None
            if cursor is None
            else _cursor_response(cursor.created_at, cursor.id)
        ),
    )


def index_report_response(report: IndexReport) -> IndexReportResponse:
    return IndexReportResponse(
        source_id=report.source_id,
        chunk_count=report.chunk_count,
        embedding_model=report.embedding_model,
        indexed_at=report.indexed_at,
    )


def stored_message_response(message: StoredMessage) -> StoredMessageResponse:
    return StoredMessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        sequence=message.sequence,
        role=message.role,
        content=message.content,
        created_at=message.created_at,
        run_trace=[
            RunTraceEntryResponse.model_validate(item) for item in message.run_trace
        ],
    )


def message_page_response(page: MessagePage) -> MessagePageResponse:
    return MessagePageResponse(
        items=[stored_message_response(item) for item in page.items],
        before_sequence=page.before_sequence,
    )


def citation_response(citation: Citation) -> CitationResponse:
    return CitationResponse(
        marker=citation.marker,
        source_type=citation.source_type,
        item_id=citation.item_id,
        source_id=citation.source_id,
        title=citation.title,
    )


def chat_reply_response(
    reply: ChatReply,
    run_trace: Sequence[dict[str, object]] = (),
) -> ChatReplyResponse:
    return ChatReplyResponse(
        conversation_id=reply.conversation_id,
        message_id=reply.message_id,
        sequence=reply.sequence,
        content=reply.content,
        citations=[citation_response(citation) for citation in reply.citations],
        run_trace=[RunTraceEntryResponse.model_validate(item) for item in run_trace],
    )


def planning_snapshot_response(
    snapshot: PlanningSnapshot,
) -> PlanningSnapshotResponse:
    return PlanningSnapshotResponse(
        run_id=snapshot.run_id,
        conversation_id=snapshot.conversation_id,
        version=snapshot.version,
        row_revision=snapshot.row_revision,
        status=snapshot.status,
        objective=snapshot.objective,
        plan_markdown=snapshot.plan_markdown,
        current_step_id=snapshot.current_step_id,
        completed_step_ids=list(snapshot.completed_step_ids),
        pending_approval_keys=list(snapshot.pending_approval_keys),
        steps=[
            PlanningStepResponse(
                step_id=step.step_id,
                title=step.title,
                instruction=step.instruction,
                completion_criteria=step.completion_criteria,
                dependency_ids=list(step.dependency_ids),
                is_final_verification=step.is_final_verification,
                status=step.status.value,
                blocked_by=list(step.blocked_by),
                result=step.result,
                error=step.error,
            )
            for step in snapshot.steps
        ],
        pending_approvals=[
            PlanningApprovalResponse(
                tool_name=approval.tool_name,
                call_id=approval.call_id,
                summary=approval.summary,
                reason=approval.reason,
            )
            for approval in snapshot.pending_approvals
        ],
        allowed_actions=list(snapshot.allowed_actions),
        last_error=snapshot.last_error,
    )


def _planning_error_payload(exc: Exception) -> dict[str, object]:
    if isinstance(exc, PlanningRunNotFoundError):
        return {"code": "planning_not_found", "message": "计划不存在。"}
    if isinstance(exc, PlanningConflictError):
        return {"code": "planning_conflict", "message": "计划已发生变化，请刷新后重试。"}
    if isinstance(exc, (ActivePlanningRunError, InvalidPlanningTransition)):
        return {"code": "planning_state_conflict", "message": "当前计划状态不允许此操作。"}
    if isinstance(exc, PlanningInputError):
        return {"code": "invalid_planning_request", "message": "规划请求不合法。"}
    if isinstance(exc, CorruptPlanningRunError):
        return {"code": "planning_data_corrupt", "message": "计划数据无法读取。"}
    return {
        "code": "planning_unavailable",
        "message": "规划未完成，请重试；若重复失败，请检查当前模型是否支持 JSON 输出。",
    }


def health_response(status: HealthStatus) -> HealthResponse:
    return HealthResponse(
        service=status.service,
        status=status.status,
        detail=status.detail,
    )


def model_profile_response(profile: ModelProfile) -> ModelProfileResponse:
    return ModelProfileResponse.model_validate(profile.public_dict())


def _sse(event: str, payload: dict[str, object]) -> str:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )


def _run_status(event: RunEvent) -> dict[str, object] | None:
    base: dict[str, object] = {
        "sequence": event.sequence,
        "step_number": event.step_number,
    }
    if isinstance(event, ModelStartedEvent):
        return {**base, "stage": "model", "message": "正在准备模型请求"}
    if isinstance(event, ModelAttemptStartedEvent):
        return {
            **base,
            "stage": "model",
            "message": f"正在请求 {event.model_name}",
            "attempt_id": event.attempt_id,
        }
    if isinstance(event, ModelRetryScheduledEvent):
        return {
            **base,
            "stage": "retry",
            "message": f"请求失败，{event.delay_seconds:.1f} 秒后重试",
            "attempt_id": event.attempt_id,
        }
    if isinstance(event, ModelFallbackEvent):
        return {
            **base,
            "stage": "retry",
            "message": f"切换到 {event.to_model}",
            "attempt_id": event.attempt_id,
        }
    if isinstance(event, ToolRequestedEvent):
        return {
            **base,
            "stage": "tool",
            "message": f"模型请求调用工具 {event.tool_name}",
            "tool_name": event.tool_name,
            "call_id": event.call_id,
        }
    if isinstance(event, ToolStartedEvent):
        return {
            **base,
            "stage": "tool",
            "message": f"正在运行工具 {event.tool_name}",
            "tool_name": event.tool_name,
            "call_id": event.call_id,
        }
    if isinstance(event, ToolCompletedEvent):
        return {
            **base,
            "stage": "tool",
            "message": f"工具 {event.tool_name} 已完成",
            "tool_name": event.tool_name,
            "call_id": event.call_id,
        }
    if isinstance(event, ToolFailedEvent):
        return {
            **base,
            "stage": "tool",
            "message": f"工具 {event.tool_name} 失败",
            "tool_name": event.tool_name,
            "call_id": event.call_id,
        }
    if isinstance(event, ModelCompletedEvent):
        return {**base, "stage": "model", "message": "模型回复已接收"}
    if isinstance(event, RunCompletedEvent):
        return {**base, "stage": "saving", "message": "Agent 循环已完成，正在保存结果"}
    return None


def _error(
    status: int,
    code: str,
    message: str,
    details: list[dict[str, object]] | None = None,
) -> JSONResponse:
    content = ErrorResponse(
        code=code,
        message=message,
        details=details,
    ).model_dump(mode="json", exclude_none=True)
    return JSONResponse(status_code=status, content=content)


async def _validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = [
        {
            "location": [
                part
                for part in error.get("loc", ())
                if isinstance(part, str | int)
            ],
            "type": (
                error["type"]
                if isinstance(error.get("type"), str)
                else "value_error"
            ),
        }
        for error in exc.errors()
    ]
    return _error(
        422,
        "invalid_request",
        "Request validation failed.",
        details,
    )


def _log_exception(message: str, exc: Exception) -> None:
    _LOGGER.error(
        message,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


async def _route_error(
    _request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    if exc.status_code == 404:
        return _error(404, "not_found", "Resource was not found.")
    if exc.status_code == 405:
        return _error(405, "method_not_allowed", "Method is not allowed.")
    return _error(exc.status_code, "http_error", "Request failed.")


async def _http_error(_request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, ChatInputError):
        return _error(422, "invalid_message", "Message content is invalid.")
    if isinstance(exc, ConversationNotFoundError):
        return _error(404, "conversation_not_found", "Conversation was not found.")
    if isinstance(exc, ConversationArchivedError):
        return _error(409, "conversation_archived", "Conversation is archived.")
    if isinstance(exc, PlanningRunNotFoundError):
        return _error(404, "planning_not_found", "Planning run was not found.")
    if isinstance(exc, PlanningConflictError):
        return _error(409, "planning_conflict", "计划已发生变化，请刷新后重试。")
    if isinstance(exc, (ActivePlanningRunError, InvalidPlanningTransition)):
        return _error(
            409,
            "planning_state_conflict",
            "当前计划状态不允许此操作，请使用规划面板中的可用按钮。",
        )
    if isinstance(exc, PlanningInputError):
        return _error(422, "invalid_planning_request", "规划输入不合法。")
    if isinstance(exc, CorruptPlanningRunError):
        return _error(503, "planning_data_corrupt", "Planning data is unavailable.")
    if isinstance(exc, PlanningRunError):
        return _error(503, "planning_unavailable", "规划暂时不可用。")
    if isinstance(exc, MemoryNotFoundError):
        return _error(404, "memory_not_found", "Memory was not found.")
    if isinstance(exc, KnowledgeNotFoundError):
        return _error(404, "knowledge_not_found", "Knowledge was not found.")
    if isinstance(exc, MemoryDuplicateError):
        return _error(409, "memory_duplicate", "Memory already exists.")
    if isinstance(exc, MemorySupersedeError):
        return _error(409, "memory_conflict", "Memory update conflicts.")
    if isinstance(exc, MemoryValidationError):
        return _error(422, "invalid_memory", "Memory input is invalid.")
    if isinstance(exc, KnowledgeValidationError):
        return _error(422, "invalid_knowledge", "Knowledge input is invalid.")
    if isinstance(exc, KnowledgeIndexUnavailableError):
        return _error(
            503,
            "embedding_unavailable",
            "Knowledge indexing is temporarily unavailable.",
        )
    if isinstance(exc, ModelProfileNotFoundError):
        return _error(404, "model_profile_not_found", "Model profile was not found.")
    if isinstance(exc, ModelProfileError):
        return _error(422, "invalid_model_profile", "Model profile is invalid.")
    if isinstance(exc, ModelProfileStoreError):
        return _error(503, "keyring_unavailable", "System keyring is unavailable.")
    if isinstance(exc, ChatRunError):
        return _error(503, "chat_unavailable", "Chat is temporarily unavailable.")
    if isinstance(exc, Error) or (
        isinstance(exc, ChatPersistenceError)
        and isinstance(exc.__cause__, Error)
    ):
        return _error(
            503,
            "database_unavailable",
            "Database is temporarily unavailable.",
        )
    if isinstance(exc, ChatPersistenceError):
        _log_exception("Chat persistence result was inconsistent.", exc)
        return _error(
            500,
            "persistence_inconsistent",
            "The chat result could not be saved consistently.",
        )

    _log_exception("Unhandled chat backend HTTP error.", exc)
    return _error(500, "internal_error", "An internal error occurred.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, _route_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    for error_type in (
        ChatInputError,
        ConversationNotFoundError,
        ConversationArchivedError,
        PlanningRunNotFoundError,
        PlanningConflictError,
        ActivePlanningRunError,
        InvalidPlanningTransition,
        PlanningInputError,
        CorruptPlanningRunError,
        PlanningRunError,
        MemoryNotFoundError,
        KnowledgeNotFoundError,
        MemoryDuplicateError,
        MemorySupersedeError,
        MemoryValidationError,
        KnowledgeValidationError,
        KnowledgeIndexUnavailableError,
        ModelProfileNotFoundError,
        ModelProfileError,
        ModelProfileStoreError,
        ChatRunError,
        Error,
        ChatPersistenceError,
        Exception,
    ):
        app.add_exception_handler(error_type, _http_error)


# 构造整个后端程序
def create_app(
    settings: AppSettings,
    agent_factory: Callable[[], Agent],
    run_config: RunConfig | None = None,
    memory_agent_factory: Callable[[], Agent] | None = None,
    embedding_client: EmbeddingClient | None = None,
    model_profile_store: ModelProfileStore | None = None,
) -> FastAPI:
    database = Database(settings.postgres_dsn)
    migration = MigrationRunner(database, Path(__file__).with_name("migrations"))
    conversations = ConversationStore(database)
    messages = MessageStore(database)
    memory_store = MemoryStore(database)
    knowledge_store = KnowledgeStore(database)
    knowledge_indexer = (
        None
        if embedding_client is None
        else KnowledgeIndexer(
            knowledge_store,
            embedding_client,
            settings.embedding_model,
        )
    )
    memory_extraction = (
        None
        if memory_agent_factory is None
        else MemoryExtractionService(memory_agent_factory, memory_store)
    )
    hybrid_retrieval = (
        None
        if embedding_client is None
        else HybridRetrievalService(
            memory_store,
            knowledge_store,
            embedding_client,
            settings.embedding_model,
            top_k=settings.retrieval_top_k,
            max_chunks=settings.max_context_chunks,
            max_chars=settings.max_context_chars,
        )
    )
    profiles = model_profile_store or ModelProfileStore()
    plans = PlanStore(database)
    planning_service = PlanningService(
        plans,
        messages,
        profiles,
        lambda: WorkspaceManifest(
            root=settings.workspace_root,
            default_test_command=settings.workspace_test_command,
        ),
    )
    background_tasks: set[asyncio.Task[object]] = set()

    def track_background(task: asyncio.Task[object]) -> asyncio.Task[object]:
        background_tasks.add(task)

        def finished(done: asyncio.Task[object]) -> None:
            background_tasks.discard(done)
            if done.cancelled():
                return
            exception = done.exception()
            if exception is not None:
                _log_exception("Planning background task failed.", exception)

        task.add_done_callback(finished)
        return task

    def configured_agent_factory() -> Agent:
        agent = agent_factory()
        if model_profile_store is None:
            return agent
        try:
            profile = profiles.active()
        except ModelProfileStoreError:
            return agent
        if profile is None:
            return agent
        return agent.clone(model=model_for_profile(profile))

    chat_service = ChatService(
        configured_agent_factory,
        conversations,
        messages,
        run_config,
        memory_extraction,
        hybrid_retrieval=hybrid_retrieval,
    )


    # 管理数据库生命周期
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            await asyncio.to_thread(database.open)  # 打开数据库连接池
            await asyncio.to_thread(migration.upgrade)  # 执行数据库迁移
            track_background(asyncio.create_task(planning_service.recover_running()))
            yield
        finally:
            for task in tuple(background_tasks):
                task.cancel()
            if background_tasks:
                await asyncio.gather(*tuple(background_tasks), return_exceptions=True)
            await asyncio.to_thread(database.close)

    app = FastAPI(lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.migration = migration
    app.state.conversations = conversations
    app.state.messages = messages
    app.state.memory_store = memory_store
    app.state.knowledge_store = knowledge_store
    app.state.knowledge_indexer = knowledge_indexer
    app.state.memory_extraction = memory_extraction
    app.state.hybrid_retrieval = hybrid_retrieval
    app.state.chat_service = chat_service
    app.state.model_profiles = profiles
    app.state.plans = plans
    app.state.planning_service = planning_service
    app.state.background_tasks = background_tasks
    register_error_handlers(app)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    static_dir = Path(__file__).with_name("static")

    @app.get("/", include_in_schema=False)
    def frontend() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/assets/app.css", include_in_schema=False)
    def frontend_css() -> FileResponse:
        return FileResponse(static_dir / "app.css", media_type="text/css")

    @app.get("/assets/app.js", include_in_schema=False)
    def frontend_js() -> FileResponse:
        return FileResponse(
            static_dir / "app.js",
            media_type="application/javascript",
        )

    def planning_stream(
        action: str,
        request: Request,
        operation: Callable[
            [Callable[[PlanningProgressEvent], None]],
            Awaitable[object],
        ],
        *,
        conversation_id: UUID | None = None,
        run_id: UUID | None = None,
    ) -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            queue: asyncio.Queue[PlanningProgressEvent] = asyncio.Queue()
            connected = True
            active_run_id = run_id
            last_revision: int | None = None
            run_trace: list[dict[str, object]] = []

            def remember_status(status: dict[str, object]) -> None:
                comparable = {
                    key: status.get(key)
                    for key in ("stage", "message", "step_number")
                    if status.get(key) is not None
                }
                if not run_trace or run_trace[-1] != comparable:
                    run_trace.append(comparable)

            def on_event(progress: PlanningProgressEvent) -> None:
                nonlocal active_run_id
                active_run_id = progress.planning_run_id
                if connected:
                    queue.put_nowait(progress)

            start: dict[str, object] = {"action": action}
            if conversation_id is not None:
                start["conversation_id"] = str(conversation_id)
            if run_id is not None:
                start["run_id"] = str(run_id)
            yield _sse("start", start)
            opening_status = {
                "stage": "model",
                "message": (
                    "正在创建规划任务"
                    if action in ("draft", "revise")
                    else "正在继续执行规划任务"
                ),
            }
            remember_status(opening_status)
            yield _sse("status", opening_status)

            task = track_background(asyncio.create_task(operation(on_event)))
            try:
                while not task.done() or not queue.empty():
                    if await request.is_disconnected():
                        connected = False
                        return
                    try:
                        progress = await asyncio.wait_for(
                            queue.get(),
                            timeout=0.15,
                        )
                    except asyncio.TimeoutError:
                        progress = None
                    if progress is not None:
                        status = _run_status(progress.event)
                        if status is not None:
                            remember_status(status)
                            yield _sse(
                                "status",
                                {
                                    **status,
                                    "planning_run_id": str(
                                        progress.planning_run_id
                                    ),
                                    "task_id": progress.task_id,
                                },
                            )

                    if active_run_id is not None:
                        snapshot = await asyncio.to_thread(
                            planning_service.get_snapshot,
                            active_run_id,
                        )
                        if snapshot.row_revision != last_revision:
                            last_revision = snapshot.row_revision
                            yield _sse(
                                "snapshot",
                                planning_snapshot_response(snapshot).model_dump(
                                    mode="json"
                                ),
                            )

                result = await task
                if isinstance(result, PlanningDraftOutcome):
                    snapshot = result.snapshot
                    saved_status = {
                        "stage": "saving",
                        "message": "规划结果已解析并保存",
                    }
                    remember_status(saved_status)
                    yield _sse("status", saved_status)
                    completed_status = {
                        "stage": "done",
                        "message": (
                            "计划已生成，等待你的审批"
                            if result.kind == "plan"
                            else "补充问题已生成，等待你的回答"
                        ),
                    }
                    remember_status(completed_status)
                    run_trace = run_trace[-200:]
                    try:
                        await asyncio.to_thread(
                            messages.set_run_trace,
                            result.message_id,
                            run_trace,
                        )
                    except Exception:
                        _LOGGER.warning("Could not persist planning run trace")
                        run_trace.append(
                            {"stage": "error", "message": "规划运行详情未能持久化"}
                        )
                        run_trace = run_trace[-200:]
                    yield _sse("status", completed_status)
                    done: dict[str, object] = {
                        "kind": result.kind,
                        "content": result.content,
                        "message_id": str(result.message_id),
                        "run_trace": [
                            RunTraceEntryResponse.model_validate(item).model_dump(
                                mode="json"
                            )
                            for item in run_trace
                        ],
                        "snapshot": planning_snapshot_response(
                            snapshot
                        ).model_dump(mode="json"),
                    }
                elif isinstance(result, PlanningSnapshot):
                    snapshot = result
                    done = {
                        "snapshot": planning_snapshot_response(
                            snapshot
                        ).model_dump(mode="json")
                    }
                else:
                    raise PlanningRunError("planning result is invalid")
                if snapshot.row_revision != last_revision:
                    yield _sse(
                        "snapshot",
                        planning_snapshot_response(snapshot).model_dump(mode="json"),
                    )
                yield _sse("done", done)
            except asyncio.CancelledError:
                connected = False
                raise
            except Exception as exc:
                yield _sse("error", _planning_error_payload(exc))
            finally:
                connected = False

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )

    def require_planning_action(
        run_id: UUID,
        expected_row_revision: int,
        action: str,
    ) -> None:
        snapshot = planning_service.get_snapshot(run_id)
        if snapshot.row_revision != expected_row_revision:
            raise PlanningConflictError(
                run_id,
                expected_row_revision,
                snapshot.row_revision,
            )
        if action not in snapshot.allowed_actions:
            raise InvalidPlanningTransition(
                run_id,
                snapshot.status,
                action,
            )

    def require_planning_draft(
        conversation_id: UUID,
        expected_row_revision: int | None,
    ) -> None:
        conversations.get(conversation_id)
        snapshot = planning_service.get_latest_snapshot(conversation_id)
        if snapshot is None or snapshot.status in {
            "completed",
            "failed",
            "cancelled",
            "superseded",
        }:
            if expected_row_revision is not None:
                raise PlanningInputError(
                    "expected_row_revision requires an active draft"
                )
            return
        if snapshot.status != "drafting":
            raise InvalidPlanningTransition(
                snapshot.run_id,
                snapshot.status,
                "draft",
            )
        if expected_row_revision is None:
            raise PlanningInputError(
                "expected_row_revision is required for an active draft"
            )
        if snapshot.row_revision != expected_row_revision:
            raise PlanningConflictError(
                snapshot.run_id,
                expected_row_revision,
                snapshot.row_revision,
            )

    @app.get("/model-providers", response_model=list[ModelProviderResponse])
    def list_model_providers() -> list[ModelProviderResponse]:
        return [
            ModelProviderResponse.model_validate(provider_public_dict(preset))
            for preset in PROVIDER_PRESETS
        ]

    @app.get(
        "/model-profiles",
        response_model=list[ModelProfileResponse],
        responses={503: {"model": ErrorResponse}},
    )
    def list_model_profiles() -> list[ModelProfileResponse]:
        return [model_profile_response(profile) for profile in profiles.list()]

    @app.post(
        "/model-profiles",
        response_model=ModelProfileResponse,
        status_code=201,
        responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def create_model_profile(
        request: ModelProfileCreateRequest,
    ) -> ModelProfileResponse:
        profile = validate_profile(**request.model_dump())
        return model_profile_response(profiles.create(profile))

    @app.post(
        "/model-profiles/models",
        response_model=ModelProfileModelsResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def list_model_profile_models(
        request: ModelProfileModelsRequest,
    ) -> ModelProfileModelsResponse:
        if request.profile_id is not None:
            profile = profiles.get(request.profile_id)
        else:
            profile = validate_profile(
                name="Model discovery",
                provider=request.provider,
                model="model-discovery",
                base_url=request.base_url,
                api_key=request.api_key,
            )
        try:
            model_ids = await asyncio.to_thread(
                _list_model_ids,
                model_for_profile(profile).client,
            )
        except Exception as exc:
            return ModelProfileModelsResponse(
                ok=False,
                message=_profile_error_message(exc),
            )
        if not model_ids:
            return ModelProfileModelsResponse(
                ok=False,
                message="没有发现可聊天模型，请先安装或开通聊天模型。",
            )
        return ModelProfileModelsResponse(
            ok=True,
            models=model_ids,
            message=f"已获取 {len(model_ids)} 个模型。",
        )

    @app.patch(
        "/model-profiles/{profile_id}",
        response_model=ModelProfileResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def update_model_profile(
        profile_id: UUID,
        request: ModelProfilePatchRequest,
    ) -> ModelProfileResponse:
        changes = request.model_dump(exclude_unset=True, exclude_none=True)
        if "provider" in changes and "base_url" not in changes:
            preset = next(
                (item for item in PROVIDER_PRESETS if item.id == changes["provider"]),
                None,
            )
            if preset is not None and preset.base_url:
                changes["base_url"] = preset.base_url
        return model_profile_response(profiles.update(profile_id, **changes))

    @app.delete(
        "/model-profiles/{profile_id}",
        status_code=204,
        response_class=Response,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def delete_model_profile(profile_id: UUID) -> Response:
        profiles.delete(profile_id)
        return Response(status_code=204)

    @app.post(
        "/model-profiles/{profile_id}/activate",
        response_model=ModelProfileResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def activate_model_profile(profile_id: UUID) -> ModelProfileResponse:
        return model_profile_response(profiles.activate(profile_id))

    @app.post(
        "/model-profiles/test",
        response_model=ModelProfileTestResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def test_model_profile(
        request: ModelProfileTestRequest,
    ) -> ModelProfileTestResponse:
        if request.profile_id is not None:
            profile = profiles.get(request.profile_id)
        else:
            profile = validate_profile(
                name=request.name or "Connection test",
                provider=request.provider,
                model=request.model,
                base_url=request.base_url,
                api_key=request.api_key,
            )
        model = model_for_profile(profile)
        request_id = f"profile-test-{uuid4().hex}"
        model_request = ModelRequest(
            model_name=profile.model,
            messages=(ChatMessage("user", "Reply with OK."),),
            tool_specs=(),
            model_settings=ModelSettings(
                connect_timeout=10,
                read_timeout=20,
                total_timeout=25,
                max_attempts=1,
                retry_budget=0,
                max_output_tokens=8,
            ),
            request_id=request_id,
            attempt_id=f"{request_id}:attempt:1",
        )
        try:
            response = await run_non_stream_model_response(model, model_request)
        except Exception as exc:
            return ModelProfileTestResponse(
                ok=False,
                message=_profile_error_message(exc),
            )
        return ModelProfileTestResponse(
            ok=bool(response.output_text or response.tool_calls),
            message="连接成功。" if response.output_text or response.tool_calls else "模型未返回内容。",
        )

    @app.get(
        "/health",
        response_model=HealthResponse, # 表示：这个接口成功时，返回的数据应该符合 这个 模型。
        responses={  # 除了成功响应之外，这个接口还可能返回哪些其他状态码，以及这些响应的数据格式是什么
            500: {"model": ErrorResponse},
            503: {"model": HealthResponse},
        },
    ) #  # 路由一：健康检查
    def health(response: Response) -> HealthResponse:
        status = database.health()
        if status.status != "ready":
            response.status_code = 503
        return health_response(status)



    #  创建会话
    @app.post(
        "/conversations",
        response_model=ConversationResponse,
        status_code=201,
        responses={  # 表示这个接口成功时，HTTP 状态码
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def create_conversation(
        request: ConversationCreateRequest | None = None,
    ) -> ConversationResponse:
        conversation = (
            conversations.create()
            if request is None
            else conversations.create(request.title)
        )
        return conversation_response(conversation)

    @app.get(
        "/conversations",
        response_model=ConversationPageResponse,
        responses={
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_conversations(
        query: Annotated[ConversationListQuery, Query()],
    ) -> ConversationPageResponse:
        page = conversations.list(
            page_size=query.page_size,
            before=conversation_cursor_from_query(query),
            status=query.status,
        )
        return conversation_page_response(page)

    @app.get(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_conversation(conversation_id: UUID) -> ConversationResponse:
        return conversation_response(conversations.get(conversation_id))

    @app.patch(
        "/conversations/{conversation_id}",
        response_model=ConversationResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def update_conversation(
        conversation_id: UUID,
        request: ConversationPatchRequest,
    ) -> ConversationResponse:
        patch = conversation_patch_from_request(request)
        return conversation_response(conversations.update(conversation_id, patch))

    @app.delete(
        "/conversations/{conversation_id}",
        status_code=204,
        response_class=Response,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def delete_conversation(conversation_id: UUID) -> Response:
        conversations.delete(conversation_id)
        return Response(status_code=204)


    #
    @app.post(
        "/conversations/{conversation_id}/messages",
        response_model=ChatReplyResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def send_message(
        conversation_id: UUID,
        message: MessageCreate,
    ) -> ChatReplyResponse:
        reply = await chat_service.send_message(conversation_id, message.content)
        return chat_reply_response(reply)

    @app.post(
        "/conversations/{conversation_id}/messages/stream",
        response_class=StreamingResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def stream_message(
        conversation_id: UUID,
        message: MessageCreate,
        request: Request,
    ) -> StreamingResponse:
        
        # 异步生成器 async generator
        async def generate() -> AsyncIterator[str]:
            queue: asyncio.Queue[RunEvent] = asyncio.Queue()
            seen_attempts: set[tuple[int | None, str]] = set()
            run_trace: list[dict[str, object]] = [
                {"stage": "retrieval", "message": "正在检索记忆与知识"}
            ]
            yield _sse("start", {"conversation_id": str(conversation_id)})
            yield _sse("status", run_trace[0])
            task = asyncio.create_task(
                chat_service.send_message(
                    conversation_id,
                    message.content,
                    on_event=queue.put_nowait,
                )
            )
            try:
                # 开始监听 Agent 事件
                while not task.done() or not queue.empty():
                    if await request.is_disconnected():  # 检查客户端是不是断开
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=0.15)  # 从事件队列拿一个事件
                    except asyncio.TimeoutError:
                        continue
                    if isinstance(event, ModelTextDeltaEvent):
                        attempt_key = (event.step_number, event.attempt_id)
                        reset = attempt_key not in seen_attempts
                        seen_attempts.add(attempt_key)

                        # 把 token 发给前端
                        yield _sse(
                            "delta",
                            {
                                "attempt_id": event.attempt_id,
                                "step_number": event.step_number,
                                "text": event.delta,
                                "reset": reset,
                            },
                        )
                        continue

                    # 把不是文本的事件转为前段可以看到的文字
                    status = _run_status(event)
                    if status is not None:
                        run_trace.append(status)
                        yield _sse("status", status)
                reply = await task
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            except Exception as exc:
                error_response = await _http_error(request, exc)
                payload = json.loads(error_response.body)
                yield _sse("error", payload)
            else:
                run_trace.append({"stage": "done", "message": "回复已保存"})
                try:
                    await asyncio.to_thread(
                        messages.set_run_trace,
                        reply.message_id,
                        run_trace,
                    )
                except Exception:
                    _LOGGER.warning("Could not persist assistant run trace")
                    run_trace.append(
                        {"stage": "error", "message": "运行详情未能持久化"}
                    )
                yield _sse(
                    "done",
                    chat_reply_response(reply, run_trace).model_dump(mode="json"),
                )
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )


    @app.get(
        "/conversations/{conversation_id}/planning",
        response_model=PlanningSnapshotResponse | None,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_latest_planning(
        conversation_id: UUID,
    ) -> PlanningSnapshotResponse | None:
        conversations.get(conversation_id)
        snapshot = planning_service.get_latest_snapshot(conversation_id)
        return None if snapshot is None else planning_snapshot_response(snapshot)

    @app.post(
        "/conversations/{conversation_id}/planning/draft/stream",
        response_class=StreamingResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def stream_planning_draft(
        conversation_id: UUID,
        payload: PlanningDraftRequest,
        request: Request,
    ) -> StreamingResponse:
        require_planning_draft(conversation_id, payload.expected_row_revision)
        return planning_stream(
            "draft",
            request,
            lambda on_event: planning_service.draft(
                conversation_id,
                payload.content,
                payload.expected_row_revision,
                on_event,
            ),
            conversation_id=conversation_id,
        )

    @app.post(
        "/planning-runs/{run_id}/approve/stream",
        response_class=StreamingResponse,
        responses=_PLANNING_ERROR_RESPONSES,
    )
    def stream_planning_approval(
        run_id: UUID,
        payload: PlanningControlRequest,
        request: Request,
    ) -> StreamingResponse:
        require_planning_action(
            run_id,
            payload.expected_row_revision,
            "approve",
        )
        return planning_stream(
            "approve",
            request,
            lambda on_event: planning_service.approve(
                run_id,
                payload.expected_row_revision,
                on_event,
            ),
            run_id=run_id,
        )

    @app.post(
        "/planning-runs/{run_id}/tool-approval/stream",
        response_class=StreamingResponse,
        responses=_PLANNING_ERROR_RESPONSES,
    )
    def stream_planning_tool_approval(
        run_id: UUID,
        payload: PlanningToolApprovalRequest,
        request: Request,
    ) -> StreamingResponse:
        require_planning_action(
            run_id,
            payload.expected_row_revision,
            "decide_tool_approval",
        )
        return planning_stream(
            "tool_approval",
            request,
            lambda on_event: planning_service.decide_tool_approval(
                run_id,
                payload.expected_row_revision,
                payload.tool_name,
                payload.call_id,
                payload.decision,
                payload.rejection_message,
                on_event,
            ),
            run_id=run_id,
        )

    @app.post(
        "/planning-runs/{run_id}/retry/stream",
        response_class=StreamingResponse,
        responses=_PLANNING_ERROR_RESPONSES,
    )
    def stream_planning_retry(
        run_id: UUID,
        payload: PlanningControlRequest,
        request: Request,
    ) -> StreamingResponse:
        require_planning_action(
            run_id,
            payload.expected_row_revision,
            "retry_current",
        )
        return planning_stream(
            "retry",
            request,
            lambda on_event: planning_service.retry_current(
                run_id,
                payload.expected_row_revision,
                on_event,
            ),
            run_id=run_id,
        )

    @app.post(
        "/planning-runs/{run_id}/revise/stream",
        response_class=StreamingResponse,
        responses=_PLANNING_ERROR_RESPONSES,
    )
    def stream_planning_revision(
        run_id: UUID,
        payload: PlanningReviseRequest,
        request: Request,
    ) -> StreamingResponse:
        require_planning_action(
            run_id,
            payload.expected_row_revision,
            "revise",
        )
        return planning_stream(
            "revise",
            request,
            lambda on_event: planning_service.revise(
                run_id,
                payload.expected_row_revision,
                payload.feedback,
                on_event,
            ),
            run_id=run_id,
        )

    @app.post(
        "/planning-runs/{run_id}/cancel",
        response_model=PlanningSnapshotResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def cancel_planning(
        run_id: UUID,
        payload: PlanningCancelRequest,
    ) -> PlanningSnapshotResponse:
        snapshot = await planning_service.cancel(
            run_id,
            payload.expected_row_revision,
            payload.reason,
        )
        return planning_snapshot_response(snapshot)


    # 查询历史消息
    @app.get(
        "/conversations/{conversation_id}/messages",
        response_model=MessagePageResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_messages(
        conversation_id: UUID,
        query: Annotated[HistoryQuery, Query()],
    ) -> MessagePageResponse:
        page = messages.page(
            conversation_id,
            page_size=query.page_size,
            before_sequence=query.before_sequence,
        )
        return message_page_response(page)

    @app.get(
        "/memories",
        response_model=MemoryPageResponse,
        responses={
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_memories(
        query: Annotated[MemoryListQuery, Query()],
    ) -> MemoryPageResponse:
        page = memory_store.list(
            page_size=query.page_size,
            before=memory_cursor_from_query(query),
            status=query.status,
            scope=query.scope,
            source_conversation_id=query.source_conversation_id,
            source_message_id=query.source_message_id,
        )
        return memory_page_response(page)

    @app.post(
        "/memories",
        response_model=MemoryWriteResponse,
        status_code=201,
        responses={
            200: {"model": MemoryWriteResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def create_memory(
        request: MemoryCreateRequest,
        response: Response,
    ) -> MemoryWriteResponse:
        result = memory_store.create(memory_create_from_request(request))
        if not result.created:
            response.status_code = 200
        return memory_write_response(result)

    @app.get(
        "/memories/{memory_id}",
        response_model=MemoryResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_memory(memory_id: UUID) -> MemoryResponse:
        return memory_response(memory_store.get(memory_id))

    @app.patch(
        "/memories/{memory_id}",
        response_model=MemoryResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def update_memory(
        memory_id: UUID,
        request: MemoryPatchRequest,
    ) -> MemoryResponse:
        patch = memory_patch_from_request(request)
        return memory_response(memory_store.update(memory_id, patch))

    @app.post(
        "/memories/{memory_id}/disable",
        response_model=MemoryResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def disable_memory(memory_id: UUID) -> MemoryResponse:
        return memory_response(memory_store.disable(memory_id))

    @app.delete(
        "/memories/{memory_id}",
        status_code=204,
        response_class=Response,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def delete_memory(memory_id: UUID) -> Response:
        if not memory_store.delete(memory_id):
            raise MemoryNotFoundError(memory_id)
        return Response(status_code=204)

    @app.get(
        "/knowledge",
        response_model=KnowledgePageResponse,
        responses={
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_knowledge(
        query: Annotated[CursorQuery, Query()],
    ) -> KnowledgePageResponse:
        page = knowledge_store.list_sources(
            query.page_size,
            knowledge_cursor_from_query(query),
        )
        return knowledge_page_response(page)

    @app.post(
        "/knowledge",
        response_model=KnowledgeResponse,
        status_code=201,
        responses={
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def create_knowledge(request: KnowledgeCreateRequest) -> KnowledgeResponse:
        source = knowledge_store.create_source(
            knowledge_create_from_request(request)
        )
        return knowledge_response(source)

    @app.get(
        "/knowledge/{source_id}",
        response_model=KnowledgeResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_knowledge(source_id: UUID) -> KnowledgeResponse:
        return knowledge_response(knowledge_store.get_source(source_id))

    @app.delete(
        "/knowledge/{source_id}",
        status_code=204,
        response_class=Response,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def delete_knowledge(source_id: UUID) -> Response:
        knowledge_store.delete_source(source_id)
        return Response(status_code=204)

    @app.post(
        "/knowledge/{source_id}/reindex",
        response_model=IndexReportResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def reindex_knowledge(source_id: UUID) -> IndexReportResponse:
        if knowledge_indexer is None:
            raise KnowledgeIndexUnavailableError
        return index_report_response(knowledge_indexer.reindex(source_id))

    return app


def main() -> None:
    settings = AppSettings.from_env()
    agent_config = ChatRuntimeConfig()
    profile_store = ModelProfileStore()
    embedding_client = (
        OpenAIEmbeddingClient(
            OpenAI(max_retries=0),
            settings.embedding_model,
            settings.embedding_dimensions,
        )
        if settings.memory_retrieval_enabled
        else None
    )

    def chat_agent() -> Agent:
        try:
            profile = profile_store.active()
        except ModelProfileStoreError:
            profile = None
        model = model_for_profile(profile) if profile is not None else None
        return build_chat_agent(agent_config, model)

    def memory_agent() -> Agent:
        try:
            profile = profile_store.active()
        except ModelProfileStoreError:
            profile = None
        model = (
            model_for_profile(profile)
            if profile is not None
            else OpenAIResponsesModel(
                model=agent_config.model,
                client=agent_config.model_client,
            )
        )
        return create_memory_extraction_agent(model)

    app = create_app(
        settings,
        chat_agent,
        memory_agent_factory=memory_agent,
        embedding_client=embedding_client,
        model_profile_store=profile_store,
    )
    uvicorn.run(app, host=settings.http_host, port=settings.http_port)
