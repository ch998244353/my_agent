from __future__ import annotations

from typing import TYPE_CHECKING

from .contracts import AgentRunResult
from .run_config import RunConfig
from .run_loop import run_agent_loop

if TYPE_CHECKING:
    from .agent import Agent


class Runner:
    @classmethod
    def run_sync(
        cls,
        agent: Agent,
        task: str,
        config: RunConfig | None = None,
    ) -> AgentRunResult:
        return run_agent_loop(agent, task, config=config)
