from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from uuid import UUID

from ..agent import Agent
from ..chat import chat_turn_from_result
from ..events import RunEvent
from ..run_config import RunConfig
from ..runner import Runner
from .conversation import (
    ConversationArchivedError,
    ConversationNotFoundError,
    ConversationStore,
    MessageStore,
)
from .memory import MemorySource
from .memory_extraction import MemoryExtractionService
from .postgres_session import CommitOnSuccessSession, PostgresSession
from .retrieval import (
    Citation,
    HybridRetrievalService,
    MemoryRetrievalService,
    parse_citations,
)


_MAX_MESSAGE_CHARS = 20_000
_LOGGER = logging.getLogger(__name__)


# 一条已经成功写进数据库的最终回答
@dataclass(frozen=True)
class ChatReply:
    conversation_id: UUID
    message_id: UUID
    sequence: int
    content: str
    citations: tuple[Citation, ...] = ()


class ChatInputError(ValueError):
    pass


class ChatRunError(RuntimeError):
    pass


class ChatPersistenceError(RuntimeError):
    pass


# 把一次“用户发消息”的完整流程串起来
class ChatService:
    def __init__(
        self,
        agent_factory: Callable[[], Agent],
        conversations: ConversationStore,
        messages: MessageStore,
        run_config: RunConfig | None = None,
        memory_extraction: MemoryExtractionService | None = None,
        memory_retrieval: MemoryRetrievalService | None = None,
        hybrid_retrieval: HybridRetrievalService | None = None,
    ) -> None:
        if memory_retrieval is not None and hybrid_retrieval is not None:
            raise ValueError(
                "memory_retrieval and hybrid_retrieval are mutually exclusive"
            )
        self._agent_factory = agent_factory
        self._conversations = conversations
        self._messages = messages
        self._run_config = run_config
        self._memory_extraction = memory_extraction
        self._memory_retrieval = memory_retrieval
        self._hybrid_retrieval = hybrid_retrieval

    async def send_message(
        self,
        conversation_id: UUID,
        content: str,
        *,
        on_event: Callable[[RunEvent], None] | None = None,
    ) -> ChatReply:
        content = content.strip()
        if not content:
            raise ChatInputError("message must not be empty")
        if len(content) > _MAX_MESSAGE_CHARS:
            raise ChatInputError("message must not exceed 20000 characters")

        try:
            conversation = await asyncio.to_thread(
                self._conversations.get,
                conversation_id,
            )
        except ConversationNotFoundError:
            raise
        except Exception as exc:
            raise ChatPersistenceError("conversation could not be read") from exc
        if conversation.status == "archived":
            raise ConversationArchivedError(conversation_id)

        retrieved_chunks = ()
        allowed_citations: tuple[Citation, ...] = ()
        if self._hybrid_retrieval is not None:
            try:
                bundle = await asyncio.to_thread(
                    self._hybrid_retrieval.retrieve,
                    content,
                    conversation_id,
                )
                retrieved_chunks = bundle.context_chunks
                allowed_citations = bundle.citations
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ChatPersistenceError(
                    "hybrid retrieval could not be read"
                ) from exc
        elif self._memory_retrieval is not None:
            try:
                retrieved_chunks = await asyncio.to_thread(
                    self._memory_retrieval.retrieve,
                    content,
                    conversation_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise ChatPersistenceError(
                    "memory retrieval could not be read"
                ) from exc

        persistent = PostgresSession(conversation_id, self._messages)
        session = CommitOnSuccessSession(persistent, self._messages)
        base_config = self._run_config or RunConfig()
        config = replace(
            base_config,
            session=session,
            context_chunks=(
                *base_config.context_chunks,
                *retrieved_chunks,
            ),
        )

        try:
            agent = self._agent_factory()
            if on_event is None:
                result = await Runner.run_async(agent, content, config)
            else:
                stream = Runner.run_stream(agent, content, config)
                async with stream:
                    try:
                        async for event in stream:
                            on_event(event)
                        result = await stream.result()
                    except asyncio.CancelledError:
                        stream.controller.cancel("client_disconnected")
                        raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ChatRunError("chat run failed") from exc

        turn = chat_turn_from_result(result)
        answer = turn.answer
        if not turn.reached_final_answer or answer is None or not answer.strip():
            raise ChatRunError("chat run did not produce a final answer")

        try:
            stored = await asyncio.to_thread(session.commit, answer)
        except Exception as exc:
            raise ChatPersistenceError("chat messages could not be saved") from exc
        if (
            not stored
            or stored[-1].role != "assistant"
            or stored[-1].content != answer
        ):
            raise ChatPersistenceError(
                "saved chat did not end with the final assistant message"
            )

        user_messages = tuple(message for message in stored if message.role == "user")
        if len(user_messages) != 1:
            raise ChatPersistenceError(
                "saved chat did not contain exactly one user message"
            )

        user_message = user_messages[0]
        final_message = stored[-1]
        citations = parse_citations(final_message.content, allowed_citations)
        reply = ChatReply(
            conversation_id=conversation_id,
            message_id=final_message.id,
            sequence=final_message.sequence,
            content=final_message.content,
            citations=citations,
        )

        if self._memory_extraction is None:
            return reply

        source = MemorySource(
            conversation_id,
            user_message.id,
            final_message.id,
        )
        try:
            candidates = await self._memory_extraction.extract(
                user_message.content,
                final_message.content,
            )
            if candidates:
                await asyncio.to_thread(
                    self._memory_extraction.save_candidates,
                    source,
                    candidates,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _LOGGER.warning(
                "Memory extraction failed after chat commit: "
                "conversation_id=%s user_message_id=%s "
                "assistant_message_id=%s error_type=%s",
                conversation_id,
                user_message.id,
                final_message.id,
                type(exc).__name__,
            )
        return reply
