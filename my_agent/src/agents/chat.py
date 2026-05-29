from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from .result import RunResult
from .run_config import RunConfig, SessionLike
from .runner import Runner

if TYPE_CHECKING:
    from .agent import Agent


@dataclass(frozen=True)
class ChatTurn:
    answer: str | None
    reached_final_answer: bool
    stop_reason: str | None
    new_items_count: int


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
    for item in reversed(result.new_items):
        if item.item_type == "run_stopped":
            return str(item.payload)
    return None


def chat_turn_from_result(result: RunResult) -> ChatTurn:
    answer = None if result.final_output is None else str(result.final_output)
    return ChatTurn(
        answer=answer,
        reached_final_answer=result.reached_final_answer,
        stop_reason=chat_stop_reason(result),
        new_items_count=len(result.new_items),
    )
