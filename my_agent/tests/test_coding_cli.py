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
    CodingCliConfig,
    build_coding_cli_setup,
    parse_coding_cli_args,
)
from agents.contracts import RunItem
from agents.memory import JsonSession
from agents.models import OpenAIResponsesModel
from agents.result import RunResult
from agents.run_context import CONTEXT_WORKSPACE_MANIFEST_KEY
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


def test_parse_coding_cli_args_returns_default_config() -> None:
    config = parse_coding_cli_args(["--workspace", "repo", "--task", "fix bug"])

    assert config == CodingCliConfig(
        task="fix bug",
        workspace=Path("repo"),
        profile="read-only",
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
            "--trajectory-jsonl",
            ".agent/last.jsonl",
            "--test-command",
            "python -m pytest tests/unit",
            "--allow-test-command",
            "ruff check .",
            "--allow-test-command",
            "python -m pytest tests/unit",
        ]
    )

    assert config.task == "run tests"
    assert config.workspace == Path(".")
    assert config.profile == "shell-test"
    assert config.model == "gpt-test"
    assert config.max_turns == 3
    assert config.max_steps == 5
    assert config.session_json == Path("session.json")
    assert config.trajectory_jsonl == Path(".agent/last.jsonl")
    assert config.default_test_command == "python -m pytest tests/unit"
    assert config.allowed_test_commands == (
        "ruff check .",
        "python -m pytest tests/unit",
    )


def test_parse_coding_cli_args_requires_task() -> None:
    with pytest.raises(SystemExit):
        parse_coding_cli_args(["--workspace", "repo"])


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
    assert output.err == ""


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
