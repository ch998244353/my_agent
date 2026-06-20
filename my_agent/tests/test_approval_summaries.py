from __future__ import annotations

import importlib

import pytest


def _summary_for_tool_call():
    try:
        module = importlib.import_module("agents.approval_summaries")
    except ModuleNotFoundError:
        pytest.fail("agents.approval_summaries module is missing")
    return module.approval_summary_for_tool_call


def test_shell_command_approval_summary_includes_command_context() -> None:
    summary = _summary_for_tool_call()(
        "run_shell_command",
        "call_shell_1",
        {
            "command": "pip install requests",
            "cwd": "src",
            "env": {"OPENAI_API_KEY": "secret-value"},
        },
        "Command matches approval-required shell prefix.",
    )

    assert "tool: run_shell_command" in summary
    assert "call_id: call_shell_1" in summary
    assert "command: pip install requests" in summary
    assert "cwd: src" in summary
    assert "risk: shell command requires approval before execution" in summary
    assert "reason: Command matches approval-required shell prefix." in summary
    assert "OPENAI_API_KEY" not in summary
    assert "secret-value" not in summary


def test_test_command_approval_summary_includes_allowed_command_context() -> None:
    summary = _summary_for_tool_call()(
        "run_test_command",
        "call_test_1",
        {
            "command": "python -m pytest tests/unit",
            "cwd": ".",
        },
        None,
    )

    assert "tool: run_test_command" in summary
    assert "call_id: call_test_1" in summary
    assert "command: python -m pytest tests/unit" in summary
    assert "cwd: ." in summary
    assert "risk: test command requires approval before execution" in summary
    assert "reason: <none>" in summary


def test_patch_approval_summary_lists_dry_run_and_changed_paths() -> None:
    summary = _summary_for_tool_call()(
        "apply_patch",
        "call_patch_1",
        {
            "patch": (
                "*** Begin Patch\n"
                "*** Add File: notes.txt\n"
                "+hello\n"
                "*** Update File: src/app.py\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** Delete File: old.txt\n"
                "*** End Patch"
            ),
            "dry_run": False,
        },
        "Actual patch writes require approval.",
    )

    assert "tool: apply_patch" in summary
    assert "call_id: call_patch_1" in summary
    assert "dry_run: false" in summary
    assert "changed_paths: notes.txt, src/app.py, old.txt" in summary
    assert "operations: add:notes.txt, update:src/app.py, delete:old.txt" in summary
    assert "risk: patch may write workspace files" in summary
    assert "reason: Actual patch writes require approval." in summary


def test_invalid_patch_approval_summary_reports_parse_failure() -> None:
    summary = _summary_for_tool_call()(
        "apply_patch",
        "call_patch_bad",
        {
            "patch_text": (
                "*** Begin Patch\n"
                "not a patch operation\n"
                "*** End Patch"
            ),
            "dry_run": False,
        },
        None,
    )

    assert "tool: apply_patch" in summary
    assert "call_id: call_patch_bad" in summary
    assert "dry_run: false" in summary
    assert "changed_paths: patch parse failed before approval summary" in summary
    assert "risk: patch may write workspace files" in summary
    assert "reason: <none>" in summary
