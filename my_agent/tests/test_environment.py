from __future__ import annotations

import sys
from pathlib import Path

import pytest

from agents.environment import CommandResult, Environment, LocalEnvironment
from agents.workspace import Workspace, WorkspacePathError


def _python_command(script: str) -> str:
    return f'"{sys.executable}" -c "{script}"'


def test_command_result_serializes_to_stable_observation() -> None:
    result = CommandResult(
        command="python -m pytest",
        cwd="C:/work/project",
        returncode=1,
        stdout="1 failed",
        stderr="traceback",
    )

    assert result.to_dict() == {
        "command": "python -m pytest",
        "cwd": "C:/work/project",
        "returncode": 1,
        "stdout": "1 failed",
        "stderr": "traceback",
        "combined_output": "1 failed\ntraceback",
        "timed_out": False,
        "succeeded": False,
    }
    assert result.to_observation() == (
        "Tool observation\n"
        "tool: command\n"
        "status: error\n"
        "summary: Command failed with exit code 1.\n"
        "details:\n"
        "  command: python -m pytest\n"
        "  cwd: C:/work/project\n"
        "  returncode: 1\n"
        "  timed_out: false\n"
        "output:\n"
        "stdout:\n"
        "1 failed\n"
        "stderr:\n"
        "traceback"
    )


def test_command_result_marks_timeout() -> None:
    result = CommandResult(
        command="python slow.py",
        cwd=".",
        returncode=None,
        stdout="partial output",
        timed_out=True,
    )

    assert result.succeeded is False
    assert result.to_dict()["timed_out"] is True
    assert "status: error" in result.to_observation()
    assert "summary: Command timed out." in result.to_observation()


def test_local_environment_stores_constructor_defaults(tmp_path) -> None:
    environment = LocalEnvironment(
        cwd=tmp_path,
        env={"PYTHONUTF8": "1"},
        timeout_seconds=12.5,
    )

    assert environment.cwd == tmp_path
    assert environment.env == {"PYTHONUTF8": "1"}
    assert environment.timeout_seconds == 12.5


def test_local_environment_matches_environment_protocol(tmp_path) -> None:
    environment = LocalEnvironment(cwd=tmp_path)

    assert isinstance(environment, Environment)
    assert callable(environment.run)


def test_local_environment_runs_successful_command_with_cwd_and_env(tmp_path) -> None:
    environment = LocalEnvironment(cwd=tmp_path, env={"LOCAL_ENV_TEST": "from-default"})

    result = environment.run(
        _python_command("import os; print(os.getcwd()); print(os.getenv('LOCAL_ENV_TEST'))")
    )

    lines = result.stdout.strip().splitlines()
    assert result.succeeded is True
    assert result.returncode == 0
    assert Path(lines[0]).resolve() == tmp_path.resolve()
    assert lines[1] == "from-default"


def test_local_environment_captures_failed_command_stdout_and_stderr(tmp_path) -> None:
    environment = LocalEnvironment(cwd=tmp_path)

    result = environment.run(
        _python_command("import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)")
    )

    assert result.succeeded is False
    assert result.returncode == 7
    assert result.stdout.strip() == "out"
    assert result.stderr.strip() == "err"


def test_local_environment_returns_timeout_result(tmp_path) -> None:
    environment = LocalEnvironment(cwd=tmp_path, timeout_seconds=0.2)

    result = environment.run(
        _python_command("import time; print('start', flush=True); time.sleep(2)")
    )

    assert result.succeeded is False
    assert result.returncode is None
    assert result.timed_out is True
    assert "start" in result.stdout


def test_local_environment_uses_workspace_root_as_default_cwd(tmp_path) -> None:
    workspace = Workspace(root=tmp_path)
    environment = LocalEnvironment(workspace=workspace)

    result = environment.run(_python_command("import os; print(os.getcwd())"))

    assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()
    assert Path(result.cwd).resolve() == tmp_path.resolve()


def test_local_environment_rejects_cwd_outside_workspace(tmp_path) -> None:
    workspace_root = tmp_path / "repo"
    workspace_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    environment = LocalEnvironment(workspace=Workspace(root=workspace_root))

    with pytest.raises(WorkspacePathError):
        environment.run(_python_command("print('outside')"), cwd=outside)


def test_local_environment_rejects_cwd_outside_allowed_paths(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    blocked = tmp_path / "blocked"
    allowed.mkdir()
    blocked.mkdir()
    workspace = Workspace(root=tmp_path, allowed_paths=("allowed",))
    environment = LocalEnvironment(workspace=workspace)

    with pytest.raises(WorkspacePathError):
        environment.run(_python_command("print('blocked')"), cwd=blocked)
