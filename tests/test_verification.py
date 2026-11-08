import pytest

from agents import (
    CommandResult,
    RunConfig,
    VerificationPolicy,
    VerificationResult,
    VerificationRunner,
)


class RecordingEnvironment:
    def __init__(self, returncodes):
        self.returncodes = list(returncodes)
        self.commands = []

    def run(self, command, cwd=None, *, timeout_seconds=None, env=None):
        self.commands.append(command)
        returncode = self.returncodes.pop(0)
        return CommandResult(
            command=command,
            cwd=cwd or ".",
            returncode=returncode,
            stdout=f"{command} output",
        )


def test_verification_policy_is_disabled_by_default():
    policy = VerificationPolicy()

    assert policy.commands == ()
    assert policy.auto_after_tools == ()
    assert policy.max_attempts == 1
    assert policy.max_output_chars is None
    assert policy.enabled is False
    assert policy.should_run_after_tool("apply_patch") is False


def test_verification_policy_normalizes_commands_and_trigger_tools():
    policy = VerificationPolicy(
        commands=["python -m pytest"],
        auto_after_tools=["apply_patch"],
        max_attempts=2,
        max_output_chars=4000,
    )

    assert policy.commands == ("python -m pytest",)
    assert policy.auto_after_tools == ("apply_patch",)
    assert policy.enabled is True
    assert policy.should_run_after_tool("apply_patch") is True
    assert policy.should_run_after_tool("run_shell_command") is False


def test_verification_policy_rejects_invalid_limits():
    with pytest.raises(ValueError, match="max_attempts"):
        VerificationPolicy(commands=["python -m pytest"], max_attempts=0)

    with pytest.raises(ValueError, match="max_output_chars"):
        VerificationPolicy(commands=["python -m pytest"], max_output_chars=0)


def test_verification_result_from_command_result_uses_combined_output():
    command_result = CommandResult(
        command="python -m pytest",
        cwd=".",
        returncode=1,
        stdout="failed test output",
        stderr="traceback",
    )

    result = VerificationResult.from_command_result(command_result)

    assert result.command == "python -m pytest"
    assert result.returncode == 1
    assert result.passed is False
    assert result.output == "failed test output\ntraceback"
    assert result.timed_out is False


def test_verification_result_formats_observation():
    result = VerificationResult(
        command="python -m pytest",
        returncode=0,
        passed=True,
        output="all passed",
    )

    observation = result.to_observation()

    assert "Verification observation" in observation
    assert "status: passed" in observation
    assert "command: python -m pytest" in observation
    assert "returncode: 0" in observation
    assert "output:\nall passed" in observation


def test_verification_result_clips_long_output():
    result = VerificationResult(
        command="python -m pytest",
        returncode=1,
        passed=False,
        output="abcdef",
    )

    observation = result.to_observation(max_chars=3)

    assert "abc" in observation
    assert "omitted 3 of 6 characters" in observation


def test_verification_runner_runs_policy_commands_in_order():
    environment = RecordingEnvironment([0, 0])
    runner = VerificationRunner(environment)
    policy = VerificationPolicy(commands=["python -m pytest", "python -m ruff check ."])

    results = runner.run(policy)

    assert environment.commands == ["python -m pytest", "python -m ruff check ."]
    assert [result.command for result in results] == [
        "python -m pytest",
        "python -m ruff check .",
    ]
    assert all(result.passed for result in results)


def test_verification_runner_stops_after_first_failure():
    environment = RecordingEnvironment([0, 1, 0])
    runner = VerificationRunner(environment)
    policy = VerificationPolicy(
        commands=[
            "python -m pytest tests/unit",
            "python -m pytest tests/integration",
            "python -m ruff check .",
        ]
    )

    results = runner.run(policy)

    assert environment.commands == [
        "python -m pytest tests/unit",
        "python -m pytest tests/integration",
    ]
    assert [result.passed for result in results] == [True, False]


def test_verification_runner_returns_no_results_when_policy_is_disabled():
    environment = RecordingEnvironment([0])
    runner = VerificationRunner(environment)

    results = runner.run(VerificationPolicy())

    assert results == ()
    assert environment.commands == []


def test_run_config_can_carry_verification_policy():
    policy = VerificationPolicy(
        commands=["python -m pytest"],
        auto_after_tools=["apply_patch"],
    )

    default_config = RunConfig()
    configured = RunConfig(verification=policy)

    assert default_config.verification is None
    assert configured.verification is policy
