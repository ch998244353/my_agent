import json
from types import SimpleNamespace

import pytest

from agents.coding_cli import CodingCliConfig
from agents.coding_state import (
    CODING_RUN_STATE_ENVELOPE_VERSION,
    CodingRunStateStore,
)
from agents.contracts import RunItem, ToolApprovalRequest
from agents.result import RunResult
from agents.run_context import CONTEXT_WORKSPACE_MANIFEST_KEY
from agents.run_state import RUN_STATE_SNAPSHOT_SCHEMA_VERSION
from agents.workspace_manifest import WorkspaceManifest


def test_save_pending_result_writes_envelope_and_loads_it(tmp_path) -> None:
    manifest = WorkspaceManifest(
        root=tmp_path,
        default_test_command="python -m pytest tests/unit",
        allowed_test_commands=("ruff check .",),
        env={"SECRET_ENV": "super-secret-value"},
    )
    result = RunResult(
        final_answer=None,
        step_results=[],
        reached_final_answer=False,
        steps_taken=1,
        input="edit file",
        current_turn=1,
        max_turns=4,
        max_steps=5,
        new_items=(
            RunItem(
                "tool_approval_required",
                1,
                ToolApprovalRequest(
                    tool_name="apply_patch",
                    call_id="call_123",
                    arguments={"path": "src/app.py"},
                    reason="edit requires approval",
                ),
            ),
        ),
    )
    setup = SimpleNamespace(
        agent=SimpleNamespace(model=SimpleNamespace(model="gpt-test")),
        run_config=SimpleNamespace(
            context={CONTEXT_WORKSPACE_MANIFEST_KEY: manifest}
        ),
        workspace=manifest.build_workspace(),
    )
    state_path = tmp_path / ".agent" / "run-state.json"
    config = CodingCliConfig(
        task="edit file",
        workspace=tmp_path,
        profile="edit-local",
        session_json=tmp_path / "session.json",
        state_json=state_path,
        trajectory_jsonl=tmp_path / ".agent" / "last.jsonl",
        verify_commands=("python -m pytest",),
        verify_after_tools=("apply_patch",),
        verify_max_attempts=2,
        verify_output_chars=4000,
    )

    store = CodingRunStateStore(state_path)
    store.save_pending_result(result, config, setup)

    raw_text = state_path.read_text(encoding="utf-8")
    payload = json.loads(raw_text)
    loaded = store.load_envelope()

    assert payload["version"] == CODING_RUN_STATE_ENVELOPE_VERSION
    assert payload["task"] == "edit file"
    assert payload["workspace_root"] == str(tmp_path.resolve())
    assert payload["profile_name"] == "edit-local"
    assert payload["model"] == "gpt-test"
    assert payload["session_json"] == str(tmp_path / "session.json")
    assert payload["trajectory_jsonl"] == str(tmp_path / ".agent" / "last.jsonl")
    assert payload["verify_commands"] == ["python -m pytest"]
    assert payload["verify_after_tools"] == ["apply_patch"]
    assert payload["verify_max_attempts"] == 2
    assert payload["verify_output_chars"] == 4000
    assert payload["workspace_manifest"] == manifest.metadata()
    assert payload["state"]["schema_version"] == RUN_STATE_SNAPSHOT_SCHEMA_VERSION
    assert len(payload["pending_approvals"]) == 1
    pending_approval = payload["pending_approvals"][0]
    assert pending_approval["tool_name"] == "apply_patch"
    assert pending_approval["call_id"] == "call_123"
    assert pending_approval["arguments"] == {"path": "src/app.py"}
    assert pending_approval["reason"] == "edit requires approval"
    assert "tool: apply_patch" in pending_approval["summary"]
    assert "call_id: call_123" in pending_approval["summary"]
    assert "super-secret-value" not in raw_text
    assert loaded.version == CODING_RUN_STATE_ENVELOPE_VERSION
    assert loaded.verify_commands == ("python -m pytest",)
    assert loaded.verify_after_tools == ("apply_patch",)
    assert loaded.verify_max_attempts == 2
    assert loaded.verify_output_chars == 4000
    assert loaded.state == payload["state"]
    assert loaded.pending_approvals[0]["summary"] == pending_approval["summary"]


def test_load_envelope_rejects_unknown_version(tmp_path) -> None:
    state_path = tmp_path / "run-state.json"
    state_path.write_text('{"version": 999}', encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported coding run state envelope"):
        CodingRunStateStore(state_path).load_envelope()


def test_load_envelope_rejects_pending_approval_missing_required_fields(tmp_path) -> None:
    state_path = tmp_path / "run-state.json"
    state_path.write_text(
        json.dumps(
            {
                "version": CODING_RUN_STATE_ENVELOPE_VERSION,
                "task": "edit file",
                "workspace_root": str(tmp_path),
                "profile_name": "edit-local",
                "model": "gpt-test",
                "workspace_manifest": {},
                "session_json": None,
                "trajectory_jsonl": None,
                "state": {"schema_version": RUN_STATE_SNAPSHOT_SCHEMA_VERSION},
                "pending_approvals": [
                    {
                        "tool_name": "apply_patch",
                        "arguments": {"path": "src/app.py"},
                        "reason": "edit requires approval",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="pending_approvals\\[0\\].*call_id"):
        CodingRunStateStore(state_path).load_envelope()
