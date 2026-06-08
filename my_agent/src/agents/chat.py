from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .result import RunResult
from .run_config import RunConfig, SessionLike
from .runner import Runner

if TYPE_CHECKING:
    from .agent import Agent


__all__ = [
    "ChatDiagnostics",
    "ChatTurn",
    "chat_stop_reason",
    "chat_turn_status_text",
    "chat_turn_from_result",
    "run_chat_turn",
]


@dataclass(frozen=True)
class ChatTurn:
    answer: str | None
    reached_final_answer: bool
    stop_reason: str | None
    new_items_count: int
    has_final_answer: bool = False
    model_response_count: int = 0
    tool_call_count: int = 0
    error_count: int = 0
    error_summary: str | None = None
    stopped_by_max_turns: bool = False
    stopped_by_tool_use: bool = False

    @property
    def has_error(self) -> bool:
        return self.error_summary is not None


@dataclass(frozen=True)
class ChatDiagnostics:
    last_turn: ChatTurn | None = None
    turn_count: int = 0

    @property
    def has_last_turn(self) -> bool:
        return self.last_turn is not None

    @property
    def has_error(self) -> bool:
        return self.last_turn.has_error if self.last_turn is not None else False

    @property
    def error_summary(self) -> str | None:
        if self.last_turn is None:
            return None
        return self.last_turn.error_summary

    @property
    def status_text(self) -> str | None:
        if self.last_turn is None:
            return None
        return chat_turn_status_text(self.last_turn)


@dataclass(frozen=True)
class _ChatItemSummary:
    stop_reason: str | None = None
    tool_call_count: int = 0
    error_count: int = 0
    error_summary: str | None = None


def run_chat_turn(
    agent: Agent,
    message: str,
    *,
    session: SessionLike | None = None,
    config: RunConfig | None = None,
    max_turns: int | None = None,
) -> RunResult:
    config = _chat_config(config, session=session, max_turns=max_turns)
    return Runner.run_sync(agent, message, config=config)


def _chat_config(
    config: RunConfig | None,
    *,
    session: SessionLike | None,
    max_turns: int | None,
) -> RunConfig | None:
    if session is None and max_turns is None:
        return config
    if config is None:
        return RunConfig(session=session, max_turns=max_turns)
    return replace(
        config,
        session=session if session is not None else config.session,
        max_turns=max_turns if max_turns is not None else config.max_turns,
    )


def chat_stop_reason(result: RunResult) -> str | None:
    return _chat_item_summary(result).stop_reason


def chat_turn_status_text(turn: ChatTurn) -> str:
    if turn.has_final_answer or turn.answer is not None:
        return "Final answer received."
    if turn.has_error and turn.error_summary is not None:
        return _chat_error_text(turn.error_summary)
    if turn.stopped_by_max_turns:
        return "No final answer before max turns were reached."
    if turn.stopped_by_tool_use:
        return "Tool use stopped this turn before a final answer."
    if turn.stop_reason is not None:
        return f"No final answer. Stop reason: {turn.stop_reason}."
    return "No final answer."


def _chat_error_text(error_summary: str) -> str:
    kind, _, detail = error_summary.partition(":")
    detail = _short_chat_text(detail.strip() or error_summary)
    if kind == "model_error":
        return f"Model error: {detail}"
    if kind == "tool_error":
        return f"Tool error: {detail}"
    return f"Run error: {_short_chat_text(error_summary)}"


def _short_chat_text(value: object, limit: int = 80) -> str:
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 3]}..."


def _chat_item_summary(result: RunResult) -> _ChatItemSummary:
    stop_reason = None
    tool_call_count = 0
    error_count = 0
    error_summary = None
    for item in result.new_items:
        item_type = item.item_type
        if item_type == "run_stopped":
            stop_reason = str(item.payload)
        elif item_type == "tool_call":
            tool_call_count += 1
        elif item_type in {"model_error", "tool_error"}:
            error_count += 1
            if error_summary is None:
                error_summary = f"{item_type}: {item.payload}"
    return _ChatItemSummary(
        stop_reason=stop_reason,
        tool_call_count=tool_call_count,
        error_count=error_count,
        error_summary=error_summary,
    )


def chat_turn_from_result(result: RunResult) -> ChatTurn:
    answer = None if result.final_output is None else str(result.final_output)
    summary = _chat_item_summary(result)
    return ChatTurn(
        answer=answer,
        reached_final_answer=result.reached_final_answer,
        stop_reason=summary.stop_reason,
        new_items_count=len(result.new_items),
        has_final_answer=result.final_output is not None,
        model_response_count=len(getattr(result, "raw_responses", ())),
        tool_call_count=summary.tool_call_count,
        error_count=summary.error_count,
        error_summary=summary.error_summary,
        stopped_by_max_turns=summary.stop_reason == "max_turns_reached",
        stopped_by_tool_use=summary.stop_reason in {"tool_use", "stop_on_first_tool"},
    )
