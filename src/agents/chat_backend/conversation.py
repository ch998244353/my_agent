from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4  # 通用唯一标识符

from psycopg import Connection
from psycopg.types.json import Jsonb

from ..contracts import ChatMessage, MessageRole
from .database import Database


ConversationStatus = Literal["active", "archived"]
ConversationMode = Literal["default", "plan"]

_CONVERSATION_COLUMNS = (
    "id, created_at, next_message_sequence, title, status, mode"
)
_MESSAGE_COLUMNS = (
    "id, conversation_id, sequence, role, content, created_at, run_trace"
)
_RUN_TRACE_STAGES = frozenset(
    {"retrieval", "model", "retry", "tool", "saving", "done", "error"}
)


@dataclass(frozen=True)
class Conversation:
    id: UUID
    created_at: datetime
    next_message_sequence: int
    title: str = "New conversation"
    status: ConversationStatus = "active"
    mode: ConversationMode = "default"

    def __post_init__(self) -> None:
        if self.mode != "default" and self.mode != "plan":
            raise ValueError("mode must be default or plan")


@dataclass(frozen=True)
class ConversationCursor:
    created_at: datetime
    conversation_id: UUID


@dataclass(frozen=True)
class ConversationPage:
    items: tuple[Conversation, ...]
    next_cursor: ConversationCursor | None


@dataclass(frozen=True)
class ConversationPatch:
    title: str | None = None
    status: ConversationStatus | None = None


# 保存一条已经进入数据库的消息
@dataclass(frozen=True)
class StoredMessage:
    id: UUID
    conversation_id: UUID
    sequence: int
    role: MessageRole
    content: str
    created_at: datetime
    run_trace: tuple[dict[str, object], ...] = ()


# 保存一页历史消息
@dataclass(frozen=True)
class MessagePage:
    items: tuple[StoredMessage, ...]
    before_sequence: int | None


class ConversationNotFoundError(LookupError):
    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"conversation not found: {conversation_id}")


class ConversationArchivedError(RuntimeError):
    def __init__(self, conversation_id: UUID) -> None:
        self.conversation_id = conversation_id
        super().__init__(f"conversation is archived: {conversation_id}")


def _require_conversation(connection: Connection, conversation_id: UUID) -> None:
    row = connection.execute(
        "SELECT 1 FROM conversations WHERE id = %s",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ConversationNotFoundError(conversation_id)


# 把数据库会话数据转成 Conversation
def _conversation_from_row(
    row: tuple[UUID, datetime, int, str, ConversationStatus, ConversationMode],
) -> Conversation:
    return Conversation(*row)


# 把数据库返回的一行消息数据转成 StoredMessage
def _message_from_row(
    row: tuple[object, ...],
) -> StoredMessage:
    trace = tuple(row[6] or ()) if len(row) > 6 else ()
    return StoredMessage(*row[:6], trace)  # type: ignore[arg-type]


def _normalize_run_trace(
    entries: Sequence[dict[str, object]],
) -> tuple[dict[str, object], ...]:
    if len(entries) > 200:
        raise ValueError("run trace must not contain more than 200 entries")
    normalized: list[dict[str, object]] = []
    for entry in entries:
        stage = entry.get("stage")
        message = entry.get("message")
        if stage not in _RUN_TRACE_STAGES or not isinstance(message, str):
            raise ValueError("run trace entries require a valid stage and message")
        text = message.strip()
        if not text or len(text) > 500:
            raise ValueError("run trace message must contain 1 to 500 characters")
        item: dict[str, object] = {"stage": stage, "message": text}
        step_number = entry.get("step_number")
        if isinstance(step_number, int) and not isinstance(step_number, bool):
            item["step_number"] = step_number
        normalized.append(item)
    return tuple(normalized)


def _require_positive_limit(limit: int, maximum: int | None = None) -> None:
    if limit <= 0:
        raise ValueError("limit must be positive")
    if maximum is not None and limit > maximum:
        raise ValueError(f"limit must not exceed {maximum}")


def _normalize_title(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("title must be a string")
    title = value.strip()
    if not 1 <= len(title) <= 200:
        raise ValueError("title must contain between 1 and 200 characters")
    return title


def _require_conversation_status(value: object) -> ConversationStatus:
    if value == "active" or value == "archived":
        return value
    raise ValueError("status must be active or archived")


# 创建和读取会话
class ConversationStore:
    def __init__(self, database: Database) -> None:
        self._database = database


    # 创建会话
    def create(self, title: str = "New conversation") -> Conversation:
        title = _normalize_title(title)
        conversation_id = uuid4()
        with self._database.connection() as connection:
            row = connection.execute(
                "INSERT INTO conversations (id, title) VALUES (%s, %s) "
                f"RETURNING {_CONVERSATION_COLUMNS}",
                (conversation_id, title),
            ).fetchone()
        assert row is not None
        return _conversation_from_row(row)


    # 读取会话
    def get(self, conversation_id: UUID) -> Conversation:
        with self._database.connection() as connection:
            row = connection.execute(
                f"SELECT {_CONVERSATION_COLUMNS} "
                "FROM conversations WHERE id = %s",
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        return _conversation_from_row(row)

    def list(
        self,
        *,
        page_size: int = 50,
        before: ConversationCursor | None = None,
        status: ConversationStatus | None = None,
    ) -> ConversationPage:
        if (
            isinstance(page_size, bool)
            or not isinstance(page_size, int)
            or not 1 <= page_size <= 100
        ):
            raise ValueError("page_size must be an integer between 1 and 100")
        if before is not None and (
            not isinstance(before, ConversationCursor)
            or not isinstance(before.created_at, datetime)
            or not isinstance(before.conversation_id, UUID)
        ):
            raise ValueError("before must be a valid ConversationCursor")
        if status is not None:
            status = _require_conversation_status(status)

        conditions: list[str] = []
        parameters: list[object] = []
        if before is not None:
            conditions.append("(created_at, id) < (%s, %s)")
            parameters.extend((before.created_at, before.conversation_id))
        if status is not None:
            conditions.append("status = %s")
            parameters.append(status)

        statement = f"SELECT {_CONVERSATION_COLUMNS} FROM conversations"
        if conditions:
            statement += " WHERE " + " AND ".join(conditions)
        statement += " ORDER BY created_at DESC, id DESC LIMIT %s"
        parameters.append(page_size + 1)
        with self._database.connection() as connection:
            rows = connection.execute(statement, tuple(parameters)).fetchall()

        has_more = len(rows) > page_size
        items = tuple(_conversation_from_row(row) for row in rows[:page_size])
        next_cursor = (
            ConversationCursor(items[-1].created_at, items[-1].id)
            if has_more
            else None
        )
        return ConversationPage(items, next_cursor)

    def update(
        self,
        conversation_id: UUID,
        patch: ConversationPatch,
    ) -> Conversation:
        if not isinstance(patch, ConversationPatch):
            raise ValueError("patch must be a ConversationPatch")

        assignments: list[str] = []
        parameters: list[object] = []
        if patch.title is not None:
            assignments.append("title = %s")
            parameters.append(_normalize_title(patch.title))
        if patch.status is not None:
            assignments.append("status = %s")
            parameters.append(_require_conversation_status(patch.status))
        if not assignments:
            raise ValueError("patch must change title or status")

        parameters.append(conversation_id)
        with self._database.connection() as connection:
            row = connection.execute(
                "UPDATE conversations SET "
                + ", ".join(assignments)
                + f" WHERE id = %s RETURNING {_CONVERSATION_COLUMNS}",
                tuple(parameters),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError(conversation_id)
        return _conversation_from_row(row)

    def delete(self, conversation_id: UUID) -> None:
        with self._database.connection() as connection:
            row = connection.execute(
                "DELETE FROM conversations WHERE id = %s RETURNING id",
                (conversation_id,),
            ).fetchone()
        if row is None:
            raise ConversationNotFoundError(conversation_id)


# 批量保存和按顺序读取消息
class MessageStore:
    def __init__(self, database: Database) -> None:
        self._database = database


    # 批量保存消息
    def add_batch(
        self,
        conversation_id: UUID,
        items: Sequence[ChatMessage],
    ) -> tuple[StoredMessage, ...]:
        items = tuple(items)
        if not items:
            return ()

        stored: list[StoredMessage] = []
         # 在一个事务里，给这一批消息一次性申请一段连续的 sequence 编号，并且保证并发情况下不会重复
        with self._database.transaction() as connection: 
            sequence_row = connection.execute(
                "UPDATE conversations "
                "SET next_message_sequence = next_message_sequence + %s "
                "WHERE id = %s "
                "RETURNING next_message_sequence - %s",  # 返回原本的数据下标, 之后根据这一行来添加新数据
                (len(items), conversation_id, len(items)),
            ).fetchone()
            if sequence_row is None:
                raise ConversationNotFoundError(conversation_id)

            first_sequence = sequence_row[0]
            for offset, item in enumerate(items):  # 根据初始值以及下标放入
                row = connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, sequence, role, content) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    f"RETURNING {_MESSAGE_COLUMNS}",
                    (
                        uuid4(),
                        conversation_id,
                        first_sequence + offset,
                        item.role,
                        item.content,
                    ),
                ).fetchone()
                assert row is not None
                stored.append(_message_from_row(row))
        return tuple(stored)

    def set_run_trace(
        self,
        message_id: UUID,
        entries: Sequence[dict[str, object]],
    ) -> StoredMessage:
        trace = _normalize_run_trace(entries)
        with self._database.connection() as connection:
            row = connection.execute(
                "UPDATE conversation_messages SET run_trace = %s "
                "WHERE id = %s AND role = 'assistant' "
                f"RETURNING {_MESSAGE_COLUMNS}",
                (Jsonb(list(trace)), message_id),
            ).fetchone()
        if row is None:
            raise LookupError(f"assistant message not found: {message_id}")
        return _message_from_row(row)

    def list(
        self,
        conversation_id: UUID,
        limit: int | None = None,
    ) -> tuple[StoredMessage, ...]:
        if limit is not None:
            _require_positive_limit(limit)
        return self._read_messages(conversation_id, limit=limit)


    # 读取一页历史消息
    def page(
        self,
        conversation_id: UUID,
        page_size: int,  # 一页返回多少条
        before_sequence: int | None = None,  # 只查询序号小于这个值的消息
    ) -> MessagePage:
        _require_positive_limit(page_size, maximum=100)
        if before_sequence is not None:
            _require_positive_limit(before_sequence)

        messages = self._read_messages(
            conversation_id,
            limit=page_size + 1,
            before_sequence=before_sequence,
        )
        items = messages[-page_size:]
        next_before = items[0].sequence if len(messages) > page_size else None
        return MessagePage(items, next_before)


    # 弹出最后一条消息
    def pop_last(self, conversation_id: UUID) -> StoredMessage | None:
        with self._database.transaction() as connection:
            _require_conversation(connection, conversation_id)
            row = connection.execute(
                "DELETE FROM conversation_messages "
                "WHERE id = ("
                "SELECT id FROM conversation_messages "
                "WHERE conversation_id = %s "
                "ORDER BY sequence DESC LIMIT 1"
                ") "
                f"RETURNING {_MESSAGE_COLUMNS}",
                (conversation_id,),
            ).fetchone()
        return None if row is None else _message_from_row(row)


    # 清空消息但保留会话
    def clear(self, conversation_id: UUID) -> None:
        with self._database.transaction() as connection:
            _require_conversation(connection, conversation_id)
            connection.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = %s",
                (conversation_id,),
            )


    # 读取某个对话的全部消息或最新 limit 条消息
    def _read_messages(
        self,
        conversation_id: UUID,
        *,
        limit: int | None,
        before_sequence: int | None = None,
    ) -> tuple[StoredMessage, ...]:
        with self._database.connection() as connection:
            _require_conversation(connection, conversation_id)

            statement = (
                f"SELECT {_MESSAGE_COLUMNS} FROM conversation_messages "
                "WHERE conversation_id = %s "
            )
            parameters: tuple[object, ...] = (conversation_id,)
            if before_sequence is not None:
                statement += "AND sequence < %s "
                parameters += (before_sequence,)
            if limit is None:
                statement += "ORDER BY sequence"
            else:
                statement += "ORDER BY sequence DESC LIMIT %s"
                parameters += (limit,)

            rows = connection.execute(statement, parameters).fetchall()

        if limit is not None:
            rows.reverse()
        return tuple(_message_from_row(row) for row in rows)
