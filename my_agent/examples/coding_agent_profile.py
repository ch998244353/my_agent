from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import CodingAgentProfile, CodingAgentSetup, build_coding_agent


def build_readonly_coding_agent(
    *,
    model: Any,
    workspace: str | Path = ".",
) -> CodingAgentSetup:
    return build_coding_agent(model=model, workspace=workspace)


def build_editable_coding_agent(
    *,
    model: Any,
    workspace: str | Path = ".",
) -> CodingAgentSetup:
    profile = CodingAgentProfile(enable_edit=True)
    return build_coding_agent(model=model, workspace=workspace, profile=profile)


__all__ = [
    "build_readonly_coding_agent",
    "build_editable_coding_agent",
]
