from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from .agent import Agent
from .edit_tools import create_apply_patch_tool
from .environment import Environment, LocalEnvironment
from .memory import AgentMemory
from .run_config import RunConfig
from .run_context import CONTEXT_ENVIRONMENT_KEY, CONTEXT_WORKSPACE_KEY
from .shell_tools import create_shell_command_tool, create_test_command_tool
from .workspace import Workspace
from .workspace_tools import create_readonly_workspace_tools


DEFAULT_CODING_AGENT_INSTRUCTIONS = """\
You are a coding agent working inside a bounded workspace.

Focus on understanding the repository before changing behavior. Prefer
workspace read tools for inspection, keep changes scoped to the task, and do
not assume shell or file editing access unless those capabilities are explicitly
enabled for this run.
"""


class CodingCapability(str, Enum):
    """Stable names for coding-agent capability groups."""

    WORKSPACE_READ = "workspace_read"
    SHELL = "shell"
    TEST = "test"
    EDIT = "edit"


CodingCapabilityInput = CodingCapability | str
CapabilityRegistrar = Callable[..., None]
DEFAULT_ENABLED_CAPABILITIES: tuple[CodingCapability, ...] = (
    CodingCapability.WORKSPACE_READ,
)


@dataclass(frozen=True)
class CapabilityPack:
    """Metadata describing one group of coding-agent tools."""

    capability: CodingCapability
    description: str
    requires_environment: bool = False
    requires_approval: bool = False
    register: CapabilityRegistrar | None = None


@dataclass(frozen=True)
class CodingAgentProfile:
    name: str = "CodingAgent"
    instructions: str = DEFAULT_CODING_AGENT_INSTRUCTIONS
    enabled_capabilities: tuple[CodingCapabilityInput, ...] = DEFAULT_ENABLED_CAPABILITIES
    enable_shell: bool = False
    enable_edit: bool = False
    require_approval_for_shell: bool = True
    require_approval_for_edit: bool = True
    max_turns: int = 12
    max_steps: int = 20

    @classmethod
    def read_only(cls, **overrides: Any) -> "CodingAgentProfile":
        return cls(
            enabled_capabilities=(CodingCapability.WORKSPACE_READ,),
            **overrides,
        )

    @classmethod
    def shell_test(cls, **overrides: Any) -> "CodingAgentProfile":
        return cls(
            enabled_capabilities=(
                CodingCapability.WORKSPACE_READ,
                CodingCapability.SHELL,
                CodingCapability.TEST,
            ),
            **overrides,
        )

    @classmethod
    def edit_local(cls, **overrides: Any) -> "CodingAgentProfile":
        return cls(
            enabled_capabilities=(
                CodingCapability.WORKSPACE_READ,
                CodingCapability.SHELL,
                CodingCapability.TEST,
                CodingCapability.EDIT,
            ),
            **overrides,
        )

    def resolved_capabilities(self) -> tuple[CodingCapability, ...]:
        capabilities: list[CodingCapability] = []
        for capability in self.enabled_capabilities:
            _append_unique_capability(capabilities, _coerce_coding_capability(capability))
        if self.enable_shell:
            _append_unique_capability(capabilities, CodingCapability.SHELL)
            _append_unique_capability(capabilities, CodingCapability.TEST)
        if self.enable_edit:
            _append_unique_capability(capabilities, CodingCapability.EDIT)
        return tuple(capabilities)

    def has_capability(self, capability: CodingCapabilityInput) -> bool:
        return _coerce_coding_capability(capability) in self.resolved_capabilities()

    def capability_names(self) -> tuple[str, ...]:
        return tuple(capability.value for capability in self.resolved_capabilities())

    def capability_summary(self) -> str:
        return f"{self.name} capabilities: {', '.join(self.capability_names())}"


def _coerce_coding_capability(capability: CodingCapabilityInput) -> CodingCapability:
    if isinstance(capability, CodingCapability):
        return capability
    return CodingCapability(capability)


def _append_unique_capability(
    capabilities: list[CodingCapability],
    capability: CodingCapability,
) -> None:
    if capability not in capabilities:
        capabilities.append(capability)


@dataclass(frozen=True)
class CodingAgentSetup:
    agent: Agent
    run_config: RunConfig
    workspace: Workspace
    environment: Environment | None = None


def _register_workspace_read_tools(
    *,
    agent: Agent,
    workspace: Workspace,
    environment: Environment | None,
    profile: CodingAgentProfile,
) -> None:
    _ = environment, profile
    for tool in create_readonly_workspace_tools(workspace):
        agent.tool_registry.register(tool)


def _register_shell_tools(
    *,
    agent: Agent,
    workspace: Workspace,
    environment: Environment | None,
    profile: CodingAgentProfile,
) -> None:
    _ = workspace
    agent.tool_registry.register(
        create_shell_command_tool(
            _require_environment(environment, CodingCapability.SHELL),
            needs_approval=profile.require_approval_for_shell,
        )
    )


def _register_test_tools(
    *,
    agent: Agent,
    workspace: Workspace,
    environment: Environment | None,
    profile: CodingAgentProfile,
) -> None:
    _ = workspace
    agent.tool_registry.register(
        create_test_command_tool(
            _require_environment(environment, CodingCapability.TEST),
            needs_approval=profile.require_approval_for_shell,
        )
    )


def _register_edit_tools(
    *,
    agent: Agent,
    workspace: Workspace,
    environment: Environment | None,
    profile: CodingAgentProfile,
) -> None:
    _ = environment
    agent.tool_registry.register(
        create_apply_patch_tool(
            workspace,
            needs_approval=profile.require_approval_for_edit,
        )
    )


DEFAULT_CAPABILITY_PACKS: tuple[CapabilityPack, ...] = (
    CapabilityPack(
        capability=CodingCapability.WORKSPACE_READ,
        description="Read workspace files without mutating the repository.",
        register=_register_workspace_read_tools,
    ),
    CapabilityPack(
        capability=CodingCapability.SHELL,
        description="Run general shell commands in the configured environment.",
        requires_environment=True,
        requires_approval=True,
        register=_register_shell_tools,
    ),
    CapabilityPack(
        capability=CodingCapability.TEST,
        description="Run allowed test commands in the configured environment.",
        requires_environment=True,
        requires_approval=True,
        register=_register_test_tools,
    ),
    CapabilityPack(
        capability=CodingCapability.EDIT,
        description="Apply patches inside the bounded workspace.",
        requires_approval=True,
        register=_register_edit_tools,
    ),
)


def _context_summary(
    workspace: Workspace,
    environment: Environment | None,
) -> dict[str, str | None]:
    return {
        "workspace_root": str(workspace.root),
        "environment_type": (
            environment.__class__.__name__
            if environment is not None
            else None
        ),
    }


def _capability_pack_for(capability: CodingCapability) -> CapabilityPack:
    for pack in DEFAULT_CAPABILITY_PACKS:
        if pack.capability == capability:
            return pack
    raise ValueError(f"Unknown coding capability: {capability.value}")


def _requires_environment(capabilities: tuple[CodingCapability, ...]) -> bool:
    return any(_capability_pack_for(capability).requires_environment for capability in capabilities)


def _require_environment(
    environment: Environment | None,
    capability: CodingCapability,
) -> Environment:
    if environment is None:
        raise ValueError(f"{capability.value} capability requires an environment")
    return environment


def _register_capability_tools(
    *,
    agent: Agent,
    workspace: Workspace,
    environment: Environment | None,
    profile: CodingAgentProfile,
    capability: CodingCapability,
) -> None:
    pack = _capability_pack_for(capability)
    if pack.register is None:
        raise ValueError(f"{capability.value} capability has no tool registrar")
    pack.register(
        agent=agent,
        workspace=workspace,
        environment=environment,
        profile=profile,
    )


def build_coding_agent(
    *,
    model: Any,
    workspace: Workspace | str | Path = ".",
    profile: CodingAgentProfile | None = None,
    memory: AgentMemory | None = None,
    environment: Environment | None = None,
) -> CodingAgentSetup:
    resolved_profile = profile or CodingAgentProfile()
    resolved_workspace = workspace if isinstance(workspace, Workspace) else Workspace(workspace)
    resolved_memory = memory or AgentMemory()
    resolved_environment = environment
    resolved_capabilities = resolved_profile.resolved_capabilities()
    if _requires_environment(resolved_capabilities) and resolved_environment is None:
        resolved_environment = LocalEnvironment(workspace=resolved_workspace)

    agent = Agent(
        memory=resolved_memory,
        model=model,
        name=resolved_profile.name,
        instructions=resolved_profile.instructions,
        max_steps=resolved_profile.max_steps,
    )
    for capability in resolved_capabilities:
        _register_capability_tools(
            agent=agent,
            workspace=resolved_workspace,
            environment=resolved_environment,
            profile=resolved_profile,
            capability=capability,
        )

    context: dict[str, object] = {CONTEXT_WORKSPACE_KEY: resolved_workspace}
    if resolved_environment is not None:
        context[CONTEXT_ENVIRONMENT_KEY] = resolved_environment

    run_config = RunConfig(
        context=context,
        metadata={"context_summary": _context_summary(resolved_workspace, resolved_environment)},
        max_turns=resolved_profile.max_turns,
        max_steps=resolved_profile.max_steps,
    )

    return CodingAgentSetup(
        agent=agent,
        run_config=run_config,
        workspace=resolved_workspace,
        environment=resolved_environment,
    )


__all__ = [
    "CapabilityPack",
    "CapabilityRegistrar",
    "CodingCapabilityInput",
    "CodingCapability",
    "DEFAULT_CODING_AGENT_INSTRUCTIONS",
    "DEFAULT_ENABLED_CAPABILITIES",
    "DEFAULT_CAPABILITY_PACKS",
    "CodingAgentProfile",
    "CodingAgentSetup",
    "build_coding_agent",
]
