import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import agents.coding_cli as coding_cli
import pytest

from agents.coding_cli import (
    ApprovalDecision,
    CodingCliConfig,
    build_coding_cli_setup,
    parse_coding_cli_args,
)
from agents.coding_agent import build_coding_agent
from agents.coding_state import CodingRunStateEnvelope, CodingRunStateStore
from agents.contracts import ModelResponse, RunItem, ToolApprovalRequest, ToolCall
from agents.environment import CommandResult
from agents.memory import JsonSession
from agents.models import OpenAIResponsesModel
from agents.result import RunResult
from agents.run_context import CONTEXT_WORKSPACE_MANIFEST_KEY
from agents.run_state import RUN_STATE_SNAPSHOT_SCHEMA_VERSION
from agents.workspace_manifest import WorkspaceManifest


@pytest.fixture(autouse=True)
def fake_openai_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-api-key")


class FakeAgent:
    def __init__(self, result: object | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.calls: list[tuple[str, object | None]] = []

    def run(self, task: str, *, config: object | None = None) -> object:
        self.calls.append((task, config))
        if self.error is not None:
            raise self.error
        return self.result


class FakePendingApprovalModel:
    model = "gpt-fake-coding"

    def get_response(
        self,
        messages: list[object],
        tool_specs: list[object],
        *,
        model_settings: object | None = None,
    ) -> ModelResponse:
        _ = messages, tool_specs, model_settings
        return ModelResponse(
            response_id="resp_123",
            output=[],
            output_text=None,
            tool_calls=[
                ToolCall(
                    tool_name="apply_patch",
                    call_id="call_123",
                    arguments={
                        "patch": (
                            "*** Begin Patch\n"
                            "*** Add File: generated.txt\n"
                            "+hello\n"
                            "*** End Patch\n"
                        ),
                        "dry_run": False,
                    },
                )
            ],
        )


class FakeCliVerificationEnvironment:
    def __init__(self):
        self.commands: list[str] = []

    def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        *,
        timeout_seconds: float | None = None,
        env: object | None = None,
    ) -> CommandResult:
        _ = timeout_seconds, env
        self.commands.append(command)
        if command == "python -m pytest":
            return CommandResult(
                command=command,
                cwd=cwd or ".",
                returncode=1,
                stdout="cli verification failed\n",
            )
        return CommandResult(
            command=command,
            cwd=cwd or ".",
            returncode=0,
            stdout="tool command ok\n",
        )


class CliVerificationModel:
    def __init__(self):
        self.turn_messages: list[list[object]] = []

    def decide(self, messages: list[object], tool_specs: list[object]) -> ToolCall:
        _ = tool_specs
        self.turn_messages.append(messages)
        if len(self.turn_messages) == 1:
            return ToolCall(
                tool_name="run_test_command",
                call_id="call_test",
                arguments={},
            )
        second_turn_text = "\n".join(message.content for message in messages)
        assert "Verification observation" in second_turn_text
        assert "status: failed" in second_turn_text
        assert "command: python -m pytest" in second_turn_text
        assert "cli verification failed" in second_turn_text
        return ToolCall(
            tool_name="final_answer",
            call_id="call_final",
            arguments={"answer": "saw verification failure"},
        )


def _replace_setup_builder(
    monkeypatch: pytest.MonkeyPatch,
    agent: FakeAgent,
) -> dict[str, object]:
    run_config = object()
    captured: dict[str, object] = {"run_config": run_config}

    def fake_build_coding_cli_setup(config: CodingCliConfig) -> object:
        captured["config"] = config
        return SimpleNamespace(agent=agent, run_config=run_config)

    monkeypatch.setattr(coding_cli, "build_coding_cli_setup", fake_build_coding_cli_setup)
    return captured


def _replace_setup_builder_with_manifest(
    monkeypatch: pytest.MonkeyPatch,
    agent: FakeAgent,
    workspace_root: Path,
) -> dict[str, object]:
    manifest = WorkspaceManifest(root=workspace_root)
    run_config = SimpleNamespace(
        context={CONTEXT_WORKSPACE_MANIFEST_KEY: manifest},
        metadata={},
    )
    captured: dict[str, object] = {
        "run_config": run_config,
        "manifest": manifest,
    }

    def fake_build_coding_cli_setup(config: CodingCliConfig) -> object:
        captured["config"] = config
        return SimpleNamespace(
            agent=agent,
            run_config=run_config,
            workspace=manifest.build_workspace(),
        )

    monkeypatch.setattr(coding_cli, "build_coding_cli_setup", fake_build_coding_cli_setup)
    return captured


def _write_resume_state(
    state_path: Path,
    workspace_root: Path,
    *,
    call_id: str = "call_123",
    last_response_id: str | None = None,
    trajectory_jsonl: Path | None = None,
) -> None:
    envelope = {
        "version": 1,
        "task": "resume edit",
        "workspace_root": str(workspace_root),
        "profile_name": "edit-local",
        "model": "gpt-test",
        "workspace_manifest": {
            "default_test_command": "python -m pytest tests/unit",
            "allowed_test_commands": ["python -m pytest tests/unit"],
        },
        "session_json": None,
        "trajectory_jsonl": str(trajectory_jsonl) if trajectory_jsonl is not None else None,
        "state": {
            "schema_version": RUN_STATE_SNAPSHOT_SCHEMA_VERSION,
            "input": "resume edit",
            "last_agent_name": None,
            "last_response_id": last_response_id,
            "current_turn": 1,
            "steps_taken": 0,
            "max_turns": None,
            "max_steps": None,
            "tool_approvals": [
                {
                    "tool_name": "apply_patch",
                    "call_id": call_id,
                    "arguments": {"path": "src/app.py"},
                    "status": "pending",
                    "reason": "edit requires approval",
                    "rejection_message": None,
                },
            ],
            "model_responses": [],
            "new_items": [],
        },
        "pending_approvals": [
            {
                "tool_name": "apply_patch",
                "call_id": call_id,
                "arguments": {"path": "src/app.py"},
                "reason": "edit requires approval",
                "summary": (
                    "tool: apply_patch\n"
                    f"call_id: {call_id}\n"
                    "changed_paths: src/app.py\n"
                    "reason: edit requires approval"
                ),
            },
        ],
    }
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(envelope), encoding="utf-8")


def test_parse_coding_cli_args_returns_default_config() -> None:
    config = parse_coding_cli_args(["--workspace", "repo", "--task", "fix bug"])

    assert config == CodingCliConfig(
        task="fix bug",
        workspace=Path("repo"),
        profile="read-only",
        verify_max_attempts=1,
    )


def test_parse_coding_cli_args_accepts_optional_values() -> None:
    config = parse_coding_cli_args(
        [
            "--workspace",
            ".",
            "--task",
            "run tests",
            "--profile",
            "shell-test",
            "--model",
            "gpt-test",
            "--max-turns",
            "3",
            "--max-steps",
            "5",
            "--session-json",
            "session.json",
            "--state-json",
            ".agent/run-state.json",
            "--trajectory-jsonl",
            ".agent/last.jsonl",
            "--test-command",
            "python -m pytest tests/unit",
            "--allow-test-command",
            "ruff check .",
            "--allow-test-command",
            "python -m pytest tests/unit",
            "--verify-command",
            "python -m pytest",
            "--verify-command",
            "ruff check .",
            "--verify-after-tool",
            "apply_patch",
            "--verify-after-tool",
            "run_shell_command",
            "--verify-max-attempts",
            "2",
            "--verify-output-chars",
            "4000",
        ]
    )

    assert config.task == "run tests"
    assert config.workspace == Path(".")
    assert config.profile == "shell-test"
    assert config.model == "gpt-test"
    assert config.max_turns == 3
    assert config.max_steps == 5
    assert config.session_json == Path("session.json")
    assert config.state_json == Path(".agent/run-state.json")
    assert config.trajectory_jsonl == Path(".agent/last.jsonl")
    assert config.default_test_command == "python -m pytest tests/unit"
    assert config.allowed_test_commands == (
        "ruff check .",
        "python -m pytest tests/unit",
    )
    assert config.verify_commands == ("python -m pytest", "ruff check .")
    assert config.verify_after_tools == ("apply_patch", "run_shell_command")
    assert config.verify_max_attempts == 2
    assert config.verify_output_chars == 4000


def test_parse_coding_cli_args_accepts_resume_approval_without_task() -> None:
    config = parse_coding_cli_args(
        [
            "--resume-state",
            ".agent/run-state.json",
            "--approve",
            "run_shell_command:call_123",
        ]
    )

    assert config.task == ""
    assert config.resume_state_json == Path(".agent/run-state.json")
    assert config.approval_decisions == (
        ApprovalDecision(
            action="approve",
            tool_name="run_shell_command",
            call_id="call_123",
        ),
    )


def test_parse_coding_cli_args_accepts_resume_rejection_reason() -> None:
    config = parse_coding_cli_args(
        [
            "--resume-state",
            ".agent/run-state.json",
            "--reject",
            "apply_patch:call_456",
            "--rejection-reason",
            "Patch writes outside the requested file.",
        ]
    )

    assert config.approval_decisions == (
        ApprovalDecision(
            action="reject",
            tool_name="apply_patch",
            call_id="call_456",
            reason="Patch writes outside the requested file.",
        ),
    )


def test_parse_coding_cli_args_accepts_approve_all_for_resume() -> None:
    config = parse_coding_cli_args(
        [
            "--resume-state",
            ".agent/run-state.json",
            "--approve-all",
        ]
    )

    assert config.approve_all is True
    assert config.approval_decisions == ()


def test_parse_coding_cli_args_requires_task() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(["--workspace", "repo"])


def test_parse_coding_cli_args_rejects_approval_without_resume_state() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(
            [
                "--task",
                "fix bug",
                "--approve",
                "run_shell_command:call_123",
            ]
        )


def test_parse_coding_cli_args_rejects_invalid_approval_ref() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(
            [
                "--resume-state",
                ".agent/run-state.json",
                "--approve",
                "call_123",
            ]
        )


def test_parse_coding_cli_args_rejects_conflicting_approval_decisions() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(
            [
                "--resume-state",
                ".agent/run-state.json",
                "--approve",
                "apply_patch:call_123",
                "--reject",
                "apply_patch:call_123",
            ]
        )


def test_parse_coding_cli_args_rejects_approve_all_with_reject() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(
            [
                "--resume-state",
                ".agent/run-state.json",
                "--approve-all",
                "--reject",
                "apply_patch:call_123",
            ]
        )


def test_config_from_state_envelope_restores_saved_cli_metadata(tmp_path) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    workspace_root = tmp_path / "repo"
    envelope = CodingRunStateEnvelope(
        version=1,
        task="resume edit",
        workspace_root=str(workspace_root),
        profile_name="edit-local",
        model="gpt-test",
        workspace_manifest={
            "default_test_command": "python -m pytest tests/unit",
            "allowed_test_commands": [
                "python -m pytest tests/unit",
                "ruff check .",
            ],
            "env_keys": ["SECRET_ENV"],
        },
        session_json=str(tmp_path / "session.json"),
        trajectory_jsonl=str(tmp_path / ".agent" / "last.jsonl"),
        state={"schema_version": RUN_STATE_SNAPSHOT_SCHEMA_VERSION},
        pending_approvals=(),
        verify_commands=("python -m pytest",),
        verify_after_tools=("apply_patch",),
        verify_max_attempts=2,
        verify_output_chars=4000,
    )

    config = coding_cli._config_from_state_envelope(envelope, state_path)

    assert config == CodingCliConfig(
        task="resume edit",
        workspace=workspace_root,
        profile="edit-local",
        model="gpt-test",
        session_json=tmp_path / "session.json",
        state_json=state_path,
        resume_state_json=state_path,
        trajectory_jsonl=tmp_path / ".agent" / "last.jsonl",
        default_test_command="python -m pytest tests/unit",
        allowed_test_commands=("python -m pytest tests/unit", "ruff check ."),
        verify_commands=("python -m pytest",),
        verify_after_tools=("apply_patch",),
        verify_max_attempts=2,
        verify_output_chars=4000,
    )


def test_run_coding_agent_cli_resume_keeps_verification_cli_config(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    _write_resume_state(state_path, tmp_path)
    agent = FakeAgent(error=AssertionError("fresh run should not execute"))
    captured = _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)

    def fake_resume_agent_loop(
        resumed_agent: object,
        run_state: object,
        run_config: object,
    ) -> RunResult:
        _ = run_state, run_config
        return RunResult(
            final_answer="resumed fixed",
            step_results=[],
            reached_final_answer=True,
            steps_taken=1,
            input="resume edit",
            last_agent=resumed_agent,
        )

    monkeypatch.setattr(
        coding_cli,
        "resume_agent_loop",
        fake_resume_agent_loop,
        raising=False,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--approve",
            "apply_patch:call_123",
            "--verify-command",
            "python -m pytest",
            "--verify-after-tool",
            "apply_patch",
            "--verify-max-attempts",
            "2",
            "--verify-output-chars",
            "4000",
        ]
    )

    output = capsys.readouterr()
    resumed_config = captured["config"]
    assert exit_code == 0
    assert output.err == ""
    assert resumed_config.verify_commands == ("python -m pytest",)
    assert resumed_config.verify_after_tools == ("apply_patch",)
    assert resumed_config.verify_max_attempts == 2
    assert resumed_config.verify_output_chars == 4000


def test_config_from_state_envelope_uses_default_test_command(
    tmp_path,
) -> None:
    state_path = tmp_path / "run-state.json"
    envelope = CodingRunStateEnvelope(
        version=1,
        task="resume readonly",
        workspace_root=str(tmp_path),
        profile_name="read-only",
        model=None,
        workspace_manifest={},
        session_json=None,
        trajectory_jsonl=None,
        state={"schema_version": RUN_STATE_SNAPSHOT_SCHEMA_VERSION},
        pending_approvals=(),
    )

    config = coding_cli._config_from_state_envelope(envelope, state_path)

    assert config.default_test_command == coding_cli.DEFAULT_TEST_COMMAND
    assert config.allowed_test_commands == ()
    assert config.session_json is None
    assert config.trajectory_jsonl is None


def test_parse_coding_cli_args_restricts_profile_choices() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(
            ["--workspace", "repo", "--task", "fix bug", "--profile", "unsafe"]
        )


@pytest.mark.parametrize("option", ["--max-turns", "--max-steps"])
def test_parse_coding_cli_args_rejects_non_positive_limits(option: str) -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(
            ["--workspace", "repo", "--task", "fix bug", option, "0"]
        )


def test_build_coding_cli_setup_builds_readonly_profile(tmp_path) -> None:
    setup = build_coding_cli_setup(
        CodingCliConfig(task="inspect repo", workspace=tmp_path)
    )

    assert setup.workspace.root == tmp_path.resolve()
    assert isinstance(setup.agent.model, OpenAIResponsesModel)
    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "read_workspace_file" in tool_names
    assert "run_shell_command" not in tool_names
    assert "apply_patch" not in tool_names
    assert setup.environment is None


def test_build_coding_cli_setup_resolves_shell_test_profile(tmp_path) -> None:
    setup = build_coding_cli_setup(
        CodingCliConfig(
            task="run tests",
            workspace=tmp_path,
            profile="shell-test",
        )
    )

    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "read_workspace_file" in tool_names
    assert "run_shell_command" in tool_names
    assert "run_test_command" in tool_names
    assert "apply_patch" not in tool_names
    assert setup.environment is not None


def test_build_coding_cli_setup_resolves_edit_local_profile(tmp_path) -> None:
    setup = build_coding_cli_setup(
        CodingCliConfig(
            task="edit file",
            workspace=tmp_path,
            profile="edit-local",
        )
    )

    tool_names = {spec.name for spec in setup.agent.tool_registry.list_specs()}
    assert "run_shell_command" in tool_names
    assert "run_test_command" in tool_names
    assert "apply_patch" in tool_names
    assert setup.environment is not None


def test_build_coding_cli_setup_applies_limits_and_session(tmp_path) -> None:
    session_path = tmp_path / "session.json"
    setup = build_coding_cli_setup(
        CodingCliConfig(
            task="continue task",
            workspace=tmp_path,
            max_turns=3,
            max_steps=5,
            session_json=session_path,
        )
    )

    assert setup.agent.max_steps == 5
    assert setup.run_config.max_turns == 3
    assert setup.run_config.max_steps == 5
    assert isinstance(setup.run_config.session, JsonSession)
    assert setup.run_config.session.path == session_path


def test_build_coding_cli_setup_uses_requested_model_name(tmp_path) -> None:
    setup = build_coding_cli_setup(
        CodingCliConfig(
            task="inspect repo",
            workspace=tmp_path,
            model="gpt-test",
        )
    )

    assert isinstance(setup.agent.model, OpenAIResponsesModel)
    assert setup.agent.model.model == "gpt-test"


def test_build_coding_cli_setup_creates_manifest_from_cli_config(tmp_path) -> None:
    setup = build_coding_cli_setup(
        CodingCliConfig(
            task="run focused tests",
            workspace=tmp_path,
            profile="shell-test",
            default_test_command="python -m pytest tests/unit",
            allowed_test_commands=("ruff check .",),
        )
    )

    manifest = setup.run_config.context[CONTEXT_WORKSPACE_MANIFEST_KEY]

    assert isinstance(manifest, WorkspaceManifest)
    assert manifest.resolved_root() == tmp_path.resolve()
    assert manifest.default_test_command == "python -m pytest tests/unit"
    assert manifest.allowed_test_commands == (
        "python -m pytest tests/unit",
        "ruff check .",
    )
    assert setup.run_config.metadata["workspace_manifest"] == manifest.metadata()


def test_build_coding_cli_setup_attaches_verification_policy(tmp_path) -> None:
    setup = build_coding_cli_setup(
        CodingCliConfig(
            task="fix and verify",
            workspace=tmp_path,
            profile="edit-local",
            default_test_command="python -m pytest tests/unit",
            allowed_test_commands=("ruff check .",),
            verify_commands=("python -m pytest", "ruff check ."),
            verify_after_tools=("apply_patch", "run_shell_command"),
            verify_max_attempts=2,
            verify_output_chars=4000,
        )
    )

    policy = setup.run_config.verification
    manifest = setup.run_config.context[CONTEXT_WORKSPACE_MANIFEST_KEY]
    assert policy is not None
    assert policy.commands == ("python -m pytest", "ruff check .")
    assert policy.auto_after_tools == ("apply_patch", "run_shell_command")
    assert policy.max_attempts == 2
    assert policy.max_output_chars == 4000
    assert manifest.allowed_test_commands == (
        "python -m pytest tests/unit",
        "ruff check .",
        "python -m pytest",
    )


def test_run_coding_agent_cli_prints_final_output_and_returns_zero(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        final_output="fixed",
        has_pending_approvals=False,
    )
    agent = FakeAgent(result=result)
    captured = _replace_setup_builder(monkeypatch, agent)

    exit_code = coding_cli.run_coding_agent_cli(["--task", "fix bug"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out == "fixed\n"
    assert output.err == ""
    assert captured["config"] == CodingCliConfig(task="fix bug")
    assert agent.calls == [("fix bug", captured["run_config"])]


def test_run_coding_agent_cli_prints_verification_summary_after_final_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation = "\n".join(
        [
            "Verification observation",
            "status: failed",
            "command: python -m pytest",
            "returncode: 1",
            "timed_out: false",
            "output:",
            "test failed",
        ]
    )
    result = RunResult(
        final_answer="fixed",
        step_results=[],
        reached_final_answer=True,
        steps_taken=1,
        input="fix bug",
        current_turn=1,
        new_items=(
            RunItem(item_type="final_output", step_number=1, payload="fixed"),
            RunItem(
                item_type="verification_result",
                step_number=1,
                payload=(),
                metadata={"passed": False, "observation": observation},
            ),
        ),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder(monkeypatch, agent)

    exit_code = coding_cli.run_coding_agent_cli(["--task", "fix bug"])

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out.startswith("fixed\nVerification summary:\n")
    assert "attempts: 1" in output.out
    assert "passed: false" in output.out
    assert "skipped: 0" in output.out
    assert "status: failed" in output.out
    assert "command: python -m pytest" in output.out
    assert "returncode: 1" in output.out
    assert "test failed" in output.out
    assert output.err == ""


def test_run_coding_agent_cli_writes_trajectory_after_printing_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    result = RunResult(
        final_answer="fixed",
        step_results=[],
        reached_final_answer=True,
        steps_taken=1,
        input="fix bug",
        current_turn=1,
        new_items=(RunItem(item_type="final_output", step_number=1, payload="fixed"),),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder(monkeypatch, agent)
    trajectory_path = tmp_path / ".agent" / "last.jsonl"

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "fix bug",
            "--workspace",
            str(tmp_path),
            "--trajectory-jsonl",
            str(trajectory_path),
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out == "fixed\n"
    assert output.err == ""
    events = [
        json.loads(line)
        for line in trajectory_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_type"] for event in events] == ["run_started", "final_output"]
    assert events[0]["payload"]["task"] == "fix bug"
    assert events[0]["payload"]["workspace_root"] == str(tmp_path)
    assert events[0]["payload"]["metadata"]["profile"] == "read-only"


def test_run_coding_agent_cli_returns_one_when_trajectory_write_fails_after_print(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    result = RunResult(
        final_answer="fixed",
        step_results=[],
        reached_final_answer=True,
        steps_taken=1,
        new_items=(RunItem(item_type="final_output", step_number=1, payload="fixed"),),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder(monkeypatch, agent)

    def fail_write_trajectory_jsonl(*args: object, **kwargs: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(
        coding_cli,
        "write_trajectory_jsonl",
        fail_write_trajectory_jsonl,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "fix bug",
            "--trajectory-jsonl",
            str(tmp_path / "last.jsonl"),
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == "fixed\n"
    assert output.err == "Error writing trajectory: disk full\n"


def test_run_coding_agent_cli_prints_pending_approval_and_returns_two(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    approval = SimpleNamespace(
        tool_name="apply_patch",
        call_id="call_123",
        arguments={"path": "src/app.py"},
        reason="edit requires approval",
        summary=(
            "tool: apply_patch\n"
            "call_id: call_123\n"
            "changed_paths: src/app.py\n"
            "reason: edit requires approval"
        ),
    )
    result = SimpleNamespace(
        final_output=None,
        has_pending_approvals=True,
        pending_approval_summaries=(approval,),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder(monkeypatch, agent)

    exit_code = coding_cli.run_coding_agent_cli(["--task", "edit file"])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "Pending approvals:" in output.out
    assert "apply_patch" in output.out
    assert "call_123" in output.out
    assert "src/app.py" in output.out
    assert "edit requires approval" in output.out
    assert "tool: apply_patch" in output.out
    assert "changed_paths: src/app.py" in output.out
    assert "State not saved" in output.out
    assert output.err == ""


def test_run_coding_agent_cli_keeps_pending_exit_code_with_verification_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    observation = "\n".join(
        [
            "Verification observation",
            "status: failed",
            "command: python -m pytest",
            "returncode: 1",
        ]
    )
    approval = ToolApprovalRequest(
        tool_name="apply_patch",
        call_id="call_123",
        arguments={"path": "src/app.py"},
        reason="edit requires approval",
    )
    result = RunResult(
        final_answer=None,
        step_results=[],
        reached_final_answer=False,
        steps_taken=1,
        input="edit file",
        current_turn=1,
        new_items=(
            RunItem(
                item_type="tool_approval_required",
                step_number=1,
                payload=approval,
            ),
            RunItem(
                item_type="verification_result",
                step_number=1,
                payload=(),
                metadata={"passed": False, "observation": observation},
            ),
        ),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder(monkeypatch, agent)

    exit_code = coding_cli.run_coding_agent_cli(["--task", "edit file"])

    output = capsys.readouterr()
    assert exit_code == 2
    assert "Pending approvals:" in output.out
    assert "Verification summary:" in output.out
    assert "status: failed" in output.out
    assert "command: python -m pytest" in output.out
    assert "returncode: 1" in output.out
    assert output.err == ""


def test_run_coding_agent_cli_feeds_failed_verification_to_next_turn(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    model = CliVerificationModel()
    environment = FakeCliVerificationEnvironment()

    def fake_model_from_config(config: CodingCliConfig) -> CliVerificationModel:
        _ = config
        return model

    def fake_build_coding_agent(**kwargs: object) -> object:
        return build_coding_agent(**kwargs, environment=environment)

    monkeypatch.setattr(coding_cli, "_model_from_config", fake_model_from_config)
    monkeypatch.setattr(coding_cli, "build_coding_agent", fake_build_coding_agent)

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "fix bug",
            "--workspace",
            str(tmp_path),
            "--profile",
            "shell-test",
            "--test-command",
            "python -m pytest tests/unit",
            "--verify-command",
            "python -m pytest",
            "--verify-after-tool",
            "run_test_command",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out == (
        "saw verification failure\n"
        "Verification summary:\n"
        "attempts: 1\n"
        "passed: false\n"
        "skipped: 0\n"
        "Verification observation\n"
        "status: failed\n"
        "command: python -m pytest\n"
        "returncode: 1\n"
        "timed_out: false\n"
        "output:\n"
        "cli verification failed\n\n"
    )
    assert output.err == ""
    assert environment.commands == ["python -m pytest tests/unit", "python -m pytest"]
    assert len(model.turn_messages) == 2


def test_run_coding_agent_cli_saves_state_for_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    approval = ToolApprovalRequest(
        tool_name="apply_patch",
        call_id="call_123",
        arguments={"path": "src/app.py"},
        reason="edit requires approval",
    )
    result = RunResult(
        final_answer=None,
        step_results=[],
        reached_final_answer=False,
        steps_taken=1,
        input="edit file",
        current_turn=1,
        new_items=(
            RunItem(
                item_type="tool_approval_required",
                step_number=1,
                payload=approval,
            ),
        ),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)
    state_path = tmp_path / ".agent" / "run-state.json"

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "edit file",
            "--workspace",
            str(tmp_path),
            "--profile",
            "edit-local",
            "--model",
            "gpt-test",
            "--state-json",
            str(state_path),
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert "Pending approvals:" in output.out
    assert f"State saved to: {state_path}" in output.out
    assert "Use --resume-state" in output.out
    assert output.err == ""
    assert payload["task"] == "edit file"
    assert payload["workspace_root"] == str(tmp_path.resolve())
    assert payload["profile_name"] == "edit-local"
    assert payload["model"] == "gpt-test"
    assert payload["state"]["schema_version"] == 1
    assert len(payload["pending_approvals"]) == 1
    pending_approval = payload["pending_approvals"][0]
    assert pending_approval["tool_name"] == "apply_patch"
    assert pending_approval["call_id"] == "call_123"
    assert pending_approval["arguments"] == {"path": "src/app.py"}
    assert pending_approval["reason"] == "edit requires approval"
    assert "tool: apply_patch" in pending_approval["summary"]
    assert "call_id: call_123" in pending_approval["summary"]


def test_run_coding_agent_cli_writes_state_saved_trajectory_event(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    approval = ToolApprovalRequest(
        tool_name="apply_patch",
        call_id="call_123",
        arguments={"path": "src/app.py"},
        reason="edit requires approval",
    )
    result = RunResult(
        final_answer=None,
        step_results=[],
        reached_final_answer=False,
        steps_taken=1,
        input="edit file",
        current_turn=1,
        new_items=(
            RunItem(
                item_type="tool_approval_required",
                step_number=1,
                payload=approval,
            ),
            RunItem(
                item_type="run_stopped",
                step_number=1,
                payload="tool_approval_required",
            ),
        ),
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)
    state_path = tmp_path / ".agent" / "run-state.json"
    trajectory_path = tmp_path / ".agent" / "run.jsonl"

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "edit file",
            "--workspace",
            str(tmp_path),
            "--profile",
            "edit-local",
            "--state-json",
            str(state_path),
            "--trajectory-jsonl",
            str(trajectory_path),
        ]
    )

    output = capsys.readouterr()
    events = [
        json.loads(line)
        for line in trajectory_path.read_text(encoding="utf-8").splitlines()
    ]
    assert exit_code == 2
    assert output.err == ""
    assert [event["event_type"] for event in events] == [
        "run_started",
        "approval_required",
        "run_stopped",
        "state_saved",
    ]
    assert events[-1]["payload"] == {
        "state_path": state_path.as_posix(),
        "pending_count": 1,
    }


def test_run_coding_agent_cli_resumes_and_deletes_completed_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    _write_resume_state(state_path, tmp_path)
    agent = FakeAgent(error=AssertionError("fresh run should not execute"))
    captured = _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)
    resume_calls: list[tuple[object, object, object]] = []

    def fake_resume_agent_loop(
        resumed_agent: object,
        run_state: object,
        run_config: object,
    ) -> RunResult:
        resume_calls.append((resumed_agent, run_state, run_config))
        assert run_state.context_wrapper.approval_status_for(
            "apply_patch",
            "call_123",
        ) == "approved"
        return RunResult(
            final_answer="resumed fixed",
            step_results=[],
            reached_final_answer=True,
            steps_taken=1,
            input="resume edit",
            last_agent=resumed_agent,
            current_turn=2,
            context_wrapper=run_state.context_wrapper,
        )

    monkeypatch.setattr(
        coding_cli,
        "resume_agent_loop",
        fake_resume_agent_loop,
        raising=False,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--approve",
            "apply_patch:call_123",
        ]
    )

    output = capsys.readouterr()
    resumed_config = captured["config"]
    assert exit_code == 0
    assert output.out == "resumed fixed\n"
    assert output.err == ""
    assert not state_path.exists()
    assert agent.calls == []
    assert len(resume_calls) == 1
    assert resumed_config.task == "resume edit"
    assert resumed_config.workspace == tmp_path
    assert resumed_config.profile == "edit-local"
    assert resumed_config.model == "gpt-test"
    assert resumed_config.state_json == state_path


def test_run_coding_agent_cli_appends_resume_started_trajectory_event(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    trajectory_path = tmp_path / ".agent" / "run.jsonl"
    trajectory_path.parent.mkdir(parents=True)
    trajectory_path.write_text(
        json.dumps(
            {
                "event_type": "run_started",
                "run_id": "fresh_run",
                "step": None,
                "payload": {"task": "resume edit"},
                "timestamp": "2026-06-20T08:00:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_resume_state(state_path, tmp_path, trajectory_jsonl=trajectory_path)
    agent = FakeAgent(error=AssertionError("fresh run should not execute"))
    _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)

    def fake_resume_agent_loop(
        resumed_agent: object,
        run_state: object,
        run_config: object,
    ) -> RunResult:
        _ = run_state, run_config
        return RunResult(
            final_answer="resumed fixed",
            step_results=[],
            reached_final_answer=True,
            steps_taken=1,
            input="resume edit",
            last_agent=resumed_agent,
        )

    monkeypatch.setattr(
        coding_cli,
        "resume_agent_loop",
        fake_resume_agent_loop,
        raising=False,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--approve",
            "apply_patch:call_123",
        ]
    )

    output = capsys.readouterr()
    events = [
        json.loads(line)
        for line in trajectory_path.read_text(encoding="utf-8").splitlines()
    ]
    assert exit_code == 0
    assert output.err == ""
    assert [event["event_type"] for event in events] == [
        "run_started",
        "resume_started",
        "approval_decision",
        "run_started",
        "final_output",
    ]
    assert events[0]["run_id"] == "fresh_run"
    assert events[1]["payload"] == {
        "state_path": state_path.as_posix(),
        "approvals": 1,
        "rejections": 0,
    }
    assert events[2]["payload"] == {
        "tool_name": "apply_patch",
        "call_id": "call_123",
        "decision": "approved",
        "reason": "approved by user",
    }
    assert (
        events[1]["run_id"]
        == events[2]["run_id"]
        == events[3]["run_id"]
        == events[4]["run_id"]
    )


def test_run_coding_agent_cli_writes_end_to_end_plan06_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    model = FakePendingApprovalModel()
    state_path = tmp_path / ".agent" / "run-state.json"
    trajectory_path = tmp_path / ".agent" / "run.jsonl"

    def fake_build_coding_cli_setup(config: CodingCliConfig) -> object:
        manifest = WorkspaceManifest(root=config.workspace)
        return build_coding_agent(
            model=model,
            manifest=manifest,
            profile=coding_cli._profile_from_name(config.profile, config),
        )

    monkeypatch.setattr(coding_cli, "build_coding_cli_setup", fake_build_coding_cli_setup)

    fresh_exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "edit file",
            "--workspace",
            str(tmp_path),
            "--profile",
            "edit-local",
            "--state-json",
            str(state_path),
            "--trajectory-jsonl",
            str(trajectory_path),
        ]
    )

    fresh_output = capsys.readouterr()
    assert fresh_exit_code == 2
    assert "State saved to:" in fresh_output.out
    assert fresh_output.err == ""
    assert state_path.exists()

    def fake_resume_agent_loop(
        resumed_agent: object,
        run_state: object,
        run_config: object,
    ) -> RunResult:
        _ = run_config
        assert run_state.context_wrapper.approval_status_for(
            "apply_patch",
            "call_123",
        ) == "approved"
        return RunResult(
            final_answer="resumed fixed",
            step_results=["patched"],
            reached_final_answer=True,
            steps_taken=2,
            input="resume edit",
            last_agent=resumed_agent,
            current_turn=2,
            context_wrapper=run_state.context_wrapper,
            new_items=(
                RunItem(
                    item_type="tool_result",
                    step_number=2,
                    payload="patched generated.txt",
                ),
                RunItem(
                    item_type="verification_result",
                    step_number=2,
                    payload=(),
                    metadata={
                        "passed": True,
                        "observation": "Verification observation\nstatus: passed",
                    },
                ),
                RunItem(
                    item_type="final_output",
                    step_number=2,
                    payload="resumed fixed",
                ),
            ),
        )

    monkeypatch.setattr(
        coding_cli,
        "resume_agent_loop",
        fake_resume_agent_loop,
        raising=False,
    )

    resume_exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--approve",
            "apply_patch:call_123",
        ]
    )

    resume_output = capsys.readouterr()
    events = [
        json.loads(line)
        for line in trajectory_path.read_text(encoding="utf-8").splitlines()
    ]
    assert resume_exit_code == 0
    assert resume_output.out.startswith("resumed fixed\nVerification summary:\n")
    assert resume_output.err == ""
    assert not state_path.exists()
    assert [event["event_type"] for event in events] == [
        "run_started",
        "model_response",
        "approval_required",
        "run_stopped",
        "state_saved",
        "resume_started",
        "approval_decision",
        "run_started",
        "tool_result",
        "verification_result",
        "final_output",
    ]
    assert all(isinstance(event, dict) for event in events)
    assert events[4]["payload"]["pending_count"] == 1
    assert events[5]["payload"]["approvals"] == 1
    assert events[6]["payload"]["decision"] == "approved"
    assert events[-1]["payload"]["summary"]["verification_summary"]["passed"] is True


def test_run_coding_agent_cli_writes_end_to_end_reject_trajectory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    class RejectResumeModel:
        model = "gpt-fake-coding"

        def __init__(self) -> None:
            self.tool_outputs: list[tuple[ToolCall, str]] = []

        def get_response(
            self,
            messages: list[object],
            tool_specs: list[object],
            *,
            model_settings: object | None = None,
        ) -> ModelResponse:
            _ = messages, tool_specs, model_settings
            if self.tool_outputs:
                return ModelResponse(
                    response_id="resp_final",
                    output=[],
                    output_text=None,
                    tool_calls=[
                        ToolCall(
                            tool_name="final_answer",
                            call_id="call_final",
                            arguments={"answer": "rejection recorded"},
                        )
                    ],
                )
            return ModelResponse(
                response_id="resp_pending",
                output=[],
                output_text=None,
                tool_calls=[
                    ToolCall(
                        tool_name="apply_patch",
                        call_id="call_123",
                        arguments={
                            "patch": (
                                "*** Begin Patch\n"
                                "*** Add File: generated.txt\n"
                                "+hello\n"
                                "*** End Patch\n"
                            ),
                            "dry_run": False,
                        },
                    )
                ],
            )

        def record_tool_output(self, action: ToolCall, output: str) -> None:
            self.tool_outputs.append((action, output))

    model = RejectResumeModel()
    state_path = tmp_path / ".agent" / "run-state.json"
    trajectory_path = tmp_path / ".agent" / "run.jsonl"

    def fake_build_coding_cli_setup(config: CodingCliConfig) -> object:
        manifest = WorkspaceManifest(root=config.workspace)
        return build_coding_agent(
            model=model,
            manifest=manifest,
            profile=coding_cli._profile_from_name(config.profile, config),
        )

    monkeypatch.setattr(coding_cli, "build_coding_cli_setup", fake_build_coding_cli_setup)

    fresh_exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "edit file",
            "--workspace",
            str(tmp_path),
            "--profile",
            "edit-local",
            "--state-json",
            str(state_path),
            "--trajectory-jsonl",
            str(trajectory_path),
        ]
    )
    fresh_output = capsys.readouterr()

    resume_exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--reject",
            "apply_patch:call_123",
            "--rejection-reason",
            "not allowed",
        ]
    )
    resume_output = capsys.readouterr()
    events = [
        json.loads(line)
        for line in trajectory_path.read_text(encoding="utf-8").splitlines()
    ]

    assert fresh_exit_code == 2
    assert "State saved to:" in fresh_output.out
    assert resume_exit_code == 0
    assert resume_output.out == "rejection recorded\n"
    assert resume_output.err == ""
    assert not state_path.exists()
    rejected_output = model.tool_outputs[0][1]
    assert model.tool_outputs[0][0].tool_name == "apply_patch"
    assert "tool_approval_rejected" in rejected_output
    assert "not allowed" in rejected_output
    event_types = [event["event_type"] for event in events]
    assert event_types[:7] == [
        "run_started",
        "model_response",
        "approval_required",
        "run_stopped",
        "state_saved",
        "resume_started",
        "approval_decision",
    ]
    assert event_types.index("approval_decision") < event_types.index(
        "approval_rejected"
    )
    assert event_types.index("approval_rejected") < event_types.index("final_output")
    assert events[6]["payload"]["decision"] == "rejected"
    assert events[6]["payload"]["reason"] == "not allowed"


def test_run_coding_agent_cli_resume_without_decision_prints_pending_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    _write_resume_state(state_path, tmp_path)

    def fail_build_coding_cli_setup(config: CodingCliConfig) -> object:
        _ = config
        raise AssertionError("runtime should not start without approval decision")

    monkeypatch.setattr(
        coding_cli,
        "build_coding_cli_setup",
        fail_build_coding_cli_setup,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 2
    assert "Pending approvals:" in output.out
    assert "apply_patch" in output.out
    assert "call_123" in output.out
    assert "changed_paths: src/app.py" in output.out
    assert "edit requires approval" in output.out
    assert output.err == ""
    assert state_path.exists()


def test_run_coding_agent_cli_restores_previous_response_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    _write_resume_state(state_path, tmp_path, last_response_id="resp_saved")
    model = SimpleNamespace(previous_response_id=None)
    agent = SimpleNamespace(model=model)
    _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)
    previous_response_ids: list[str | None] = []

    def fake_resume_agent_loop(
        resumed_agent: object,
        run_state: object,
        run_config: object,
    ) -> RunResult:
        _ = run_state, run_config
        previous_response_ids.append(resumed_agent.model.previous_response_id)
        return RunResult(
            final_answer="resumed fixed",
            step_results=[],
            reached_final_answer=True,
            steps_taken=1,
            input="resume edit",
            last_agent=resumed_agent,
        )

    monkeypatch.setattr(
        coding_cli,
        "resume_agent_loop",
        fake_resume_agent_loop,
        raising=False,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--approve",
            "apply_patch:call_123",
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.err == ""
    assert previous_response_ids == ["resp_saved"]


def test_run_coding_agent_cli_resumes_and_rewrites_pending_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    state_path = tmp_path / ".agent" / "run-state.json"
    _write_resume_state(state_path, tmp_path)
    agent = FakeAgent(error=AssertionError("fresh run should not execute"))
    _replace_setup_builder_with_manifest(monkeypatch, agent, tmp_path)

    def fake_resume_agent_loop(
        resumed_agent: object,
        run_state: object,
        run_config: object,
    ) -> RunResult:
        _ = run_config
        return RunResult(
            final_answer=None,
            step_results=[],
            reached_final_answer=False,
            steps_taken=2,
            input="resume edit",
            last_agent=resumed_agent,
            current_turn=2,
            context_wrapper=run_state.context_wrapper,
            new_items=(
                RunItem(
                    item_type="tool_approval_required",
                    step_number=2,
                    payload=ToolApprovalRequest(
                        tool_name="run_shell_command",
                        call_id="call_next",
                        arguments={"command": "python -m pytest tests/unit"},
                        reason="shell command needs approval",
                    ),
                ),
            ),
        )

    monkeypatch.setattr(
        coding_cli,
        "resume_agent_loop",
        fake_resume_agent_loop,
        raising=False,
    )

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--resume-state",
            str(state_path),
            "--approve",
            "apply_patch:call_123",
        ]
    )

    output = capsys.readouterr()
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert "Pending approvals:" in output.out
    assert f"State saved to: {state_path}" in output.out
    assert "Use --resume-state" in output.out
    assert output.err == ""
    assert agent.calls == []
    assert payload["task"] == "resume edit"
    assert len(payload["pending_approvals"]) == 1
    pending_approval = payload["pending_approvals"][0]
    assert pending_approval["tool_name"] == "run_shell_command"
    assert pending_approval["call_id"] == "call_next"
    assert pending_approval["arguments"] == {"command": "python -m pytest tests/unit"}
    assert pending_approval["reason"] == "shell command needs approval"
    assert "tool: run_shell_command" in pending_approval["summary"]
    assert "command: python -m pytest tests/unit" in pending_approval["summary"]


def test_run_coding_agent_cli_writes_state_contract_from_fake_model(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    model = FakePendingApprovalModel()
    state_path = tmp_path / ".agent" / "run-state.json"

    def fake_build_coding_cli_setup(config: CodingCliConfig) -> object:
        manifest = WorkspaceManifest(
            root=config.workspace,
            env={"SECRET_ENV": "super-secret-value"},
        )
        return build_coding_agent(
            model=model,
            manifest=manifest,
            profile=coding_cli._profile_from_name(config.profile, config),
        )

    monkeypatch.setattr(coding_cli, "build_coding_cli_setup", fake_build_coding_cli_setup)

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "edit file",
            "--workspace",
            str(tmp_path),
            "--profile",
            "edit-local",
            "--state-json",
            str(state_path),
        ]
    )

    output = capsys.readouterr()
    raw_text = state_path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    loaded = CodingRunStateStore(state_path).load_envelope()

    assert exit_code == 2
    assert "State saved to:" in output.out
    assert "Use --resume-state" in output.out
    assert output.err == ""
    assert payload["task"] == "edit file"
    assert payload["workspace_root"] == str(tmp_path.resolve())
    assert payload["profile_name"] == "edit-local"
    assert payload["model"] == "gpt-fake-coding"
    assert payload["workspace_manifest"]["root"] == str(tmp_path.resolve())
    assert payload["workspace_manifest"]["env_keys"] == ["SECRET_ENV"]
    assert payload["state"]["schema_version"] == RUN_STATE_SNAPSHOT_SCHEMA_VERSION
    assert payload["pending_approvals"][0]["tool_name"] == "apply_patch"
    assert payload["pending_approvals"][0]["call_id"] == "call_123"
    assert payload["pending_approvals"][0]["arguments"]["dry_run"] is False
    assert "reason" in payload["pending_approvals"][0]
    assert "summary" in payload["pending_approvals"][0]
    assert "changed_paths: generated.txt" in payload["pending_approvals"][0]["summary"]
    assert "super-secret-value" not in raw_text
    assert "test-api-key" not in raw_text
    assert "SimpleNamespace" not in raw_text
    assert loaded.state == payload["state"]


def test_run_coding_agent_cli_does_not_save_state_without_pending_approval(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    result = SimpleNamespace(
        final_output="fixed",
        has_pending_approvals=False,
    )
    agent = FakeAgent(result=result)
    _replace_setup_builder(monkeypatch, agent)
    state_path = tmp_path / ".agent" / "run-state.json"

    exit_code = coding_cli.run_coding_agent_cli(
        [
            "--task",
            "fix bug",
            "--state-json",
            str(state_path),
        ]
    )

    output = capsys.readouterr()
    assert exit_code == 0
    assert output.out == "fixed\n"
    assert output.err == ""
    assert not state_path.exists()


def test_run_coding_agent_cli_prints_exception_and_returns_one(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    agent = FakeAgent(error=RuntimeError("model unavailable"))
    _replace_setup_builder(monkeypatch, agent)

    exit_code = coding_cli.run_coding_agent_cli(["--task", "fix bug"])

    output = capsys.readouterr()
    assert exit_code == 1
    assert output.out == ""
    assert output.err == "Error: model unavailable\n"


def test_main_delegates_to_run_coding_agent_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    def fake_run_coding_agent_cli(argv: object) -> int:
        calls.append(argv)
        return 7

    monkeypatch.setattr(coding_cli, "run_coding_agent_cli", fake_run_coding_agent_cli)

    argv = ["--task", "fix bug"]
    assert coding_cli.main(argv) == 7
    assert calls == [argv]


def test_coding_cli_can_run_as_python_module_for_help() -> None:
    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(project_root / "src")
    if existing_pythonpath:
        env["PYTHONPATH"] = f"{env['PYTHONPATH']}{os.pathsep}{existing_pythonpath}"

    completed = subprocess.run(
        [sys.executable, "-m", "agents.coding_cli", "--help"],
        cwd=project_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )

    assert completed.returncode == 0
    assert "agents.coding_cli" in completed.stdout
    assert "--task" in completed.stdout
    assert completed.stderr == ""


def test_local_coding_cli_example_builds_module_command() -> None:
    project_root = Path(__file__).resolve().parents[1]
    example_path = project_root / "examples" / "local_coding_cli.py"

    spec = importlib.util.spec_from_file_location("local_coding_cli", example_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    command = module.build_example_command(
        workspace=".",
        task="inspect repo",
        profile="read-only",
        model="gpt-test",
    )

    assert command == [
        sys.executable,
        "-m",
        "agents.coding_cli",
        "--workspace",
        ".",
        "--task",
        "inspect repo",
        "--profile",
        "read-only",
        "--model",
        "gpt-test",
    ]
