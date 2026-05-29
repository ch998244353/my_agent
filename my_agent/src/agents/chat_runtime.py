from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent import Agent
from .chat import ChatTurn, chat_turn_from_result, run_chat_turn
from .memory import AgentMemory, AgentSession, JsonSession
from .models import OpenAIResponsesModel
from .run_config import RunConfig, SessionLike


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
        max_turns=config.max_turns,
    )


@dataclass
class ChatRuntime:
    agent: Agent
    session: SessionLike | None = None
    max_turns: int | None = None
    run_config: RunConfig | None = None

    def run_turn(self, message: str) -> ChatTurn:
        result = run_chat_turn(
            self.agent,
            message,
            session=self.session,
            config=self.run_config,
            max_turns=self.max_turns,
        )
        return chat_turn_from_result(result)
