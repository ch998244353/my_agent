from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from uuid import uuid4

from .coding_agent import CodingAgentProfile, CodingAgentSetup, build_coding_agent
from .memory import JsonSession
from .models import OpenAIResponsesModel
from .shell_tools import DEFAULT_TEST_COMMAND
from .trajectory import trajectory_events_from_result, write_trajectory_jsonl
from .workspace_manifest import WorkspaceManifest


PROFILE_CHOICES = ("read-only", "shell-test", "edit-local")

# 本地 coding 命令的配置快照,业务是：用户输入一次 coding task 后，把任务文本、工作区、能力模式、模型名、轮数/步骤限制、session 文件路径统一收进一个不可变对象
@dataclass(frozen=True)
class CodingCliConfig:
    task: str
    workspace: Path = Path(".")
    profile: str = "read-only"
    model: str | None = None
    max_turns: int | None = None
    max_steps: int | None = None
    session_json: Path | None = None
    trajectory_jsonl: Path | None = None
    default_test_command: str = DEFAULT_TEST_COMMAND
    allowed_test_commands: tuple[str, ...] = ()


# 处理 --max-turns 0、--max-steps abc 这类业务输入错误，让 CLI 在进入 agent runtime 前就拒绝无效限制
def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


# 声明这个命令支持哪些参数
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.coding_cli")
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        default="read-only",
    )
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=_positive_int)
    parser.add_argument("--max-steps", type=_positive_int)
    parser.add_argument("--session-json", type=Path)
    parser.add_argument("--trajectory-jsonl", type=Path)
    parser.add_argument("--test-command", default=DEFAULT_TEST_COMMAND)
    parser.add_argument("--allow-test-command", action="append", default=[])
    return parser


#  把 argparse 的散乱结果转成稳定配置对象，为 build_coding_cli_setup(config) 直接消费 
def parse_coding_cli_args(argv: Sequence[str] | None = None) -> CodingCliConfig:
    args = _build_parser().parse_args(argv)
    return CodingCliConfig(
        task=args.task,
        workspace=args.workspace,
        profile=args.profile,
        model=args.model,
        max_turns=args.max_turns,
        max_steps=args.max_steps,
        session_json=args.session_json,
        trajectory_jsonl=args.trajectory_jsonl,
        default_test_command=args.test_command,
        allowed_test_commands=tuple(args.allow_test_command),
    )


# 重写可变配置,让用户可以修改agent配置
def _profile_overrides(config: CodingCliConfig) -> dict[str, int]:
    overrides: dict[str, int] = {}
    if config.max_turns is not None:
        overrides["max_turns"] = config.max_turns
    if config.max_steps is not None:
        overrides["max_steps"] = config.max_steps
    return overrides


# 把用户命令里的 --profile shell-test 转成真实能力包：只读、可运行 shell/test、或本地可编辑 
def _profile_from_name(name: str, config: CodingCliConfig) -> CodingAgentProfile:
    overrides = _profile_overrides(config)
    if name == "read-only":
        return CodingAgentProfile.read_only(**overrides)
    if name == "shell-test":
        return CodingAgentProfile.shell_test(**overrides)
    if name == "edit-local":
        return CodingAgentProfile.edit_local(**overrides)
    raise ValueError(f"Unknown coding agent profile: {name}")


def _model_from_config(config: CodingCliConfig) -> OpenAIResponsesModel:
    if config.model is not None:
        return OpenAIResponsesModel(model=config.model)
    return OpenAIResponsesModel()


# 把 CLI 参数真正装配成 coding agent：绑定 workspace
def build_coding_cli_setup(config: CodingCliConfig) -> CodingAgentSetup:
    manifest = WorkspaceManifest(
        root=config.workspace,
        default_test_command=config.default_test_command,
        allowed_test_commands=config.allowed_test_commands,
    )
    setup = build_coding_agent(
        model=_model_from_config(config),
        manifest=manifest,
        profile=_profile_from_name(config.profile, config),
    )
    if config.session_json is None:
        return setup
    return CodingAgentSetup(
        agent=setup.agent,
        run_config=replace(setup.run_config, session=JsonSession(config.session_json)),
        workspace=setup.workspace,
        environment=setup.environment,
    )


# 专门处理 agent 执行后的命令行展示
def _print_result(result: object) -> None:
    if getattr(result, "has_pending_approvals", False):
        print("Pending approvals:")
        for approval in getattr(result, "pending_approval_summaries", ()):
            reason = getattr(approval, "reason", None)
            line = (
                f"- {getattr(approval, 'tool_name', '<unknown>')} "
                f"call_id={getattr(approval, 'call_id', '<unknown>')} "
                f"arguments={getattr(approval, 'arguments', {})}"
            )
            if reason:
                line = f"{line} reason={reason}"
            print(line)
        return

    final_output = getattr(result, "final_output", None)
    if final_output is not None:
        print(final_output)


# 把运行结果转成进程退出码
def _exit_code_for_result(result: object) -> int:
    if getattr(result, "has_pending_approvals", False):
        return 2
    if getattr(result, "final_output", None) is not None:
        return 0
    return 1


# 接收 CLI 配置和 agent 运行结果，返回 None。它处理的业务是：如果用户指定轨迹路径，就把本次任务、workspace、profile 和运行结果写成 JSONL；没指定则不做任何事
def _write_trajectory_from_result(config: CodingCliConfig, result: object) -> None:
    if config.trajectory_jsonl is None:
        return
    events = trajectory_events_from_result(
        result,
        run_id=f"coding_cli_{uuid4().hex}",
        task=config.task,
        workspace_root=str(config.workspace),
        metadata={"profile": config.profile},
    )
    write_trajectory_jsonl(config.trajectory_jsonl, events)


# 先解析命令参数，再复用setup builder，然后调用现有 Agent.run() 执行一次任务
def run_coding_agent_cli(argv: Sequence[str] | None = None) -> int:
    try:
        config = parse_coding_cli_args(argv)
        setup = build_coding_cli_setup(config)
        result = setup.agent.run(config.task, config=setup.run_config)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    _print_result(result)
    try:
        _write_trajectory_from_result(config, result)
    except Exception as exc:
        print(f"Error writing trajectory: {exc}", file=sys.stderr)
        return 1
    return _exit_code_for_result(result)


def main(argv: Sequence[str] | None = None) -> int:
    return run_coding_agent_cli(argv)


__all__ = [
    "build_coding_cli_setup",
    "CodingCliConfig",
    "main",
    "parse_coding_cli_args",
    "run_coding_agent_cli",
]


if __name__ == "__main__":
    raise SystemExit(main())
