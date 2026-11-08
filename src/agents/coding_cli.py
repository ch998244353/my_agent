from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from uuid import uuid4

from .coding_agent import CodingAgentProfile, CodingAgentSetup, build_coding_agent
from .coding_state import CodingRunStateEnvelope, CodingRunStateStore
from .memory import JsonSession
from .models import OpenAIResponsesModel
from .run_context import RunContextWrapper
from .run_loop import resume_agent_loop
from .run_state import RunState
from .shell_tools import DEFAULT_TEST_COMMAND
from .trajectory import (
    approval_decision_event,
    resume_started_event,
    state_saved_event,
    trajectory_events_from_result,
    write_trajectory_jsonl,
)
from .verification import VerificationPolicy
from .workspace_manifest import WorkspaceManifest


PROFILE_CHOICES = ("read-only", "shell-test", "edit-local")
DEFAULT_REJECTION_REASON = "Rejected by user."


@dataclass(frozen=True)
class ApprovalDecision:
    action: Literal["approve", "reject"]
    tool_name: str
    call_id: str
    reason: str | None = None


# 本地 coding 命令的配置快照,业务是：用户输入一次 coding task 后，把任务文本、工作区、能力模式、模型名、轮数/步骤限制、session 文件路径、verification 参数统一收进一个不可变对象
@dataclass(frozen=True)
class CodingCliConfig:
    task: str = ""
    workspace: Path = Path(".")
    profile: str = "read-only"
    model: str | None = None
    max_turns: int | None = None
    max_steps: int | None = None
    session_json: Path | None = None
    state_json: Path | None = None   # state 保存路径
    resume_state_json: Path | None = None
    approval_decisions: tuple[ApprovalDecision, ...] = ()
    approve_all: bool = False
    trajectory_jsonl: Path | None = None
    default_test_command: str = DEFAULT_TEST_COMMAND
    allowed_test_commands: tuple[str, ...] = ()
    verify_commands: tuple[str, ...] = ()
    verify_after_tools: tuple[str, ...] = ()
    verify_max_attempts: int = 1
    verify_output_chars: int | None = None


# 处理 --max-turns 0、--max-steps abc 这类业务输入错误，让 CLI 在进入 agent runtime 前就拒绝无效限制
def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return number


def _parse_approval_ref(value: str) -> tuple[str, str]:
    tool_name, separator, call_id = value.partition(":")
    if separator != ":" or not tool_name or not call_id:
        raise argparse.ArgumentTypeError(
            "must use TOOL_NAME:CALL_ID, for example run_shell_command:call_123"
        )
    return tool_name, call_id


# 把 argparse 解析出的 approve/reject 列表转成统一的 ApprovalDecision
def _approval_decisions_from_args(args: argparse.Namespace) -> tuple[ApprovalDecision, ...]:
    decisions: list[ApprovalDecision] = [
        ApprovalDecision("approve", tool_name, call_id)
        for tool_name, call_id in args.approve
    ]
    decisions.extend(
        ApprovalDecision(
            "reject",
            tool_name,
            call_id,
            args.rejection_reason,
        )
        for tool_name, call_id in args.reject
    )
    return tuple(decisions)

# 负责 CLI 参数之间的业务规则校验。传入 parser 和 args，不返回值；非法时由 argparse 退出。它确保 fresh run 必须有任务
def _validate_coding_cli_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> None:
    has_approval_input = bool(args.approve or args.reject or args.approve_all)
    if args.resume_state is None and args.task is None:
        parser.error("--task is required unless --resume-state is provided")
    if args.resume_state is None and has_approval_input:
        parser.error("--approve, --reject, and --approve-all require --resume-state")
    if args.approve_all and args.reject:
        parser.error("--approve-all cannot be combined with --reject")

    approved_refs = set(args.approve)
    rejected_refs = set(args.reject)
    conflicts = approved_refs & rejected_refs
    if conflicts:
        tool_name, call_id = sorted(conflicts)[0]
        parser.error(f"conflicting approval decisions for {tool_name}:{call_id}")


# 声明这个命令支持哪些参数
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agents.coding_cli")
    parser.add_argument("--workspace", type=Path, default=Path("."))
    parser.add_argument("--task")
    parser.add_argument(
        "--profile",
        choices=PROFILE_CHOICES,
        default="read-only",
    )
    parser.add_argument("--model")
    parser.add_argument("--max-turns", type=_positive_int)
    parser.add_argument("--max-steps", type=_positive_int)
    parser.add_argument("--session-json", type=Path)
    parser.add_argument("--state-json", type=Path)
    parser.add_argument("--resume-state", type=Path)
    parser.add_argument("--approve", type=_parse_approval_ref, action="append", default=[])
    parser.add_argument("--reject", type=_parse_approval_ref, action="append", default=[])
    parser.add_argument("--rejection-reason", default=DEFAULT_REJECTION_REASON)
    parser.add_argument("--approve-all", action="store_true")
    parser.add_argument("--trajectory-jsonl", type=Path)
    parser.add_argument("--test-command", default=DEFAULT_TEST_COMMAND)
    parser.add_argument("--allow-test-command", action="append", default=[])
    parser.add_argument("--verify-command", action="append", default=[])
    parser.add_argument("--verify-after-tool", action="append", default=[])
    parser.add_argument("--verify-max-attempts", type=_positive_int, default=1)
    parser.add_argument("--verify-output-chars", type=_positive_int)
    return parser


#  把 argparse 的散乱结果转成稳定配置对象，为 build_coding_cli_setup(config) 直接消费 
def parse_coding_cli_args(argv: Sequence[str] | None = None) -> CodingCliConfig:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_coding_cli_args(parser, args)
    return CodingCliConfig(
        task=args.task or "",
        workspace=args.workspace,
        profile=args.profile,
        model=args.model,
        max_turns=args.max_turns,
        max_steps=args.max_steps,
        session_json=args.session_json,
        state_json=args.state_json,
        resume_state_json=args.resume_state,
        approval_decisions=_approval_decisions_from_args(args),
        approve_all=args.approve_all,
        trajectory_jsonl=args.trajectory_jsonl,
        default_test_command=args.test_command,
        allowed_test_commands=tuple(args.allow_test_command),
        verify_commands=tuple(args.verify_command),
        verify_after_tools=tuple(args.verify_after_tool),
        verify_max_attempts=args.verify_max_attempts,
        verify_output_chars=args.verify_output_chars,
    )


def _optional_path(value: str | None) -> Path | None:
    return Path(value) if value is not None else None


def _manifest_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"workspace_manifest.{key} must be a string")
    return value


# 读取 manifest 里的字符串列表，传入字段值和字段名，返回 tuple[str, ...]。业务上恢复 allowed_test_commands
def _manifest_string_list(value: object, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"workspace_manifest.{key} must be a JSON array")
    commands: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"workspace_manifest.{key}[{index}] must be a string")
        commands.append(item)
    return tuple(commands)


# 传入 CodingRunStateEnvelope 和当前 state 文件路径，返回新的 CodingCliConfig
def _config_from_state_envelope(
    envelope: CodingRunStateEnvelope,
    state_path: Path,
) -> CodingCliConfig:
    manifest_metadata = envelope.workspace_manifest
    default_test_command = _manifest_string(
        manifest_metadata.get("default_test_command"),
        "default_test_command",
    )
    return CodingCliConfig(
        task=envelope.task,
        workspace=Path(envelope.workspace_root),
        profile=envelope.profile_name,
        model=envelope.model,
        session_json=_optional_path(envelope.session_json),
        state_json=state_path,
        resume_state_json=state_path,
        trajectory_jsonl=_optional_path(envelope.trajectory_jsonl),
        default_test_command=default_test_command or DEFAULT_TEST_COMMAND,
        allowed_test_commands=_manifest_string_list(
            manifest_metadata.get("allowed_test_commands"),
            "allowed_test_commands",
        ),
        verify_commands=envelope.verify_commands,
        verify_after_tools=envelope.verify_after_tools,
        verify_max_attempts=envelope.verify_max_attempts,
        verify_output_chars=envelope.verify_output_chars,
    )

# 它防止用户 approve 一个 state 中不存在的工具调用，后续由 _apply_approval_decisions() 调用
def _pending_approval_refs(
    envelope: CodingRunStateEnvelope,
    run_state: RunState,
) -> set[tuple[str, str]]:
    refs = {
        (approval["tool_name"], approval["call_id"])
        for approval in envelope.pending_approvals
    }
    refs.update(
        (tool_call.tool_name, tool_call.call_id)
        for tool_call in run_state.pending_tool_calls
    )
    return refs


# approve_all 批准全部 pending call；单个 approve 写入 approved；单个 reject 写入 rejected 和拒绝原因。未知 call id 会直接报错，不进入工具恢复执行
def _apply_approval_decisions(
    run_state: RunState,
    envelope: CodingRunStateEnvelope,
    config: CodingCliConfig,
) -> None:
    pending_refs = _pending_approval_refs(envelope, run_state)
    if config.approve_all:
        for tool_name, call_id in sorted(pending_refs):
            run_state.approve_tool_call(tool_name, call_id)

    for decision in config.approval_decisions:
        ref = (decision.tool_name, decision.call_id)
        if ref not in pending_refs:
            raise ValueError(
                f"Unknown pending approval: {decision.tool_name}:{decision.call_id}"
            )
        if decision.action == "approve":
            run_state.approve_tool_call(decision.tool_name, decision.call_id)
        else:
            run_state.reject_tool_call(
                decision.tool_name,
                decision.call_id,
                decision.reason,
            )


# 返回可继续运行的 RunState
def _run_state_from_state_envelope(
    envelope: CodingRunStateEnvelope,
    setup: CodingAgentSetup,
    config: CodingCliConfig,
) -> RunState:
    context_wrapper = RunContextWrapper(
        context=setup.run_config.context,
        metadata=dict(setup.run_config.metadata or {}),
    )
    run_state = RunState.from_snapshot(
        envelope.state,
        agent=setup.agent,
        context_wrapper=context_wrapper,
    )
    _apply_approval_decisions(run_state, envelope, config)
    return run_state


def _restore_previous_response_id(
    setup: CodingAgentSetup,
    envelope: CodingRunStateEnvelope,
) -> None:
    last_response_id = envelope.state.get("last_response_id")
    if not isinstance(last_response_id, str) or not last_response_id:
        return
    model = setup.agent.model
    if hasattr(model, "previous_response_id"):
        model.previous_response_id = last_response_id


def _has_resume_decision(config: CodingCliConfig) -> bool:
    return bool(config.approval_decisions or config.approve_all)


def _print_pending_approvals_from_envelope(
    envelope: CodingRunStateEnvelope,
    state_path: Path,
) -> None:
    print("Pending approvals:")
    for approval in envelope.pending_approvals:
        summary = approval.get("summary")
        if summary:
            print(str(summary))
            continue
        line = (
            f"- {approval['tool_name']} "
            f"call_id={approval['call_id']} "
            f"arguments={approval['arguments']}"
        )
        reason = approval.get("reason")
        if reason:
            line = f"{line} reason={reason}"
        print(line)
    print(f"State saved to: {state_path}")
    print(
        "Use --resume-state PATH with --approve, --reject, or "
        "--approve-all to continue."
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
        allowed_test_commands=(*config.allowed_test_commands, *config.verify_commands),
    )
    setup = build_coding_agent(
        model=_model_from_config(config),
        manifest=manifest,
        profile=_profile_from_name(config.profile, config),
    )
    run_config = setup.run_config
    if config.verify_commands:
        run_config = replace(
            run_config,
            verification=VerificationPolicy(
                commands=config.verify_commands,
                auto_after_tools=config.verify_after_tools,
                max_attempts=config.verify_max_attempts,
                max_output_chars=config.verify_output_chars,
            ),
        )
    if config.session_json is not None:
        run_config = replace(run_config, session=JsonSession(config.session_json))
    if run_config is setup.run_config:
        return setup
    return CodingAgentSetup(
        agent=setup.agent,
        run_config=run_config,
        workspace=setup.workspace,
        environment=setup.environment,
    )


# 接收配置，保存 state 到指定位置config.state_json,返回已写入的 state 路径或 None。
def _save_pending_state_from_result(
    config: CodingCliConfig,
    setup: object,
    result: object,
) -> Path | None:
    if not getattr(result, "has_pending_approvals", False):
        return None
    if config.state_json is None:
        return None
    CodingRunStateStore(config.state_json).save_pending_result(result, config, setup)
    return config.state_json


# 传入恢复配置、setup 和 resume 结果，返回新写入的 state 路径或 None
def _save_or_clear_resumed_state(
    config: CodingCliConfig,
    setup: CodingAgentSetup,
    result: object,
) -> Path | None:
    saved_state_path = _save_pending_state_from_result(config, setup, result)
    if saved_state_path is not None:
        return saved_state_path
    if config.resume_state_json is not None and config.resume_state_json.exists():
        config.resume_state_json.unlink()
    return None


# 专门处理 agent 执行后的命令行展示
def _print_verification_summary(result: object) -> None:
    summary = getattr(result, "verification_summary", None)
    if summary is None:
        return
    print("Verification summary:")
    print(f"attempts: {summary.attempts}")
    print(f"passed: {str(summary.passed).lower()}")
    print(f"skipped: {summary.skipped}")
    if summary.last_observation:
        print(summary.last_observation)


def _print_result(result: object, saved_state_path: Path | None = None) -> None:
    if getattr(result, "has_pending_approvals", False):
        print("Pending approvals:")
        for approval in getattr(result, "pending_approval_summaries", ()):
            summary = getattr(approval, "summary", None)
            if summary:
                print(str(summary))
                continue
            reason = getattr(approval, "reason", None)
            line = (
                f"- {getattr(approval, 'tool_name', '<unknown>')} "
                f"call_id={getattr(approval, 'call_id', '<unknown>')} "
                f"arguments={getattr(approval, 'arguments', {})}"
            )
            if reason:
                line = f"{line} reason={reason}"
            print(line)
        if saved_state_path is None:
            print("State not saved: pass --state-json PATH to persist this pending run.")
        else:
            print(f"State saved to: {saved_state_path}")
            print(
                "Use --resume-state PATH with --approve, --reject, or "
                "--approve-all to continue."
            )
        _print_verification_summary(result)
        return

    final_output = getattr(result, "final_output", None)
    if final_output is not None:
        print(final_output)
    _print_verification_summary(result)


# 把运行结果转成进程退出码
def _exit_code_for_result(result: object) -> int:
    if getattr(result, "has_pending_approvals", False):
        return 2
    if getattr(result, "final_output", None) is not None:
        return 0
    return 1


# 接收 CLI 配置和 agent 运行结果，返回 None。它处理的业务是：如果用户指定轨迹路径，就把本次任务、workspace、profile 和运行结果写成 JSONL；没指定则不做任何事
def _write_trajectory_from_result(
    config: CodingCliConfig,
    result: object,
    saved_state_path: Path | None = None,
    *,
    run_id: str | None = None,
    append: bool = False,
) -> None:
    if config.trajectory_jsonl is None:
        return
    event_run_id = run_id or f"coding_cli_{uuid4().hex}"
    events = list(
        trajectory_events_from_result(
            result,
            run_id=event_run_id,
            task=config.task,
            workspace_root=str(config.workspace),
            metadata={"profile": config.profile},
        )
    )
    if saved_state_path is not None:
        events.append(
            state_saved_event(
                event_run_id,
                saved_state_path,
                len(result.pending_approvals),
            )
        )
    write_trajectory_jsonl(config.trajectory_jsonl, events, append=append)


# ，传入 CLI config 和 state 路径，返回进程退出码。它读取 state envelope、恢复配置、重建 setup、恢复 RunState、调用 resume_agent_loop()，最后统一处理输出、轨迹和 state 生命周期
def _run_resumed_coding_agent_cli(
    config: CodingCliConfig,
    state_path: Path,
) -> int:
    envelope = CodingRunStateStore(state_path).load_envelope()
    if not _has_resume_decision(config):
        _print_pending_approvals_from_envelope(envelope, state_path)
        return 2

    state_config = _config_from_state_envelope(envelope, state_path)
    if config.verify_commands:
        state_config = replace(
            state_config,
            verify_commands=config.verify_commands,
            verify_after_tools=config.verify_after_tools,
            verify_max_attempts=config.verify_max_attempts,
            verify_output_chars=config.verify_output_chars,
        )
    resumed_config = replace(
        state_config,
        approval_decisions=config.approval_decisions,
        approve_all=config.approve_all,
    )
    resume_run_id = f"coding_cli_{uuid4().hex}"
    if resumed_config.trajectory_jsonl is not None:
        approval_count = (
            len(envelope.pending_approvals)
            if resumed_config.approve_all
            else sum(
                decision.action == "approve"
                for decision in resumed_config.approval_decisions
            )
        )
        rejection_count = sum(
            decision.action == "reject"
            for decision in resumed_config.approval_decisions
        )
        write_trajectory_jsonl(
            resumed_config.trajectory_jsonl,
            [
                resume_started_event(
                    resume_run_id,
                    state_path,
                    approval_count,
                    rejection_count,
                )
            ],
            append=True,
        )
    setup = build_coding_cli_setup(resumed_config)
    _restore_previous_response_id(setup, envelope)
    run_state = _run_state_from_state_envelope(envelope, setup, resumed_config)
    if resumed_config.trajectory_jsonl is not None:
        decision_events = []
        if resumed_config.approve_all:
            decision_events.extend(
                approval_decision_event(
                    resume_run_id,
                    approval["tool_name"],
                    approval["call_id"],
                    "approved",
                    "approved by user",
                )
                for approval in envelope.pending_approvals
            )
        else:
            decision_events.extend(
                approval_decision_event(
                    resume_run_id,
                    decision.tool_name,
                    decision.call_id,
                    "approved" if decision.action == "approve" else "rejected",
                    "approved by user"
                    if decision.action == "approve"
                    else decision.reason,
                )
                for decision in resumed_config.approval_decisions
            )
        if decision_events:
            write_trajectory_jsonl(
                resumed_config.trajectory_jsonl,
                decision_events,
                append=True,
            )
    result = resume_agent_loop(setup.agent, run_state, setup.run_config)
    saved_state_path = _save_or_clear_resumed_state(resumed_config, setup, result)
    _print_result(result, saved_state_path)
    _write_trajectory_from_result(
        resumed_config,
        result,
        saved_state_path,
        run_id=resume_run_id,
        append=True,
    )
    return _exit_code_for_result(result)


# 先解析命令参数，再根据 fresh/resume 模式进入对应运行入口
def run_coding_agent_cli(argv: Sequence[str] | None = None) -> int:
    try:
        config = parse_coding_cli_args(argv)
        if config.resume_state_json is not None:
            return _run_resumed_coding_agent_cli(config, config.resume_state_json)
        setup = build_coding_cli_setup(config)
        result = setup.agent.run(config.task, config=setup.run_config)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        saved_state_path = _save_pending_state_from_result(config, setup, result)
    except Exception as exc:
        print(f"Error writing state: {exc}", file=sys.stderr)
        return 1

    _print_result(result, saved_state_path)
    try:
        _write_trajectory_from_result(config, result, saved_state_path)
    except Exception as exc:
        print(f"Error writing trajectory: {exc}", file=sys.stderr)
        return 1
    return _exit_code_for_result(result)


def main(argv: Sequence[str] | None = None) -> int:
    return run_coding_agent_cli(argv)


__all__ = [
    "ApprovalDecision",
    "build_coding_cli_setup",
    "CodingCliConfig",
    "main",
    "parse_coding_cli_args",
    "run_coding_agent_cli",
]


if __name__ == "__main__":
    raise SystemExit(main())
