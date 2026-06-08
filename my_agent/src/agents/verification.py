from __future__ import annotations

from dataclasses import dataclass

from .environment import CommandResult, Environment
from .tool_runtime import clip_tool_text


@dataclass(frozen=True)
class VerificationPolicy:
    commands: tuple[str, ...] = ()
    auto_after_tools: tuple[str, ...] = ()
    max_attempts: int = 1
    max_output_chars: int | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1.")
        if self.max_output_chars is not None and self.max_output_chars < 1:
            raise ValueError("max_output_chars must be at least 1 when set.")
        object.__setattr__(self, "commands", tuple(self.commands))
        object.__setattr__(self, "auto_after_tools", tuple(self.auto_after_tools))

    @property
    def enabled(self) -> bool:
        return bool(self.commands)

    def should_run_after_tool(self, tool_name: str) -> bool:
        return self.enabled and tool_name in self.auto_after_tools


@dataclass(frozen=True)
class VerificationResult:
    command: str
    returncode: int | None
    passed: bool
    output: str = ""
    timed_out: bool = False

    @classmethod
    def from_command_result(cls, result: CommandResult) -> VerificationResult:
        return cls(
            command=result.command,
            returncode=result.returncode,
            passed=result.succeeded,
            output=result.combined_output,
            timed_out=result.timed_out,
        )

    def to_observation(self, max_chars: int | None = None) -> str:
        return "\n".join(
            [
                "Verification observation",
                f"status: {self._status()}",
                f"command: {self.command}",
                f"returncode: {self.returncode}",
                f"timed_out: {str(self.timed_out).lower()}",
                "output:",
                clip_tool_text(self.output, max_chars),
            ]
        )

    def _status(self) -> str:
        if self.timed_out:
            return "timeout"
        if self.passed:
            return "passed"
        return "failed"


@dataclass(frozen=True)
class VerificationRunner:
    environment: Environment

    def run(self, policy: VerificationPolicy) -> tuple[VerificationResult, ...]:
        if not policy.enabled:
            return ()
        results: list[VerificationResult] = []
        for command in policy.commands:
            result = VerificationResult.from_command_result(
                self.environment.run(command)
            )
            results.append(result)
            if not result.passed:
                break
        return tuple(results)
