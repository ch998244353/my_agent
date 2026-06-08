from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from .agent import Agent
from .chat import ChatDiagnostics, ChatTurn, chat_turn_from_result, run_chat_turn
from .memory import AgentMemory, AgentSession, JsonSession
from .models import OpenAIResponsesModel
from .run_config import RunConfig, SessionLike


__all__ = [
    "ChatRuntime",
    "ChatRuntimeConfig",
    "build_chat_agent",
    "build_chat_runtime",
    "build_chat_session",
]


@dataclass(frozen=True)
class ChatRuntimeConfig:
    model: str = "gpt-5.4"
    instructions: str | None = None
    session_path: str | Path | None = None
    use_session: bool = True
    agent_name: str = "ChatAgent"
    max_steps: int = 5
    max_turns: int | None = None
    model_client: Any | None = None
    run_config: RunConfig | None = None

    def __post_init__(self) -> None:
        if self.max_steps < 1:
            raise ValueError("max_steps must be at least 1.")
        if self.max_turns is not None and self.max_turns < 1:
            raise ValueError("max_turns must be at least 1.")


def build_chat_agent(config: ChatRuntimeConfig) -> Agent:
    return Agent(
        memory=AgentMemory(),
        model=OpenAIResponsesModel(
            model=config.model,
            client=config.model_client,
        ),
        name=config.agent_name,
        instructions=config.instructions,
        max_steps=config.max_steps,
    )


def build_chat_session(
    config: ChatRuntimeConfig,
) -> AgentSession | JsonSession | None:
    if not config.use_session:
        return None
    if config.session_path is not None:
        return JsonSession(
            config.session_path,
            max_turns=config.max_turns,
        )
    return AgentSession(max_turns=config.max_turns)


def build_chat_runtime(config: ChatRuntimeConfig) -> ChatRuntime:
    return ChatRuntime(
        agent=build_chat_agent(config),
        session=build_chat_session(config),
        use_session=config.use_session,
        max_turns=config.max_turns,
        run_config=config.run_config,
    )


@dataclass
class ChatRuntime:
    agent: Agent
    session: SessionLike | None = None
    use_session: bool = True
    max_turns: int | None = None
    run_config: RunConfig | None = None
    last_turn: ChatTurn | None = None
    turn_count: int = 0

    def run_turn(self, message: str) -> ChatTurn:
        config = _effective_run_config(
            self.run_config,
            session=self._active_session(),
            max_turns=self.max_turns,
        )
        result = run_chat_turn(
            self.agent,
            message,
            config=config,
        )
        turn = chat_turn_from_result(result)
        self.last_turn = turn
        self.turn_count += 1
        return turn

    @property
    def diagnostics(self) -> ChatDiagnostics:
        return ChatDiagnostics(
            last_turn=self.last_turn,
            turn_count=self.turn_count,
        )

    @property
    def session_enabled(self) -> bool:
        return self.use_session

    @property
    def session_mode(self) -> str:
        if not self.session_enabled or self.session is None:
            return "disabled"
        if isinstance(self.session, JsonSession):
            return "json"
        return "memory"

    @property
    def session_status_text(self) -> str:
        mode = self.session_mode
        if mode == "json":
            return "json session (history is saved to disk)."
        if mode == "memory":
            return "memory session (history is kept for this process only)."
        return "disabled session (history is not stored)."

    def clear_session(self) -> bool:
        session = self._active_session()
        if session is None:
            return False
        session.clear_session()
        return True

    def history(self, limit: int | None = None) -> list[Any]:
        session = self._active_session()
        if session is None:
            return []
        return session.get_items(limit=limit)

    def _active_session(self) -> SessionLike | None:
        if not self.session_enabled:
            return None
        return self.session


def _effective_run_config(
    config: RunConfig | None,
    *,
    session: SessionLike | None,
    max_turns: int | None,
) -> RunConfig | None:
    resolved_session = _resolve_run_session(config, session)
    resolved_max_turns = _resolve_run_max_turns(config, max_turns)
    if config is None:
        if resolved_session is None and resolved_max_turns is None:
            return None
        return RunConfig(session=resolved_session, max_turns=resolved_max_turns)
    if resolved_session is config.session and resolved_max_turns == config.max_turns:
        return config
    return replace(
        config,
        session=resolved_session,
        max_turns=resolved_max_turns,
    )


def _resolve_run_session(
    config: RunConfig | None,
    runtime_session: SessionLike | None,
) -> SessionLike | None:
    if runtime_session is not None:
        return runtime_session
    if config is None:
        return None
    return config.session


def _resolve_run_max_turns(
    config: RunConfig | None,
    runtime_max_turns: int | None,
) -> int | None:
    if runtime_max_turns is not None:
        return runtime_max_turns
    if config is None:
        return None
    return config.max_turns
