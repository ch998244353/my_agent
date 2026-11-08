from __future__ import annotations

import math
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.errors import UniqueViolation

from .database import Database
from .embedding import (
    EmbeddingBatch,
    EmbeddingClient,
    EmbeddingContractError,
    embedding_vector_literal,
)


MemoryKind = Literal["fact", "preference", "instruction"]
MemoryScope = Literal["global", "conversation"]
MemoryStatus = Literal["active", "disabled"]

_MEMORY_COLUMNS = (
    "id, kind, scope, status, content, tags, confidence, fingerprint, "
    "source_conversation_id, source_user_message_id, "
    "source_assistant_message_id, created_at, updated_at, supersedes_id, "
    "embedding_model, embedded_at"
)


# 把一条记忆的三个来源 UUID 放在一起
@dataclass(frozen=True)
class MemorySource:
    conversation_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID


# 保存数据库已经确认的一整条长期记忆
@dataclass(frozen=True)
class MemoryRecord:
    id: UUID
    kind: MemoryKind
    scope: MemoryScope
    status: MemoryStatus
    content: str
    tags: tuple[str, ...]
    confidence: float
    fingerprint: str
    source: MemorySource
    created_at: datetime
    updated_at: datetime
    supersedes_id: UUID | None = None
    embedding_model: str | None = None
    embedded_at: datetime | None = None



# 发出 Embedding 请求前保存的最小快照
@dataclass(frozen=True)
class MemoryEmbeddingTarget:
    id: UUID
    content: str
    fingerprint: str



@dataclass(frozen=True)
class MemorySearchHit:
    memory: MemoryRecord
    distance: float | None = None


# 保存下一页从哪一条记忆之前继续读取
@dataclass(frozen=True)
class MemoryCursor:
    created_at: datetime
    memory_id: UUID


# 保存一页记忆和下一页的位置
@dataclass(frozen=True)
class MemoryPage:
    items: tuple[MemoryRecord, ...]
    next_cursor: MemoryCursor | None



# 定义创建记忆所需的数据
@dataclass(frozen=True)
class MemoryCreate:
    kind: MemoryKind
    scope: MemoryScope  # 记忆级别 : 全局 / 会话
    content: str
    source: MemorySource
    tags: tuple[str, ...] = ()
    confidence: float = 1.0


# 只列出人工修改时允许改变的字段；None 表示本次不改
@dataclass(frozen=True)
class MemoryPatch:
    content: str | None = None
    kind: MemoryKind | None = None
    tags: tuple[str, ...] | None = None
    confidence: float | None = None


# 数据库记录和本次是否真的插入
@dataclass(frozen=True)
class MemorySaveResult:
    record: MemoryRecord
    created: bool


class MemoryNotFoundError(LookupError):
    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id
        super().__init__(f"memory not found: {memory_id}")


class MemoryDuplicateError(ValueError):
    def __init__(self, existing_id: UUID) -> None:
        self.existing_id = existing_id
        super().__init__(f"memory already exists: {existing_id}")


class MemoryValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"invalid memory: {reason}")


class MemorySupersedeError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"memory supersede rejected: {reason}")


class MemoryEmbeddingConflictError(RuntimeError):
    def __init__(self, memory_id: UUID) -> None:
        self.memory_id = memory_id
        super().__init__(f"memory changed while embedding: {memory_id}")


def _normalize_content(content: object) -> str:
    if not isinstance(content, str):
        raise MemoryValidationError("content_type")
    normalized = " ".join(unicodedata.normalize("NFC", content).split())
    if not 1 <= len(normalized) <= 2000:
        raise MemoryValidationError("content_length")
    return normalized


# 把标签整理成固定顺序的元组
def _normalize_tags(tags: object) -> tuple[str, ...]:
    if isinstance(tags, str | bytes) or not isinstance(tags, Iterable):
        raise MemoryValidationError("tags_type")

    normalized: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            raise MemoryValidationError("tag_type")
        value = unicodedata.normalize("NFC", tag).strip().casefold()
        if not 1 <= len(value) <= 40:
            raise MemoryValidationError("tag_length")
        normalized.add(value)
        if len(normalized) > 16:
            raise MemoryValidationError("tags_count")
    return tuple(sorted(normalized))


def _normalize_confidence(confidence: object) -> float:
    if isinstance(confidence, bool) or not isinstance(confidence, int | float):
        raise MemoryValidationError("confidence_type")
    try:
        value = float(confidence)
    except OverflowError:
        raise MemoryValidationError("confidence_range") from None
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise MemoryValidationError("confidence_range")
    return value


def _normalize_embedding_model(model: object) -> str:
    if not isinstance(model, str) or not model.strip():
        raise MemoryValidationError("embedding_model")
    return model.strip()


def _prepare_embedding(
    embedding: object | None,
    embedding_model: object | None,
) -> tuple[str | None, str | None]:
    if (embedding is None) != (embedding_model is None):
        raise MemoryValidationError("embedding_pair")
    if embedding is None:
        return None, None

    model = _normalize_embedding_model(embedding_model)
    try:
        return embedding_vector_literal(embedding), model
    except EmbeddingContractError as exc:
        raise MemoryValidationError("embedding") from exc


def _require_conversation_id(conversation_id: object) -> UUID:
    if not isinstance(conversation_id, UUID):
        raise MemoryValidationError("conversation_id")
    return conversation_id


def _require_search_limit(limit: object) -> int:
    if type(limit) is not int or limit < 1:
        raise MemoryValidationError("search_limit")
    return limit


def _keyword_pattern(query: object) -> str:
    if not isinstance(query, str) or not query.strip():
        raise MemoryValidationError("search_query")
    escaped = query.strip().replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _vector_search_hit(row: tuple[object, ...]) -> MemorySearchHit:
    distance = float(row[-1])
    if not math.isfinite(distance):
        raise RuntimeError("memory search returned an invalid distance")
    memory = _memory_from_row(row[:-1])  # type: ignore[arg-type]
    return MemorySearchHit(memory, distance)


# 在打开数据库事务之前，校验用户传入的记忆数据，并把内容、标签、可信度转换为数据库可以直接使用的标准格式
def _validate_memory_create(
    data: MemoryCreate,
) -> tuple[
    MemoryKind,
    MemoryScope,
    str,
    tuple[str, ...],
    float,
]:
    if not isinstance(data, MemoryCreate):
        raise MemoryValidationError("data_type")
    if data.kind not in ("fact", "preference", "instruction"):
        raise MemoryValidationError("kind")
    if data.scope not in ("global", "conversation"):
        raise MemoryValidationError("scope")
    if not isinstance(data.source, MemorySource) or not all(
        isinstance(value, UUID)
        for value in (
            data.source.conversation_id,
            data.source.user_message_id,
            data.source.assistant_message_id,
        )
    ):
        raise MemoryValidationError("source_type")
    if data.source.user_message_id == data.source.assistant_message_id:
        raise MemoryValidationError("source_distinct")
    return (
        data.kind,
        data.scope,
        _normalize_content(data.content),
        _normalize_tags(data.tags),
        _normalize_confidence(data.confidence),
    )


# 根据“记忆范围 + 标准化后的正文” hash后生成一个固定的 SHA-256 指纹
def memory_fingerprint(scope: MemoryScope, normalized_content: str) -> str:
    value = f"{scope}\n{normalized_content}".encode("utf-8")
    return sha256(value).hexdigest()


# 查询数据库，确认记忆引用的用户消息和助手消息确实存在，并且属于同一会话、角色正确、顺序正确
def _validate_source(connection: Connection, source: MemorySource) -> None:
    rows = connection.execute(
        "SELECT id, role, sequence FROM conversation_messages "
        "WHERE conversation_id = %s AND id IN (%s, %s)",
        (
            source.conversation_id,
            source.user_message_id,
            source.assistant_message_id,
        ),
    ).fetchall()
    messages = {row[0]: (row[1], row[2]) for row in rows}
    user = messages.get(source.user_message_id)
    assistant = messages.get(source.assistant_message_id)
    if user is None:
        raise MemoryValidationError("source_user_missing")
    if assistant is None:
        raise MemoryValidationError("source_assistant_missing")
    if user[0] != "user":
        raise MemoryValidationError("source_user_role")
    if assistant[0] != "assistant":
        raise MemoryValidationError("source_assistant_role")
    if user[1] >= assistant[1]:
        raise MemoryValidationError("source_order")


# 将记忆系统的版本号加一，表示数据库中的记忆已经发生变化
def _bump_epoch(connection: Connection) -> None:
    row = connection.execute(
        "UPDATE memory_epochs SET value = value + 1 "
        "WHERE name = %s RETURNING value",
        ("memory",),
    ).fetchone()
    if row is None:
        raise RuntimeError("memory epoch is missing")



# 把 PostgreSQL 返回的一行值转为 MemoryRecord
def _memory_from_row(
    row: tuple[
        UUID,
        MemoryKind,
        MemoryScope,
        MemoryStatus,
        str,
        list[str],
        float,
        str,
        UUID,
        UUID,
        UUID,
        datetime,
        datetime,
        UUID | None,
        str | None,
        datetime | None,
    ],
) -> MemoryRecord:
    return MemoryRecord(
        id=row[0],
        kind=row[1],
        scope=row[2],
        status=row[3],
        content=row[4],
        tags=tuple(row[5]),
        confidence=row[6],
        fingerprint=row[7],
        source=MemorySource(row[8], row[9], row[10]),
        created_at=row[11],
        updated_at=row[12],
        supersedes_id=row[13],
        embedding_model=row[14],
        embedded_at=row[15],
    )


def _require_memory_id(memory_id: object) -> UUID:
    if not isinstance(memory_id, UUID):
        raise ValueError("memory_id must be a UUID")
    return memory_id


# 读取并锁住要修改的记忆
def _get_memory_for_update(
    connection: Connection,  # connection释放才会解锁
    memory_id: UUID,
) -> MemoryRecord:
    row = connection.execute(
        f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = %s FOR UPDATE",
        (memory_id,),
    ).fetchone()
    if row is None:
        raise MemoryNotFoundError(memory_id)
    return _memory_from_row(row)


# 抽出有效记忆查询
def _find_active_memory(
    connection: Connection,
    scope: MemoryScope,
    source_conversation_id: UUID,
    fingerprint: str,
) -> MemoryRecord | None:
    if scope == "global":
        row = connection.execute(
            f"SELECT {_MEMORY_COLUMNS} FROM memories "
            "WHERE scope = 'global' AND status = 'active' "
            "AND fingerprint = %s",
            (fingerprint,),
        ).fetchone()
    else:
        row = connection.execute(
            f"SELECT {_MEMORY_COLUMNS} FROM memories "
            "WHERE scope = 'conversation' AND status = 'active' "
            "AND source_conversation_id = %s AND fingerprint = %s",
            (source_conversation_id, fingerprint),
        ).fetchone()
    return None if row is None else _memory_from_row(row)


# 保存项目现有的 Database，每次查询仍通过连接池借连接。它不打开或关闭连接池，也不缓存第二份记忆数据
class MemoryStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, memory_id: UUID) -> MemoryRecord:
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories WHERE id = %s",
                (memory_id,),
            ).fetchone()
        if row is None:
            raise MemoryNotFoundError(memory_id)
        return _memory_from_row(row)

    def get_epoch(self) -> int:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT value FROM memory_epochs WHERE name = %s",
                ("memory",),
            ).fetchone()
        if row is None:
            raise RuntimeError("memory epoch is missing")
        return row[0]

    def find_active_exact(self, data: MemoryCreate) -> MemoryRecord | None:
        _, scope, content, _, _ = _validate_memory_create(data)
        fingerprint = memory_fingerprint(scope, content)
        with self._database.connection() as connection:
            return _find_active_memory(
                connection,
                scope,
                data.source.conversation_id,
                fingerprint,
            )


    # 从正文和 tags 中查找当前 conversation 可以看到的 active 记忆
    def search_keyword(
        self,
        query: str,
        conversation_id: UUID,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        pattern = _keyword_pattern(query)
        conversation_id = _require_conversation_id(conversation_id)
        limit = _require_search_limit(limit)
        with self._database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_MEMORY_COLUMNS} FROM memories "
                "WHERE status = 'active' "
                "AND (scope = 'global' OR "
                "(scope = 'conversation' AND source_conversation_id = %s)) "
                "AND (content ILIKE %s ESCAPE E'\\\\' OR "
                "array_to_string(tags, ' ') ILIKE %s ESCAPE E'\\\\') "
                "ORDER BY (content ILIKE %s ESCAPE E'\\\\') DESC, "
                "created_at DESC, id DESC LIMIT %s",
                (conversation_id, pattern, pattern, pattern, limit),
            ).fetchall()
        return tuple(MemorySearchHit(_memory_from_row(row)) for row in rows)


    # 普通向量召回
    def search_vector(
        self,
        vector: Sequence[float],
        embedding_model: str,
        conversation_id: UUID,
        limit: int,
    ) -> tuple[MemorySearchHit, ...]:
        vector_literal, embedding_model = _prepare_embedding(
            vector,
            embedding_model,
        )
        assert vector_literal is not None and embedding_model is not None
        conversation_id = _require_conversation_id(conversation_id)
        limit = _require_search_limit(limit)
        with self._database.connection() as connection:
            rows = connection.execute(
                f"SELECT {_MEMORY_COLUMNS}, "
                "embedding <=> %s::vector AS distance FROM memories "
                "WHERE status = 'active' AND embedding IS NOT NULL "
                "AND embedding_model = %s "
                "AND (scope = 'global' OR "
                "(scope = 'conversation' AND source_conversation_id = %s)) "
                "ORDER BY distance, id LIMIT %s",
                (vector_literal, embedding_model, conversation_id, limit),
            ).fetchall()
        return tuple(_vector_search_hit(row) for row in rows)


    # 只查与候选记忆具有相同 kind、scope 和 model 的最近一条记录
    def find_nearest_active(
        self,
        vector: Sequence[float],
        embedding_model: str,
        kind: MemoryKind,
        scope: MemoryScope,
        conversation_id: UUID | None,
    ) -> MemorySearchHit | None:
        vector_literal, embedding_model = _prepare_embedding(
            vector,
            embedding_model,
        )
        assert vector_literal is not None and embedding_model is not None
        if kind not in ("fact", "preference", "instruction"):
            raise MemoryValidationError("kind")
        if scope not in ("global", "conversation"):
            raise MemoryValidationError("scope")

        parameters: list[object] = [vector_literal, kind, scope, embedding_model]
        conversation_filter = ""
        if scope == "conversation":
            conversation_id = _require_conversation_id(conversation_id)
            conversation_filter = "AND source_conversation_id = %s "
            parameters.append(conversation_id)

        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_MEMORY_COLUMNS}, "
                "embedding <=> %s::vector AS distance FROM memories "
                "WHERE status = 'active' AND kind = %s AND scope = %s "
                "AND embedding IS NOT NULL AND embedding_model = %s "
                f"{conversation_filter}"
                "ORDER BY distance, id LIMIT 1",
                tuple(parameters),
            ).fetchone()
        return None if row is None else _vector_search_hit(row)


    # 查询真正需要回填的记忆
    def list_embedding_targets(
        self,
        model: str,
        limit: int,
    ) -> tuple[MemoryEmbeddingTarget, ...]:
        model = _normalize_embedding_model(model)
        if type(limit) is not int or limit < 1:
            raise MemoryValidationError("embedding_limit")

        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT id, content, fingerprint FROM memories "
                "WHERE status = 'active' "
                "AND (embedding IS NULL OR embedding_model <> %s) "
                "ORDER BY created_at, id LIMIT %s",
                (model, limit),
            ).fetchall()
        return tuple(MemoryEmbeddingTarget(*row) for row in rows)


    # 整批写入embedding
    def save_embeddings(
        self,
        targets: Sequence[MemoryEmbeddingTarget],
        vectors: Sequence[Sequence[float]],
        model: str,
    ) -> int:
        model = _normalize_embedding_model(model)
        if isinstance(targets, (str, bytes)) or not isinstance(targets, Sequence):
            raise MemoryValidationError("embedding_targets")
        if isinstance(vectors, (str, bytes)) or not isinstance(vectors, Sequence):
            raise MemoryValidationError("embedding_vectors")

        target_batch = tuple(targets)
        vector_batch = tuple(vectors)
        if len(target_batch) != len(vector_batch):
            raise MemoryValidationError("embedding_count")

        prepared: list[tuple[MemoryEmbeddingTarget, str]] = []
        seen_ids: set[UUID] = set()
        for target, vector in zip(target_batch, vector_batch):
            if (
                not isinstance(target, MemoryEmbeddingTarget)
                or not isinstance(target.id, UUID)
                or not isinstance(target.content, str)
                or not target.content
                or not isinstance(target.fingerprint, str)
                or len(target.fingerprint) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in target.fingerprint
                )
                or target.id in seen_ids
            ):
                raise MemoryValidationError("embedding_target")
            seen_ids.add(target.id)
            try:
                literal = embedding_vector_literal(vector)
            except EmbeddingContractError as exc:
                raise MemoryValidationError("embedding") from exc
            prepared.append((target, literal))

        if not prepared:
            return 0

        with self._database.transaction() as connection:
            for target, _ in prepared:
                row = connection.execute(
                    "SELECT status, fingerprint FROM memories "
                    "WHERE id = %s FOR UPDATE",
                    (target.id,),
                ).fetchone()
                if row is None or row != ("active", target.fingerprint):
                    raise MemoryEmbeddingConflictError(target.id)

            for target, literal in prepared:
                connection.execute(
                    "UPDATE memories SET embedding = %s::vector, "
                    "embedding_model = %s, embedded_at = now() WHERE id = %s",
                    (literal, model, target.id),
                )
            _bump_epoch(connection)
        return len(prepared)



    # 从 memories 表中按条件查询记忆，并使用“游标分页”返回一页结果
    def list(
        self,
        *,
        page_size: int = 50,  # 本页最多返回多少条，默认 50
        before: MemoryCursor | None = None,  # 上一页返回的游标
        status: MemoryStatus | None = None,  # active 或 disabled
        scope: MemoryScope | None = None,  # global 或 conversation
        source_conversation_id: UUID | None = None,  # 哪个会话
        source_message_id: UUID | None = None,
    ) -> MemoryPage:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size must be an integer between 1 and 100")
        if before is not None and (
            not isinstance(before, MemoryCursor)
            or not isinstance(before.created_at, datetime)
            or not isinstance(before.memory_id, UUID)
        ):
            raise ValueError("before must be a valid MemoryCursor")
        if status is not None and status not in ("active", "disabled"):
            raise ValueError("invalid memory status")
        if scope is not None and scope not in ("global", "conversation"):
            raise ValueError("invalid memory scope")
        if source_conversation_id is not None and not isinstance(
            source_conversation_id,
            UUID,
        ):
            raise ValueError("source_conversation_id must be a UUID")
        if source_message_id is not None and not isinstance(source_message_id, UUID):
            raise ValueError("source_message_id must be a UUID")

        conditions: list[str] = []
        parameters: list[object] = []
        if before is not None:
            conditions.append("(created_at, id) < (%s, %s)")
            parameters.extend((before.created_at, before.memory_id))
        if status is not None:
            conditions.append("status = %s")
            parameters.append(status)
        if scope is not None:
            conditions.append("scope = %s")
            parameters.append(scope)
        if source_conversation_id is not None:
            conditions.append("source_conversation_id = %s")
            parameters.append(source_conversation_id)
        if source_message_id is not None:
            conditions.append(
                "%s IN (source_user_message_id, source_assistant_message_id)"
            )
            parameters.append(source_message_id)

        statement = f"SELECT {_MEMORY_COLUMNS} FROM memories"
        if conditions:
            statement += " WHERE " + " AND ".join(conditions)
        statement += " ORDER BY created_at DESC, id DESC LIMIT %s"
        parameters.append(page_size + 1)
        with self._database.connection() as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()

        has_more = len(rows) > page_size
        items = tuple(_memory_from_row(row) for row in rows[:page_size])
        next_cursor = (
            MemoryCursor(items[-1].created_at, items[-1].id) if has_more else None
        )
        return MemoryPage(items, next_cursor)

    # 将记忆存入数据库
    def create(
        self,
        data: MemoryCreate,
        *,
        embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> MemorySaveResult:
        kind, scope, content, tags, confidence = _validate_memory_create(data)
        embedding_literal, embedding_model = _prepare_embedding(
            embedding,
            embedding_model,
        )
        fingerprint = memory_fingerprint(scope, content)
        memory_id = uuid4()

        with self._database.transaction() as connection:
            _validate_source(connection, data.source)
            row = connection.execute(
                "INSERT INTO memories "
                "(id, kind, scope, content, tags, confidence, fingerprint, "
                "source_conversation_id, source_user_message_id, "
                "source_assistant_message_id, embedding, embedding_model, "
                "embedded_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s::vector, %s, "
                "CASE WHEN %s::text IS NULL THEN NULL ELSE now() END) "
                f"ON CONFLICT DO NOTHING RETURNING {_MEMORY_COLUMNS}",
                (
                    memory_id,
                    kind,
                    scope,
                    content,
                    list(tags),
                    confidence,
                    fingerprint,
                    data.source.conversation_id,
                    data.source.user_message_id,
                    data.source.assistant_message_id,
                    embedding_literal,
                    embedding_model,
                    embedding_model,
                ),
            ).fetchone()
            if row is not None:
                _bump_epoch(connection)
                return MemorySaveResult(_memory_from_row(row), created=True)

            existing = _find_active_memory(
                connection,
                scope,
                data.source.conversation_id,
                fingerprint,
            )
            if existing is None:
                raise RuntimeError("memory conflict could not be resolved")
            return MemorySaveResult(existing, created=False)


    # 用一条新事实显式替代仍然有效的旧事实
    def supersede(
        self,
        old_id: UUID,
        data: MemoryCreate,
        *,
        embedding: Sequence[float] | None = None,
        embedding_model: str | None = None,
    ) -> MemoryRecord:
        kind, scope, content, tags, confidence = _validate_memory_create(data)
        embedding_literal, embedding_model = _prepare_embedding(
            embedding,
            embedding_model,
        )
        old_id = _require_memory_id(old_id)
        fingerprint = memory_fingerprint(scope, content)
        memory_id = uuid4()

        try:
            with self._database.transaction() as connection:
                try:
                    current = _get_memory_for_update(connection, old_id)
                except MemoryNotFoundError:
                    raise MemorySupersedeError("old_not_found") from None
                if current.status != "active":
                    raise MemorySupersedeError("old_disabled")
                if current.scope != scope:
                    raise MemorySupersedeError("scope_mismatch")
                if (
                    scope == "conversation"
                    and current.source.conversation_id
                    != data.source.conversation_id
                ):
                    raise MemorySupersedeError("conversation_mismatch")

                _validate_source(connection, data.source)
                # 先插入新事实，再停用旧事实
                row = connection.execute(
                    "INSERT INTO memories "
                    "(id, kind, scope, content, tags, confidence, fingerprint, "
                    "source_conversation_id, source_user_message_id, "
                    "source_assistant_message_id, supersedes_id, embedding, "
                    "embedding_model, embedded_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s::vector, %s, "
                    "CASE WHEN %s::text IS NULL THEN NULL ELSE now() END) "
                    f"RETURNING {_MEMORY_COLUMNS}",
                    (
                        memory_id,
                        kind,
                        scope,
                        content,
                        list(tags),
                        confidence,
                        fingerprint,
                        data.source.conversation_id,
                        data.source.user_message_id,
                        data.source.assistant_message_id,
                        old_id,
                        embedding_literal,
                        embedding_model,
                        embedding_model,
                    ),
                ).fetchone()
                assert row is not None
                connection.execute(
                    "UPDATE memories SET status = 'disabled', updated_at = now() "
                    "WHERE id = %s",
                    (old_id,),
                )
                _bump_epoch(connection)
                return _memory_from_row(row)
        except UniqueViolation as exc:
            if exc.diag.constraint_name not in {
                "memories_active_global_fingerprint_key",
                "memories_active_conversation_fingerprint_key",
            }:
                raise
            raise MemorySupersedeError("duplicate_target") from None


    # 实现记忆更新
    def update(self, memory_id: UUID, patch: MemoryPatch) -> MemoryRecord:
        memory_id = _require_memory_id(memory_id)
        if not isinstance(patch, MemoryPatch):
            raise MemoryValidationError("patch_type")

        duplicate_key: tuple[MemoryScope, UUID, str] | None = None
        try:
            with self._database.transaction() as connection:
                current = _get_memory_for_update(connection, memory_id)
                kind, _, content, tags, confidence = _validate_memory_create(
                    MemoryCreate(
                        kind=current.kind if patch.kind is None else patch.kind,
                        scope=current.scope,
                        content=(
                            current.content
                            if patch.content is None
                            else patch.content
                        ),
                        source=current.source,
                        tags=current.tags if patch.tags is None else patch.tags,
                        confidence=(
                            current.confidence
                            if patch.confidence is None
                            else patch.confidence
                        ),
                    )
                )
                fingerprint = memory_fingerprint(current.scope, content)
                if (
                    kind == current.kind
                    and content == current.content
                    and tags == current.tags
                    and confidence == current.confidence
                ):
                    return current

                duplicate_key = (
                    current.scope,
                    current.source.conversation_id,
                    fingerprint,
                )
                embedding_update = (
                    ", embedding = NULL, embedding_model = NULL, "
                    "embedded_at = NULL"
                    if content != current.content
                    else ""
                )
                row = connection.execute(
                    "UPDATE memories SET kind = %s, content = %s, tags = %s, "
                    "confidence = %s, fingerprint = %s, updated_at = now()"
                    f"{embedding_update} "
                    f"WHERE id = %s RETURNING {_MEMORY_COLUMNS}",
                    (
                        kind,
                        content,
                        list(tags),
                        confidence,
                        fingerprint,
                        memory_id,
                    ),
                ).fetchone()
                assert row is not None
                _bump_epoch(connection)
                return _memory_from_row(row)
        except UniqueViolation:
            if duplicate_key is None:
                raise
            scope, source_conversation_id, fingerprint = duplicate_key
            with self._database.connection() as connection:
                existing = _find_active_memory(
                    connection,
                    scope,
                    source_conversation_id,
                    fingerprint,
                )
            if existing is None:
                raise
            raise MemoryDuplicateError(existing.id) from None


    # 停用记忆
    def disable(self, memory_id: UUID) -> MemoryRecord:
        memory_id = _require_memory_id(memory_id)
        with self._database.transaction() as connection:
            current = _get_memory_for_update(connection, memory_id)
            if current.status == "disabled":
                return current
            row = connection.execute(
                "UPDATE memories SET status = 'disabled', updated_at = now() "
                f"WHERE id = %s RETURNING {_MEMORY_COLUMNS}",
                (memory_id,),
            ).fetchone()
            assert row is not None
            _bump_epoch(connection)
            return _memory_from_row(row)


    # 删除记忆
    def delete(self, memory_id: UUID) -> bool:
        memory_id = _require_memory_id(memory_id)
        with self._database.transaction() as connection:
            row = connection.execute(
                "DELETE FROM memories WHERE id = %s RETURNING id",
                (memory_id,),
            ).fetchone()
            if row is None:
                return False
            _bump_epoch(connection)
            return True


def embed_missing_memories(
    store: MemoryStore,
    client: EmbeddingClient,
    model: str,
    batch_size: int = 64,
) -> int:
    if type(batch_size) is not int or batch_size < 1:
        raise MemoryValidationError("embedding_batch_size")

    saved = 0
    while targets := store.list_embedding_targets(model, batch_size):
        vectors: EmbeddingBatch = client.embed(
            tuple(target.content for target in targets)
        )
        saved += store.save_embeddings(targets, vectors, model)
    return saved
