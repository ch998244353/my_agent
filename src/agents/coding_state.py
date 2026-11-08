from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .run_context import CONTEXT_WORKSPACE_MANIFEST_KEY
from .workspace_manifest import WorkspaceManifest


CODING_RUN_STATE_ENVELOPE_VERSION = 1
PENDING_APPROVAL_REQUIRED_FIELDS = ("tool_name", "call_id", "arguments", "reason")


# 把一次 pending run 需要恢复的信息集中起来
@dataclass(frozen=True)
class CodingRunStateEnvelope:
    version: int
    task: str
    workspace_root: str
    profile_name: str
    model: str | None
    workspace_manifest: dict[str, Any]
    session_json: str | None
    trajectory_jsonl: str | None
    state: dict[str, Any]
    pending_approvals: tuple[dict[str, Any], ...]
    verify_commands: tuple[str, ...] = ()
    verify_after_tools: tuple[str, ...] = ()
    verify_max_attempts: int = 1
    verify_output_chars: int | None = None


def _path_to_state(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _pending_approval_to_dict(approval: object) -> dict[str, Any]:
    data = {
        "tool_name": getattr(approval, "tool_name"),
        "call_id": getattr(approval, "call_id"),
        "arguments": dict(getattr(approval, "arguments", {})),
        "reason": getattr(approval, "reason", None),
    }
    summary = getattr(approval, "summary", None)
    if summary:
        data["summary"] = str(summary)
    return data


# 从 CodingAgentSetup.run_config.context 里找 WorkspaceManifest
def _workspace_manifest_from_setup(setup: object) -> WorkspaceManifest | None:
    run_config = getattr(setup, "run_config", None)
    context = getattr(run_config, "context", None)
    if isinstance(context, Mapping):
        manifest = context.get(CONTEXT_WORKSPACE_MANIFEST_KEY)
        if isinstance(manifest, WorkspaceManifest):
            return manifest
    return None


# 把 manifest 转成可保存元数据
def _workspace_manifest_metadata(setup: object) -> dict[str, Any]:
    manifest = _workspace_manifest_from_setup(setup)
    if manifest is None:
        return {}
    return dict(manifest.metadata())


# 解析本次 CLI 实际使用的模型名
def _model_name_from_setup(setup: object, fallback: str | None) -> str | None:
    agent = getattr(setup, "agent", None)
    model = getattr(agent, "model", None)
    return getattr(model, "model", fallback)


def _mapping_field(data: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be a JSON object")
    return dict(value)


def _string_tuple_field(data: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{key} must be a JSON array")
    items: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ValueError(f"{key}[{index}] must be a string")
        items.append(item)
    return tuple(items)


def _positive_int_field(data: Mapping[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _optional_positive_int_field(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer or null")
    return value


def _pending_approval_from_state(value: object, index: int) -> dict[str, Any]:
    label = f"pending_approvals[{index}]"
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    missing = [
        field for field in PENDING_APPROVAL_REQUIRED_FIELDS
        if field not in value
    ]
    if missing:
        fields = ", ".join(missing)
        raise ValueError(f"{label} missing required field(s): {fields}")
    arguments = value["arguments"]
    if not isinstance(arguments, Mapping):
        raise ValueError(f"{label}.arguments must be a JSON object")
    reason = value["reason"]
    data = {
        "tool_name": str(value["tool_name"]),
        "call_id": str(value["call_id"]),
        "arguments": dict(arguments),
        "reason": None if reason is None else str(reason),
    }
    summary = value.get("summary")
    if summary is not None:
        data["summary"] = str(summary)
    return data


# 把 JSON dict 还原成 CodingRunStateEnvelope
def _envelope_from_dict(data: Mapping[str, Any]) -> CodingRunStateEnvelope:
    version = int(data.get("version", 0))
    if version != CODING_RUN_STATE_ENVELOPE_VERSION:
        raise ValueError(f"Unsupported coding run state envelope version: {version!r}")
    pending_approvals = data.get("pending_approvals") or ()
    if not isinstance(pending_approvals, list):
        raise ValueError("pending_approvals must be a JSON array")
    return CodingRunStateEnvelope(
        version=version,
        task=str(data["task"]),
        workspace_root=str(data["workspace_root"]),
        profile_name=str(data["profile_name"]),
        model=data.get("model"),
        workspace_manifest=_mapping_field(data, "workspace_manifest"),
        session_json=data.get("session_json"),
        trajectory_jsonl=data.get("trajectory_jsonl"),
        state=_mapping_field(data, "state"),
        pending_approvals=tuple(
            _pending_approval_from_state(approval, index)
            for index, approval in enumerate(pending_approvals)
        ),
        verify_commands=_string_tuple_field(data, "verify_commands"),
        verify_after_tools=_string_tuple_field(data, "verify_after_tools"),
        verify_max_attempts=_positive_int_field(data, "verify_max_attempts", 1),
        verify_output_chars=_optional_positive_int_field(data, "verify_output_chars"),
    )


# state 文件读写边界,写入保存数据,解码保存数据
class CodingRunStateStore:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def save_pending_result(
        self,
        result: object,
        config: object,
        setup: object,
    ) -> CodingRunStateEnvelope:
        workspace = getattr(setup, "workspace")
        envelope = CodingRunStateEnvelope(
            version=CODING_RUN_STATE_ENVELOPE_VERSION,
            task=str(getattr(config, "task")),
            workspace_root=str(getattr(workspace, "root")),
            profile_name=str(getattr(config, "profile")),
            model=_model_name_from_setup(setup, getattr(config, "model", None)),
            workspace_manifest=_workspace_manifest_metadata(setup),
            session_json=_path_to_state(getattr(config, "session_json", None)),
            trajectory_jsonl=_path_to_state(
                getattr(config, "trajectory_jsonl", None)
            ),
            state=dict(result.to_state()),
            pending_approvals=tuple(
                _pending_approval_to_dict(approval)
                for approval in getattr(result, "pending_approval_summaries", ())
            ),
            verify_commands=tuple(getattr(config, "verify_commands", ())),
            verify_after_tools=tuple(getattr(config, "verify_after_tools", ())),
            verify_max_attempts=int(getattr(config, "verify_max_attempts", 1)),
            verify_output_chars=getattr(config, "verify_output_chars", None),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True) # 创建父目录
        self.path.write_text(
            json.dumps(envelope.__dict__, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return envelope

    def load_envelope(self) -> CodingRunStateEnvelope:
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(data, Mapping):
            raise ValueError("Coding run state envelope must be a JSON object")
        return _envelope_from_dict(data)


__all__ = [
    "CODING_RUN_STATE_ENVELOPE_VERSION",
    "CodingRunStateEnvelope",
    "CodingRunStateStore",
]
