import importlib.util
from pathlib import Path

from agents.coding_agent import (
    DEFAULT_CAPABILITY_PACKS,
    DEFAULT_CODING_AGENT_INSTRUCTIONS,
    CapabilityPack,
    CodingCapability,
    CodingAgentSetup,
    CodingAgentProfile,
    build_coding_agent,
)
from agents.agent import Agent
from agents.coding_policies import PatchApprovalPolicy, ShellCommandPolicy
from agents.environment import LocalEnvironment
from agents.environment import CommandResult
from agents.memory import AgentMemory
from agents.run_config import RunConfig
from agents.run_context import (
    CONTEXT_ENVIRONMENT_KEY,
    CONTEXT_SELECTED_FILES_KEY,
    CONTEXT_WORKSPACE_MANIFEST_KEY,
    CONTEXT_WORKSPACE_KEY,
)
from agents.selected_files import SelectedFilesState
from agents.tools import ToolExecutionError
from agents.workspace import Workspace
from agents.workspace_manifest import WorkspaceManifest


class RecordingEnvironment:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def run(
        self,
        command,
        cwd=None,
        *,
        timeout_seconds=None,
        env=None,
    ) -> CommandResult:
        self.commands.append(command)
        return CommandResult(
            command=command,
            cwd=cwd or ".",
            returncode=0,
            stdout=f"ran {command}",
        )


DELETE_PATCH = """*** Begin Patch
*** Delete File: old.txt
*** End Patch"""

ADD_PATCH = """*** Begin Patch
*** Add File: notes.txt
+hello
*** End Patch"""


def test_coding_capabilities_have_stable_names() -> None:
    assert [capability.value for capability in CodingCapability] == [
        "workspace_read",
        "shell",
        "test",
        "edit",
    ]


def test_default_capability_packs_describe_tool_group_boundaries() -> None:
    packs = {pack.capability: pack for pack in DEFAULT_CAPABILITY_PACKS}

    assert set(packs) == set(CodingCapability)
    assert all(isinstance(pack, CapabilityPack) for pack in packs.values())
    assert all(callable(pack.register) for pack in packs.values())
    assert packs[CodingCapability.WORKSPACE_READ].requires_environment is False
    assert packs[CodingCapability.WORKSPACE_READ].requires_approval is False
    assert packs[CodingCapability.SHELL].requires_environment is True
    assert packs[CodingCapability.SHELL].requires_approval is True
    assert packs[CodingCapability.TEST].requires_environment is True
    assert packs[CodingCapability.TEST].requires_approval is True
    assert packs[CodingCapability.EDIT].requires_environment is False
    assert packs[CodingCapability.EDIT].requires_approval is True


def test_capability_pack_registrars_register_one_tool_group_each(tmp_path) -> None:
    workspace = Workspace(tmp_path)
    environment = LocalEnvironment(workspace=workspace)
    profile = CodingAgentProfile()
    packs = {pack.capability: pack for pack in DEFAULT_CAPABILITY_PACKS}

    workspace_agent = Agent(memory=AgentMemory(), model=object())
    packs[CodingCapability.WORKSPACE_READ].register(
        agent=workspace_agent,
        workspace=workspace,
        environment=None,
        profile=profile,
    )
    assert {spec.name for spec in workspace_agent.tool_registry.list_specs()} == {
        "final_answer",
        "find_related_workspace_files",
        "find_workspace_files",
        "list_workspace_files",
        "outline_workspace_file",
        "read_workspace_lines",
        "read_workspace_file",
        "search_workspace_code",
        "search_workspace_text",
    }

    shell_agent = Agent(memory=AgentMemory(), model=object())
    packs[CodingCapability.SHELL].register(
        agent=shell_agent,
        workspace=workspace,
        environment=environment,
        profile=profile,
    )
    assert {spec.name for spec in shell_agent.tool_registry.list_specs()} == {
        "final_answer",
        "run_shell_command",
    }

    test_agent = Agent(memory=AgentMemory(), model=object())
    packs[CodingCapability.TEST].register(
        agent=test_agent,
        workspace=workspace,
        environment=environment,
        profile=profile,
    )
    assert {spec.name for spec in test_agent.tool_registry.list_specs()} == {
        "final_answer",
        "run_test_command",
    }

    edit_agent = Agent(memory=AgentMemory(), model=object())
    packs[CodingCapability.EDIT].register(
        agent=edit_agent,
        workspace=workspace,
        environment=None,
        profile=profile,
    )
    assert {spec.name for spec in edit_agent.tool_registry.list_specs()} == {
        "final_answer",
        "apply_patch",
    }


def test_coding_agent_profile_resolves_enabled_capabilities_with_legacy_flags() -> None:
    profile = CodingAgentProfile(enable_shell=True, enable_edit=True)

    assert profile.resolved_capabilities() == (
        CodingCapability.WORKSPACE_READ,
        CodingCapability.SHELL,
        CodingCapability.TEST,
        CodingCapability.EDIT,
    )
    assert profile.has_capability(CodingCapability.SHELL) is True
    assert profile.has_capability("test") is True


def test_coding_agent_profile_factories_create_common_profiles() -> None:
    read_only = CodingAgentProfile.read_only(name="Reader")
    shell_test = CodingAgentProfile.shell_test(name="ShellTester")
    edit_local = CodingAgentProfile.edit_local(name="LocalEditor")

    assert read_only.name == "Reader"
    assert read_only.resolved_capabilities() == (CodingCapability.WORKSPACE_READ,)
    assert shell_test.name == "ShellTester"
    assert shell_test.resolved_capabilities() == (
        CodingCapability.WORKSPACE_READ,
        CodingCapability.SHELL,
        CodingCapability.TEST,
    )
    assert edit_local.name == "LocalEditor"
    assert edit_local.resolved_capabilities() == (
        CodingCapability.WORKSPACE_READ,
        CodingCapability.SHELL,
        CodingCapability.TEST,
        CodingCapability.EDIT,
    )
    assert shell_test.enable_shell is False
    assert edit_local.enable_edit is False
    assert shell_test.require_approval_for_shell is True
    assert edit_local.require_approval_for_edit is True
    assert isinstance(shell_test.shell_command_policy, ShellCommandPolicy)
    assert isinstance(edit_local.patch_approval_policy, PatchApprovalPolicy)


def test_coding_agent_profile_summarizes_enabled_capabilities() -> None:
    profile = CodingAgentProfile.edit_local(name="LocalEditor")

    assert profile.capability_names() == (
        "workspace_read",
        "shell",
        "test",
        "edit",
    )
    assert profile.capability_summary() == (
        "LocalEditor capabilities: workspace_read, shell, test, edit"
    )


def test_coding_agent_profile_defaults_are_safe() -> None:
    profile = CodingAgentProfile()

    assert profile.name == "CodingAgent"
    assert profile.instructions == DEFAULT_CODING_AGENT_INSTRUCTIONS
    assert profile.enable_shell is False
    assert profile.enable_edit is False
    assert profile.require_approval_for_shell is True
    assert profile.require_approval_for_edit is True
    assert isinstance(profile.shell_command_policy, ShellCommandPolicy)
    assert isinstance(profile.patch_approval_policy, PatchApprovalPolicy)
    assert profile.max_turns == 12
    assert profile.max_steps == 20


def test_coding_agent_profile_can_override_safe_defaults() -> None:
    profile = CodingAgentProfile(
        name="RepoReader",
        instructions="Read the repository before answering.",
        max_turns=4,
        max_steps=7,
    )

    assert profile.name == "RepoReader"
    assert profile.instructions == "Read the repository before answering."
    assert profile.enable_shell is False
    assert profile.enable_edit is False
    assert profile.max_turns == 4
    assert profile.max_steps == 7


def test_coding_agent_setup_keeps_core_objects_together(tmp_path) -> None:
    workspace = Workspace(tmp_path)
    environment = LocalEnvironment(workspace=workspace)
    agent = Agent(memory=AgentMemory(), model=object())
    run_config = RunConfig(context={"workspace": workspace})

    setup = CodingAgentSetup(
        agent=agent,
        run_config=run_config,
        workspace=workspace,
        environment=environment,
    )

    assert setup.agent is agent
    assert setup.run_config is run_config
    assert setup.workspace is workspace
    assert setup.environment is environment


def test_build_coding_agent_creates_default_workspace_memory_and_run_config(tmp_path) -> None:
    model = object()

    setup = build_coding_agent(model=model, workspace=tmp_path)

    assert setup.agent.model is model
    assert isinstance(setup.agent.memory, AgentMemory)
    assert setup.agent.name == "CodingAgent"
    assert setup.agent.instructions == DEFAULT_CODING_AGENT_INSTRUCTIONS
    assert setup.agent.max_steps == 20
    assert setup.workspace.root == tmp_path.resolve()
    selected_files = setup.run_config.context[CONTEXT_SELECTED_FILES_KEY]
    assert isinstance(selected_files, SelectedFilesState)
    assert selected_files.files() == ()
    assert setup.run_config.context == {
        CONTEXT_WORKSPACE_KEY: setup.workspace,
        CONTEXT_SELECTED_FILES_KEY: selected_files,
    }
    assert setup.run_config.metadata == {
        "context_summary": {
            "workspace_root": str(tmp_path.resolve()),
            "environment_type": None,
        }
    }
    assert setup.run_config.max_turns == 12
    assert setup.run_config.max_steps == 20
    assert setup.environment is None


def test_build_coding_agent_accepts_existing_workspace_memory_and_profile(tmp_path) -> None:
    model = object()
    workspace = Workspace(tmp_path)
    memory = AgentMemory()
    profile = CodingAgentProfile(
        name="RepoAssistant",
        instructions="Stay inside the repo.",
        max_turns=3,
        max_steps=6,
    )

    setup = build_coding_agent(
        model=model,
        workspace=workspace,
        profile=profile,
        memory=memory,
    )

    assert setup.agent.memory is memory
    assert setup.agent.model is model
    assert setup.agent.name == "RepoAssistant"
    assert setup.agent.instructions == "Stay inside the repo."
    assert setup.agent.max_steps == 6
    assert setup.workspace is workspace
    selected_files = setup.run_config.context[CONTEXT_SELECTED_FILES_KEY]
    assert isinstance(selected_files, SelectedFilesState)
    assert selected_files.files() == ()
    assert setup.run_config.context == {
        CONTEXT_WORKSPACE_KEY: workspace,
        CONTEXT_SELECTED_FILES_KEY: selected_files,
    }
    assert setup.run_config.max_turns == 3
    assert setup.run_config.max_steps == 6


def test_build_coding_agent_registers_readonly_workspace_tools_by_default(tmp_path) -> None:
    (tmp_path / "notes.txt").write_text("repo note\n", encoding="utf-8")

    setup = build_coding_agent(model=object(), workspace=tmp_path)

    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "list_workspace_files" in tool_names
    assert "read_workspace_file" in tool_names
    assert "search_workspace_text" in tool_names
    assert "run_shell_command" not in tool_names
    assert "apply_patch" not in tool_names

    listed = setup.agent.tool_registry.execute("list_workspace_files", {"path": "."})
    assert "notes.txt" in listed["entries"]


def test_build_coding_agent_registers_shell_tools_when_explicitly_enabled(tmp_path) -> None:
    profile = CodingAgentProfile(enable_shell=True)

    setup = build_coding_agent(model=object(), workspace=tmp_path, profile=profile)

    assert isinstance(setup.environment, LocalEnvironment)
    assert setup.environment.workspace is setup.workspace
    assert setup.environment.cwd == tmp_path.resolve()
    selected_files = setup.run_config.context[CONTEXT_SELECTED_FILES_KEY]
    assert isinstance(selected_files, SelectedFilesState)
    assert selected_files.files() == ()
    assert setup.run_config.context == {
        CONTEXT_WORKSPACE_KEY: setup.workspace,
        CONTEXT_SELECTED_FILES_KEY: selected_files,
        CONTEXT_ENVIRONMENT_KEY: setup.environment,
    }
    assert setup.run_config.metadata == {
        "context_summary": {
            "workspace_root": str(tmp_path.resolve()),
            "environment_type": "LocalEnvironment",
        }
    }

    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "run_shell_command" in tool_names
    assert "run_test_command" in tool_names
    assert "apply_patch" not in tool_names
    shell_tool = setup.agent.tool_registry.get("run_shell_command")
    test_tool = setup.agent.tool_registry.get("run_test_command")
    safe_shell = shell_tool.requires_approval_for(
        None,
        None,
        {"call_id": "safe-shell", "arguments": {"command": "python -m pytest"}},
    )
    risky_shell = shell_tool.requires_approval_for(
        None,
        None,
        {"call_id": "risky-shell", "arguments": {"command": "pip install requests"}},
    )
    test_approval = test_tool.requires_approval_for(
        None,
        None,
        {"call_id": "test-call", "arguments": {}},
    )
    assert safe_shell.requires_approval is False
    assert risky_shell.requires_approval is True
    assert test_approval.requires_approval is False


def test_build_coding_agent_keeps_block_policy_when_shell_approval_is_disabled(
    tmp_path,
) -> None:
    environment = RecordingEnvironment()
    profile = CodingAgentProfile(
        enable_shell=True,
        require_approval_for_shell=False,
    )

    setup = build_coding_agent(
        model=object(),
        workspace=tmp_path,
        profile=profile,
        environment=environment,
    )
    shell_tool = setup.agent.tool_registry.get("run_shell_command")

    risky_approval = shell_tool.requires_approval_for(
        None,
        None,
        {"call_id": "risky-shell", "arguments": {"command": "pip install requests"}},
    )
    assert risky_approval.requires_approval is False

    try:
        shell_tool.execute({"command": "git reset --hard HEAD"})
    except ToolExecutionError as exc:
        assert "blocked_shell_command" in str(exc)
    else:
        raise AssertionError("Expected blocked shell command rejection")
    assert environment.commands == []


def test_build_coding_agent_uses_manifest_workspace_context_metadata_and_env(
    tmp_path,
) -> None:
    manifest = WorkspaceManifest(
        root=tmp_path,
        allowed_paths=("src",),
        ignore_patterns=(".git", ".cache"),
        default_test_command="python -m pytest tests/unit",
        allowed_test_commands=("ruff check .",),
        env={"PYTHONPATH": "src", "SECRET_TOKEN": "hidden"},
    )
    profile = CodingAgentProfile(enable_shell=True)

    setup = build_coding_agent(model=object(), manifest=manifest, profile=profile)

    assert setup.workspace.root == tmp_path.resolve()
    assert setup.workspace.allowed_paths == (tmp_path.resolve() / "src",)
    assert setup.run_config.context[CONTEXT_WORKSPACE_KEY] is setup.workspace
    assert setup.run_config.context[CONTEXT_WORKSPACE_MANIFEST_KEY] is manifest
    assert isinstance(setup.environment, LocalEnvironment)
    assert setup.environment.env == {"PYTHONPATH": "src", "SECRET_TOKEN": "hidden"}
    assert setup.run_config.metadata == {
        "context_summary": {
            "workspace_root": str(tmp_path.resolve()),
            "environment_type": "LocalEnvironment",
        },
        "workspace_manifest": manifest.metadata(),
    }
    assert "hidden" not in repr(setup.run_config.metadata)


def test_build_coding_agent_rejects_workspace_manifest_root_mismatch(tmp_path) -> None:
    manifest = WorkspaceManifest(root=tmp_path)

    try:
        build_coding_agent(
            model=object(),
            workspace=tmp_path / "other",
            manifest=manifest,
        )
    except ValueError as exc:
        assert "workspace root does not match manifest root" in str(exc)
    else:
        raise AssertionError("Expected workspace and manifest root mismatch")


def test_build_coding_agent_uses_manifest_test_command_policy(tmp_path) -> None:
    manifest = WorkspaceManifest(
        root=tmp_path,
        default_test_command="python -m pytest tests/unit",
        allowed_test_commands=("ruff check .",),
    )
    environment = RecordingEnvironment()
    profile = CodingAgentProfile(
        enabled_capabilities=(CodingCapability.WORKSPACE_READ, CodingCapability.TEST),
    )

    setup = build_coding_agent(
        model=object(),
        manifest=manifest,
        profile=profile,
        environment=environment,
    )

    setup.agent.tool_registry.execute("run_test_command", {})
    setup.agent.tool_registry.execute(
        "run_test_command",
        {"command": "ruff check ."},
    )

    assert environment.commands == [
        "python -m pytest tests/unit",
        "ruff check .",
    ]
    try:
        setup.agent.tool_registry.execute(
            "run_test_command",
            {"command": "python -m unittest"},
        )
    except ToolExecutionError as exc:
        assert "not in the test command allowlist" in str(exc)
    else:
        raise AssertionError("Expected test command allowlist rejection")


def test_build_coding_agent_registers_edit_tool_when_explicitly_enabled(tmp_path) -> None:
    profile = CodingAgentProfile(enable_edit=True)

    setup = build_coding_agent(model=object(), workspace=tmp_path, profile=profile)

    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "apply_patch" in tool_names
    assert "run_shell_command" not in tool_names
    assert "run_test_command" not in tool_names
    patch_tool = setup.agent.tool_registry.get("apply_patch")
    dry_run_delete = patch_tool.requires_approval_for(
        None,
        None,
        {"call_id": "dry-run-delete", "arguments": {"patch": DELETE_PATCH, "dry_run": True}},
    )
    actual_delete = patch_tool.requires_approval_for(
        None,
        None,
        {"call_id": "actual-delete", "arguments": {"patch": DELETE_PATCH, "dry_run": False}},
    )
    small_add = patch_tool.requires_approval_for(
        None,
        None,
        {"call_id": "small-add", "arguments": {"patch": ADD_PATCH, "dry_run": False}},
    )
    assert dry_run_delete.requires_approval is False
    assert actual_delete.requires_approval is True
    assert small_add.requires_approval is True
    assert setup.environment is None


def test_build_coding_agent_uses_enabled_capabilities(tmp_path) -> None:
    profile = CodingAgentProfile(
        enabled_capabilities=(
            CodingCapability.WORKSPACE_READ,
            CodingCapability.TEST,
            CodingCapability.EDIT,
        ),
    )

    setup = build_coding_agent(model=object(), workspace=tmp_path, profile=profile)

    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "read_workspace_file" in tool_names
    assert "run_test_command" in tool_names
    assert "apply_patch" in tool_names
    assert "run_shell_command" not in tool_names
    assert isinstance(setup.environment, LocalEnvironment)


def test_build_coding_agent_reuses_supplied_environment_for_shell_tools(tmp_path) -> None:
    workspace = Workspace(tmp_path)
    environment = LocalEnvironment(workspace=workspace)
    profile = CodingAgentProfile(enable_shell=True)

    setup = build_coding_agent(
        model=object(),
        workspace=workspace,
        profile=profile,
        environment=environment,
    )

    assert setup.environment is environment
    assert "run_shell_command" in {
        spec.name for spec in setup.agent.tool_registry.list_specs()
    }


def test_coding_agent_symbols_are_public_exports() -> None:
    import agents
    from agents import (
        CodingAgentProfile as ExportedCodingAgentProfile,
        CodingAgentSetup as ExportedCodingAgentSetup,
        build_coding_agent as exported_build_coding_agent,
    )

    assert ExportedCodingAgentProfile is CodingAgentProfile
    assert ExportedCodingAgentSetup is CodingAgentSetup
    assert exported_build_coding_agent is build_coding_agent
    assert "CodingAgentProfile" in agents.__all__
    assert "CodingAgentSetup" in agents.__all__
    assert "build_coding_agent" in agents.__all__


def test_coding_agent_example_can_build_readonly_and_editable_setups(tmp_path) -> None:
    example_path = (
        Path(__file__).resolve().parents[1] / "examples" / "coding_agent_profile.py"
    )
    spec = importlib.util.spec_from_file_location("coding_agent_profile_example", example_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    readonly_setup = module.build_readonly_coding_agent(
        model=object(),
        workspace=tmp_path,
    )
    editable_setup = module.build_editable_coding_agent(
        model=object(),
        workspace=tmp_path,
    )

    readonly_tools = {spec.name for spec in readonly_setup.agent.tool_registry.list_specs()}
    editable_tools = {spec.name for spec in editable_setup.agent.tool_registry.list_specs()}

    assert "read_workspace_file" in readonly_tools
    assert "apply_patch" not in readonly_tools
    assert "apply_patch" in editable_tools
