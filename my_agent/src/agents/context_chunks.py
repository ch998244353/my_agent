from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from .contracts import ChatMessage, MessageRole

if TYPE_CHECKING:
    from .agent import Agent
    from .run_context import RunContextWrapper

CONTEXT_CHUNK_SYSTEM_INSTRUCTIONS = "system_instructions"
CONTEXT_CHUNK_SESSION_SUMMARY = "session_summary"
CONTEXT_CHUNK_MEMORY = "memory"
CONTEXT_CHUNK_WORKSPACE_CONTEXT = "workspace_context"
CONTEXT_CHUNK_REPO_CONTEXT = "repo_context"
CONTEXT_CHUNK_SELECTED_FILES = "selected_files"
CONTEXT_CHUNK_VERIFICATION_SUMMARY = "verification_summary"
RESERVED_CONTEXT_CHUNK_NAMES = (
    CONTEXT_CHUNK_WORKSPACE_CONTEXT,
    CONTEXT_CHUNK_REPO_CONTEXT,
    CONTEXT_CHUNK_SELECTED_FILES,
    CONTEXT_CHUNK_VERIFICATION_SUMMARY,
)

SYSTEM_CONTEXT_PRIORITY = 0
SESSION_SUMMARY_CONTEXT_PRIORITY = 25
REPO_CONTEXT_PRIORITY = 45
MEMORY_CONTEXT_PRIORITY = 50
SELECTED_FILES_CONTEXT_PRIORITY = 60


@dataclass(frozen=True)
class ContextChunk:
    name: str
    role: MessageRole
    content: str
    priority: int
    source: str

    def has_content(self) -> bool:
        return bool(self.content.strip())

    def to_message(self) -> ChatMessage:
        return ChatMessage(role=self.role, content=self.content)


@dataclass(frozen=True)
class TurnContextBundle:
    chunks: tuple[ContextChunk, ...]

    def to_messages(self) -> list[ChatMessage]:
        return render_context_messages(self.chunks)


def sort_context_chunks(chunks: Iterable[ContextChunk]) -> list[ContextChunk]:
    indexed_chunks = list(enumerate(chunks))
    ordered = sorted(indexed_chunks, key=lambda item: (item[1].priority, item[0]))
    return [chunk for _, chunk in ordered]


def render_context_messages(chunks: Iterable[ContextChunk]) -> list[ChatMessage]:
    return [
        chunk.to_message()
        for chunk in sort_context_chunks(chunks)
        if chunk.has_content()
    ]


def _memory_summary_message(agent: Agent) -> ChatMessage | None:
    summary = getattr(agent.memory, "summary", None)
    if summary is None:
        return None
    content = getattr(summary, "content", "")
    if not isinstance(content, str) or not content.strip():
        return None
    to_message = getattr(summary, "to_message", None)
    if not callable(to_message):
        return None
    return to_message()


def _memory_messages_without_summary(
    agent: Agent,
    summary_message: ChatMessage | None,
) -> list[ChatMessage]:
    messages = agent.memory.to_messages()
    if summary_message is not None and messages and messages[0] == summary_message:
        return messages[1:]
    return messages


def _selected_files_context_chunk(
    context_wrapper: RunContextWrapper | None,
) -> ContextChunk | None:
    if context_wrapper is None:
        return None
    selected_files = context_wrapper.selected_files
    if selected_files is None:
        return None
    summary = selected_files.summary()
    if not summary.strip():
        return None
    return ContextChunk(
        name=CONTEXT_CHUNK_SELECTED_FILES,
        role="user",
        content=f"Selected files:\n{summary}",
        priority=SELECTED_FILES_CONTEXT_PRIORITY,
        source="selected_files",
    )


def _repo_context_chunk(
    context_wrapper: RunContextWrapper | None,
) -> ContextChunk | None:
    if context_wrapper is None:
        return None
    repo_context = context_wrapper.repo_context
    if repo_context is None:
        return None
    if (
        not repo_context.sections
        and not repo_context.selected_paths
        and not repo_context.mentioned_symbols
    ):
        return None
    return ContextChunk(
        name=CONTEXT_CHUNK_REPO_CONTEXT,
        role="user",
        content=repo_context.to_text(),
        priority=REPO_CONTEXT_PRIORITY,
        source="repo_context",
    )


def build_turn_context(
    agent: Agent,
    context_wrapper: RunContextWrapper | None = None,
) -> TurnContextBundle:
    chunks: list[ContextChunk] = []

    if agent.instructions:
        chunks.append(
            ContextChunk(
                name=CONTEXT_CHUNK_SYSTEM_INSTRUCTIONS,
                role="system",
                content=agent.instructions,
                priority=SYSTEM_CONTEXT_PRIORITY,
                source="agent",
            )
        )

    summary_message = _memory_summary_message(agent)
    if summary_message is not None:
        chunks.append(
            ContextChunk(
                name=CONTEXT_CHUNK_SESSION_SUMMARY,
                role=summary_message.role,
                content=summary_message.content,
                priority=SESSION_SUMMARY_CONTEXT_PRIORITY,
                source="memory.summary",
            )
        )

    repo_context_chunk = _repo_context_chunk(context_wrapper)
    if repo_context_chunk is not None:
        chunks.append(repo_context_chunk)

    chunks.extend(
        ContextChunk(
            name=CONTEXT_CHUNK_MEMORY,
            role=message.role,
            content=message.content,
            priority=MEMORY_CONTEXT_PRIORITY,
            source="memory",
        )
        for message in _memory_messages_without_summary(agent, summary_message)
    )

    selected_files_chunk = _selected_files_context_chunk(context_wrapper)
    if selected_files_chunk is not None:
        chunks.append(selected_files_chunk)

    return TurnContextBundle(chunks=tuple(chunks))
