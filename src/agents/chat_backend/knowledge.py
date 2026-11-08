from __future__ import annotations

import json
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from math import isfinite
from typing import Literal
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from .database import Database
from .embedding import (
    Embedding,
    EmbeddingBatch,
    EmbeddingClient,
    EmbeddingContractError,
    embedding_vector_literal,
)


KnowledgeStatus = Literal["pending", "indexed"]

_SOURCE_COLUMNS = (
    "id, title, text, metadata, status, embedding_model, created_at, "
    "updated_at, indexed_at"
)
_SEARCH_HIT_COLUMNS = (
    "s.id, c.id, s.title, c.content, c.ordinal, c.start_char, c.end_char"
)


# 人工创建知识来源时提交的原文；切块和向量不由调用者填写
@dataclass(frozen=True)
class KnowledgeSourceCreate:
    title: str
    text: str
    metadata: Mapping[str, object] = field(default_factory=dict)


# 保存数据库已经确认的一份知识原文及其索引状态
@dataclass(frozen=True)
class KnowledgeSource:
    id: UUID
    title: str
    text: str
    metadata: dict[str, object]
    status: KnowledgeStatus
    embedding_model: str | None
    created_at: datetime
    updated_at: datetime
    indexed_at: datetime | None


# 下一页从这条记录之前继续读取
@dataclass(frozen=True)
class KnowledgeCursor:
    created_at: datetime
    id: UUID


@dataclass(frozen=True)
class KnowledgePage:
    items: tuple[KnowledgeSource, ...]
    next_cursor: KnowledgeCursor | None


# Lesson07 的切块函数将生成这些尚未写入数据库的数据
@dataclass(frozen=True)
class KnowledgeChunkDraft:
    ordinal: int
    start_char: int
    end_char: int
    content: str


# 查询知识分块时返回的公开字段；向量只留在 PostgreSQL 中参与排序
@dataclass(frozen=True)
class KnowledgeChunk:
    id: UUID
    source_id: UUID
    ordinal: int
    start_char: int
    end_char: int
    content: str
    embedding_model: str
    created_at: datetime


# 一次知识索引真正提交后返回的结果
@dataclass(frozen=True)
class IndexReport:
    source_id: UUID
    chunk_count: int
    embedding_model: str
    indexed_at: datetime


# 关键词或向量查询返回的一条知识分块，不把数据库向量带回 Python
@dataclass(frozen=True)
class KnowledgeSearchHit:
    source_id: UUID
    chunk_id: UUID
    source_title: str
    content: str
    ordinal: int
    start_char: int
    end_char: int
    distance: float | None = None


# 所有批次成功后才生成，Lesson08 再把它原子发布到数据库
@dataclass(frozen=True)
class _PreparedKnowledgeIndex:
    source_id: UUID
    drafts: tuple[KnowledgeChunkDraft, ...]
    vectors: EmbeddingBatch
    embedding_model: str


class KnowledgeNotFoundError(LookupError):
    def __init__(self, source_id: UUID) -> None:
        self.source_id = source_id
        super().__init__(f"knowledge source not found: {source_id}")


class KnowledgeValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"invalid knowledge source: {reason}")


def _normalize_title(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeValidationError("title_type")
    title = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(title) <= 200:
        raise KnowledgeValidationError("title_length")
    return title


def _normalize_text(value: object) -> str:
    if not isinstance(value, str):
        raise KnowledgeValidationError("text_type")
    text = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    if not 1 <= len(text) <= 200_000 or text.isspace():
        raise KnowledgeValidationError("text_length")
    return text


def _normalize_metadata(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise KnowledgeValidationError("metadata_type")
    if any(not isinstance(key, str) for key in value):
        raise KnowledgeValidationError("metadata_key")
    try:
        payload = json.dumps(
            dict(value),
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise KnowledgeValidationError("metadata_json") from error
    if len(payload.encode("utf-8")) > 16 * 1024:
        raise KnowledgeValidationError("metadata_size")
    normalized = json.loads(payload)
    assert isinstance(normalized, dict)
    return normalized


def _require_source_id(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise KnowledgeValidationError("source_id")
    return value


def _normalize_embedding_model(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeValidationError("embedding_model")
    return value.strip()


def _require_search_limit(value: object) -> int:
    if type(value) is not int or value < 1:
        raise KnowledgeValidationError("search_limit")
    return value


def _keyword_pattern(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KnowledgeValidationError("search_query")
    escaped = value.strip().replace("\\", "\\\\")
    escaped = escaped.replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _normalize_source_ids(
    value: Sequence[UUID] | None,
) -> tuple[UUID, ...] | None:
    if value is None:
        return None
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise KnowledgeValidationError("source_ids")
    source_ids = tuple(value)
    if any(not isinstance(source_id, UUID) for source_id in source_ids):
        raise KnowledgeValidationError("source_ids")
    return source_ids


def _search_hit_from_row(
    row: tuple[object, ...],
    *,
    with_distance: bool = False,
) -> KnowledgeSearchHit:
    distance: float | None = None
    if with_distance:
        try:
            distance = float(row[-1])
        except (TypeError, ValueError, OverflowError) as error:
            raise RuntimeError(
                "knowledge search returned an invalid distance"
            ) from error
        if not isfinite(distance):
            raise RuntimeError("knowledge search returned an invalid distance")
        row = row[:-1]
    return KnowledgeSearchHit(
        source_id=row[0],  # type: ignore[arg-type]
        chunk_id=row[1],  # type: ignore[arg-type]
        source_title=row[2],  # type: ignore[arg-type]
        content=row[3],  # type: ignore[arg-type]
        ordinal=row[4],  # type: ignore[arg-type]
        start_char=row[5],  # type: ignore[arg-type]
        end_char=row[6],  # type: ignore[arg-type]
        distance=distance,
    )



# 在把知识分块写入向量索引之前，统一检查分块数据和向量数据是否合法，并把向量转换成可存储的字符串格式
def _prepare_chunk_replacement(
    drafts: Sequence[KnowledgeChunkDraft],
    vectors: Sequence[Sequence[float]],
    embedding_model: object,
) -> tuple[tuple[KnowledgeChunkDraft, ...], tuple[str, ...], str]:
    if (
        isinstance(drafts, (str, bytes))
        or not isinstance(drafts, Sequence)
        or isinstance(vectors, (str, bytes))
        or not isinstance(vectors, Sequence)
    ):
        raise KnowledgeValidationError("index_batch")
    draft_batch = tuple(drafts)
    vector_batch = tuple(vectors)
    model = _normalize_embedding_model(embedding_model)
    if not draft_batch or len(draft_batch) != len(vector_batch):
        raise KnowledgeValidationError("index_batch")

    literals: list[str] = []
    for ordinal, (draft, vector) in enumerate(zip(draft_batch, vector_batch)):
        if (
            not isinstance(draft, KnowledgeChunkDraft)
            or type(draft.ordinal) is not int
            or draft.ordinal != ordinal
            or type(draft.start_char) is not int
            or type(draft.end_char) is not int
            or not 0 <= draft.start_char < draft.end_char
            or not isinstance(draft.content, str)
            or not draft.content.strip()
            or len(draft.content) != draft.end_char - draft.start_char
        ):
            raise KnowledgeValidationError("chunk_draft")
        try:
            literals.append(embedding_vector_literal(vector))  # 检查并转换向量
        except EmbeddingContractError as error:
            raise KnowledgeValidationError("embedding") from error
    return draft_batch, tuple(literals), model


# 在一整段文本中，找出每个非空段落在原字符串里的起始位置和结束位置
def _paragraph_ranges(text: str) -> tuple[tuple[int, int], ...]:
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    end = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if line.strip():
            if start is None:
                start = offset
            end = offset + len(line.rstrip("\r\n"))
        elif start is not None:
            ranges.append((start, end))
            start = None
        offset += len(line)
    if start is not None:
        ranges.append((start, end))
    return tuple(ranges)


# 把一整段知识文本按段落,切成多个不超过 max_chars 字符的知识块
def split_knowledge_text(
    text: str,
    max_chars: int = 2000,
) -> tuple[KnowledgeChunkDraft, ...]:
    if not isinstance(text, str) or not text.strip():
        raise KnowledgeValidationError("chunk_text")
    if type(max_chars) is not int or max_chars < 1:
        raise KnowledgeValidationError("chunk_chars")

    chunk_ranges: list[tuple[int, int]] = []
    current: tuple[int, int] | None = None
    for start, end in _paragraph_ranges(text):
        if end - start > max_chars:
            if current is not None:
                chunk_ranges.append(current)
                current = None
            chunk_ranges.extend(
                (window_start, min(window_start + max_chars, end))
                for window_start in range(start, end, max_chars)
            )
        elif current is None:
            current = (start, end)
        elif end - current[0] <= max_chars:
            current = (current[0], end)
        else:
            chunk_ranges.append(current)
            current = (start, end)
    if current is not None:
        chunk_ranges.append(current)

    return tuple(
        KnowledgeChunkDraft(ordinal, start, end, text[start:end])
        for ordinal, (start, end) in enumerate(chunk_ranges)
    )


def _source_from_row(row: tuple[object, ...]) -> KnowledgeSource:
    return KnowledgeSource(
        id=row[0],  # type: ignore[arg-type]
        title=row[1],  # type: ignore[arg-type]
        text=row[2],  # type: ignore[arg-type]
        metadata=dict(row[3]),  # type: ignore[arg-type]
        status=row[4],  # type: ignore[arg-type]
        embedding_model=row[5],  # type: ignore[arg-type]
        created_at=row[6],  # type: ignore[arg-type]
        updated_at=row[7],  # type: ignore[arg-type]
        indexed_at=row[8],  # type: ignore[arg-type]
    )


def _bump_epoch(connection: Connection) -> None:
    row = connection.execute(
        "UPDATE knowledge_epoch SET value = value + 1 "
        "WHERE id = 1 RETURNING value"
    ).fetchone()
    if row is None:
        raise RuntimeError("knowledge epoch is missing")


# 保存一篇新的知识原文
class KnowledgeStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    # 保存一篇新的知识原文
    def create_source(self, data: KnowledgeSourceCreate) -> KnowledgeSource:
        if not isinstance(data, KnowledgeSourceCreate):
            raise KnowledgeValidationError("create_type")
        title = _normalize_title(data.title)
        text = _normalize_text(data.text)
        metadata = _normalize_metadata(data.metadata)
        source_id = uuid4()

        with self._database.transaction() as connection:
            row = connection.execute(
                "INSERT INTO knowledge_sources (id, title, text, metadata) "
                "VALUES (%s, %s, %s, %s) "
                f"RETURNING {_SOURCE_COLUMNS}",
                (source_id, title, text, Jsonb(metadata)),
            ).fetchone()
            assert row is not None
            _bump_epoch(connection)
        return _source_from_row(row)


    # 按 UUID 读取完整原文
    def get_source(self, source_id: UUID) -> KnowledgeSource:
        source_id = _require_source_id(source_id)
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_SOURCE_COLUMNS} FROM knowledge_sources WHERE id = %s",
                (source_id,),
            ).fetchone()
        if row is None:
            raise KnowledgeNotFoundError(source_id)
        return _source_from_row(row)


    # 读取一页知识
    def list_sources(
        self,
        limit: int = 50,
        cursor: KnowledgeCursor | None = None,
    ) -> KnowledgePage:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise KnowledgeValidationError("limit")
        if cursor is not None and (
            not isinstance(cursor, KnowledgeCursor)
            or not isinstance(cursor.created_at, datetime)
            or not isinstance(cursor.id, UUID)
        ):
            raise KnowledgeValidationError("cursor")

        statement = f"SELECT {_SOURCE_COLUMNS} FROM knowledge_sources"
        parameters: tuple[object, ...]
        if cursor is None:
            parameters = (limit + 1,)
        else:
            statement += " WHERE (created_at, id) < (%s, %s)"
            parameters = (cursor.created_at, cursor.id, limit + 1)
        statement += " ORDER BY created_at DESC, id DESC LIMIT %s"

        with self._database.connection() as connection:
            rows = connection.execute(statement, parameters).fetchall()
        items = tuple(_source_from_row(row) for row in rows[:limit])
        next_cursor = (
            KnowledgeCursor(items[-1].created_at, items[-1].id)
            if len(rows) > limit
            else None
        )
        return KnowledgePage(items, next_cursor)

    def delete_source(self, source_id: UUID) -> None:
        source_id = _require_source_id(source_id)
        with self._database.transaction() as connection:
            row = connection.execute(
                "DELETE FROM knowledge_sources WHERE id = %s RETURNING id",
                (source_id,),
            ).fetchone()
            if row is None:
                raise KnowledgeNotFoundError(source_id)
            _bump_epoch(connection)

    def get_epoch(self) -> int:
        with self._database.connection() as connection:
            row = connection.execute(
                "SELECT value FROM knowledge_epoch WHERE id = 1"
            ).fetchone()
        if row is None:
            raise RuntimeError("knowledge epoch is missing")
        if type(row[0]) is not int or row[0] < 0:
            raise RuntimeError("knowledge epoch is invalid")
        return row[0]


    # 把某个知识源原来的所有分块删除，然后用新的分块和向量完整替换，并把知识源标记为已建立索引
    def replace_chunks(
        self,
        source_id: UUID,
        drafts: Sequence[KnowledgeChunkDraft],
        vectors: Sequence[Sequence[float]],
        embedding_model: str,
    ) -> IndexReport:
        source_id = _require_source_id(source_id)
        draft_batch, vector_literals, model = _prepare_chunk_replacement(
            drafts,
            vectors,
            embedding_model,
        )

        with self._database.transaction() as connection:
            source_row = connection.execute(
                "SELECT text FROM knowledge_sources WHERE id = %s FOR UPDATE",
                (source_id,),
            ).fetchone()
            if source_row is None:
                raise KnowledgeNotFoundError(source_id)
            source_text = source_row[0]
            if any(
                source_text[draft.start_char : draft.end_char] != draft.content
                for draft in draft_batch
            ):
                raise KnowledgeValidationError("draft_source_range")

            connection.execute(
                "DELETE FROM knowledge_chunks WHERE source_id = %s",
                (source_id,),
            )
            for draft, vector_literal in zip(draft_batch, vector_literals):
                connection.execute(
                    "INSERT INTO knowledge_chunks "
                    "(id, source_id, ordinal, start_char, end_char, content, "
                    "embedding, embedding_model) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s)",
                    (
                        uuid4(),
                        source_id,
                        draft.ordinal,
                        draft.start_char,
                        draft.end_char,
                        draft.content,
                        vector_literal,
                        model,
                    ),
                )
            indexed_row = connection.execute(
                "UPDATE knowledge_sources SET status = 'indexed', "
                "embedding_model = %s, indexed_at = now(), updated_at = now() "
                "WHERE id = %s RETURNING indexed_at",
                (model, source_id),
            ).fetchone()
            if indexed_row is None:
                raise RuntimeError("knowledge source disappeared while indexing")
            _bump_epoch(connection)

        return IndexReport(source_id, len(draft_batch), model, indexed_row[0])

    # 只在已经完整发布的知识分块里匹配标题或正文
    def search_keyword(
        self,
        query: str,
        limit: int,
        source_ids: Sequence[UUID] | None = None,
    ) -> tuple[KnowledgeSearchHit, ...]:
        pattern = _keyword_pattern(query)
        limit = _require_search_limit(limit)
        source_filter = _normalize_source_ids(source_ids)
        if source_filter == ():
            return ()

        statement = (
            f"SELECT {_SEARCH_HIT_COLUMNS} FROM knowledge_chunks c "
            "JOIN knowledge_sources s ON s.id = c.source_id "
            "WHERE s.status = 'indexed' "
            "AND (s.title ILIKE %s ESCAPE E'\\\\' OR "
            "c.content ILIKE %s ESCAPE E'\\\\') "
        )
        parameters: list[object] = [pattern, pattern]
        if source_filter is not None:
            statement += "AND s.id = ANY(%s::uuid[]) "
            parameters.append(list(source_filter))
        statement += (
            "ORDER BY s.created_at DESC, s.id DESC, c.ordinal, c.id LIMIT %s"
        )
        parameters.append(limit)
        with self._database.connection() as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        return tuple(_search_hit_from_row(row) for row in rows)



    # 使用一个查询向量，在知识库中查找语义最相似的知识分块，并返回前 limit 条结果
    def search_vector(
        self,
        vector: Sequence[float],
        embedding_model: str,
        limit: int,
        source_ids: Sequence[UUID] | None = None,
    ) -> tuple[KnowledgeSearchHit, ...]:
        try:
            vector_literal = embedding_vector_literal(vector)
        except EmbeddingContractError as error:
            raise KnowledgeValidationError("embedding") from error
        model = _normalize_embedding_model(embedding_model)
        limit = _require_search_limit(limit)
        source_filter = _normalize_source_ids(source_ids)
        if source_filter == ():
            return ()

        statement = (
            f"SELECT {_SEARCH_HIT_COLUMNS}, "
            "c.embedding <=> %s::vector AS distance "
            "FROM knowledge_chunks c "
            "JOIN knowledge_sources s ON s.id = c.source_id "
            "WHERE s.status = 'indexed' AND s.embedding_model = %s "
            "AND c.embedding_model = s.embedding_model "
        )
        parameters: list[object] = [vector_literal, model]
        if source_filter is not None:
            statement += "AND s.id = ANY(%s::uuid[]) "
            parameters.append(list(source_filter))
        statement += "ORDER BY distance, c.id LIMIT %s"  # 按相似度排序并限制数量
        parameters.append(limit)
        with self._database.connection() as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()
        return tuple(_search_hit_from_row(row, with_distance=True) for row in rows)


# 把现有 Store、EmbeddingClient 和固定参数放在一起
class KnowledgeIndexer:
    def __init__(
        self,
        store: KnowledgeStore,
        embedding_client: EmbeddingClient,
        embedding_model: str,
        chunk_chars: int = 2000,
        batch_size: int = 64,
    ) -> None:
        if type(chunk_chars) is not int or chunk_chars < 1:
            raise KnowledgeValidationError("chunk_chars")
        if type(batch_size) is not int or batch_size < 1:
            raise KnowledgeValidationError("embedding_batch_size")
        self._store = store
        self._embedding_client = embedding_client
        self._embedding_model = _normalize_embedding_model(embedding_model)
        self._chunk_chars = chunk_chars
        self._batch_size = batch_size

    # 按顺序完成读取、切块和批量 Embedding
    def _prepare(self, source_id: UUID) -> _PreparedKnowledgeIndex:
        source = self._store.get_source(source_id)
        drafts = split_knowledge_text(source.text, self._chunk_chars)
        vectors: list[Embedding] = []
        for offset in range(0, len(drafts), self._batch_size):
            batch = drafts[offset : offset + self._batch_size]
            batch_vectors = self._embedding_client.embed(
                tuple(draft.content for draft in batch)
            )
            if len(batch_vectors) != len(batch):
                raise EmbeddingContractError(
                    "knowledge embedding count does not match chunk count"
                )
            vectors.extend(batch_vectors)

        prepared_vectors = tuple(vectors)
        if len(prepared_vectors) != len(drafts):
            raise EmbeddingContractError(
                "knowledge embedding total does not match chunk count"
            )
        return _PreparedKnowledgeIndex(
            source.id,
            drafts,
            prepared_vectors,
            self._embedding_model,
        )

    def reindex(self, source_id: UUID) -> IndexReport:
        prepared = self._prepare(source_id)
        return self._store.replace_chunks(
            prepared.source_id,
            prepared.drafts,
            prepared.vectors,
            prepared.embedding_model,
        )
