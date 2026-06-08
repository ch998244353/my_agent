from __future__ import annotations

import os
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .tool_runtime import clip_tool_text
from .workspace import Workspace


# 将输出转化成字符串
def _text_or_empty(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        # 将字节转回字符串,编译错误则用 ? 标识
        return output.decode("utf-8", errors="replace")
    return output


@dataclass(frozen=True)
class CommandResult:
    command: str
    cwd: str | Path
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False

    @property
    def combined_output(self) -> str:
        if self.stdout and self.stderr:
            joiner = "" if self.stdout.endswith("\n") else "\n"
            return f"{self.stdout}{joiner}{self.stderr}"
        return self.stdout or self.stderr

    @property
    def succeeded(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "cwd": str(self.cwd),
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "combined_output": self.combined_output,
            "timed_out": self.timed_out,
            "succeeded": self.succeeded,
        }

    def to_observation(self, max_chars: int | None = None) -> str:
        return "\n".join(
            [
                "Command observation",
                f"status: {self._status()}",
                f"command: {self.command}",
                f"cwd: {self.cwd}",
                f"returncode: {self.returncode}",
                f"timed_out: {str(self.timed_out).lower()}",
                "stdout:",
                clip_tool_text(self.stdout, max_chars),
                "stderr:",
                clip_tool_text(self.stderr, max_chars),
            ]
        )

    def _status(self) -> str:
        if self.timed_out:
            return "timeout"
        if self.succeeded:
            return "success"
        return "error"


@runtime_checkable
class Environment(Protocol):
    def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        *,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult:
        ...


@dataclass(frozen=True)
class LocalEnvironment:
    cwd: str | Path = "."
    env: Mapping[str, str] | None = None
    timeout_seconds: float | None = None
    workspace: Workspace | None = None

    def __post_init__(self) -> None:
        cwd = Path(self.cwd)
        if self.workspace is not None:
            cwd = self.workspace.resolve_path(cwd)
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "env", dict(self.env or {}))

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        candidate = self.cwd if cwd is None else Path(cwd)
        if self.workspace is None:
            return Path(candidate)
        return self.workspace.ensure_readable_path(candidate)


    # 执行终端命令
    def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        *,
        timeout_seconds: float | None = None,
        env: Mapping[str, str] | None = None,
    ) -> CommandResult: 
        resolved_cwd = self._resolve_cwd(cwd) # 确定命令在哪个目录执行 
        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds 
        '''
        构造命令运行时的环境变量
        os.environ 是当前 Python 进程的环境变量,
        先复制一份系统环境变量
        把 runner 的环境变量合进去
        环境变量覆盖顺序 : 系统环境变量 < self.env < 本次传入的 env
        '''
        process_env = os.environ.copy() 
        process_env.update(self.env)
        if env is not None:
            process_env.update(env)

        try:
            # subprocess 是 Python 标准库里专门用来启动子进程的模块
            completed = subprocess.run(
                command,
                shell=True,
                text=True ,
                cwd=str(resolved_cwd),
                env=process_env,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            '''
            completed.returncode( 0表示成功 非0表示失败 ) ; completed.stdout ; completed.stderr
            '''
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                command=command,
                cwd=resolved_cwd,
                returncode=None,
                stdout=_text_or_empty(exc.stdout),
                stderr=_text_or_empty(exc.stderr),
                timed_out=True,
            )

        return CommandResult(
            command=command,
            cwd=resolved_cwd,
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
