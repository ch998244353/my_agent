from __future__ import annotations

from typing import Any
from uuid import UUID

from ..contracts import ChatMessage
from ..memory import session_item_to_message
from .conversation import MessageStore, StoredMessage


# 保存 conversation_id 与 对应 数据库
class PostgresSession:
    def __init__(self, conversation_id: UUID, store: MessageStore) -> None:
        self.conversation_id = conversation_id
        self._store = store

    # 把数据库历史变成 Runner 能直接使用的消息列表
    def get_items(self, limit: int | None = None) -> list[ChatMessage]:
        if limit is not None and limit <= 0:
            return []
        return [
            ChatMessage(message.role, message.content)
            for message in self._store.list(self.conversation_id, limit)
        ]

    # 把 Runner 给出的不同消息形式统一后整批写入数据库
    def add_items(self, items: list[Any]) -> None:
        self._store.add_batch(
            self.conversation_id,
            [session_item_to_message(item) for item in items],
        )

    def pop_item(self) -> ChatMessage | None:
        message = self._store.pop_last(self.conversation_id)
        if message is None:
            return None
        return ChatMessage(message.role, message.content)

    def clear_session(self) -> None:
        self._store.clear(self.conversation_id)


# 一次性消息暂存区
class CommitOnSuccessSession:
    def __init__(self, session: PostgresSession, store: MessageStore) -> None:
        self._session = session  # 用来读取已有聊天记录。
        self._store = store
        self._pending: list[ChatMessage] = []   #  暂存本轮 Runner 产生的新消息, 暂时不放入数据库
        self._committed = False   #  防止同一轮消息重复写入数据库

    def get_items(self, limit: int | None = None) -> list[ChatMessage]:  # 直接读取数据库里的既有聊天记录
        return self._session.get_items(limit)

    def add_items(self, items: list[Any]) -> None:  #暂存 Runner 产生的新消息
        if self._committed:
            raise RuntimeError("chat session is already committed")
        self._pending.extend(session_item_to_message(item) for item in items)

    def pop_item(self) -> ChatMessage | None:  # 删除并返回数据库中最后一条消息
        return self._session.pop_item()

    def clear_session(self) -> None:
        self._session.clear_session()

    # 核对 Runner 最终答案，然后整批保存本轮消息
    def commit(
        self,
        final_answer: str,
        *,
        stored_answer: str | None = None,
    ) -> tuple[StoredMessage, ...]:
        if self._committed:
            raise RuntimeError("chat session is already committed")
        if (
            not self._pending
            or self._pending[-1].role != "assistant"
            or self._pending[-1].content != final_answer
        ):
            raise RuntimeError("chat session does not end with the final answer")
        if stored_answer is not None and not stored_answer.strip():
            raise ValueError("stored_answer must be non-empty text")

        pending = self._pending
        if stored_answer is not None:
            pending = [
                *pending[:-1],
                ChatMessage("assistant", stored_answer),
            ]

        self._committed = True
        return self._store.add_batch(
            self._session.conversation_id,
            pending,
        )
