from __future__ import annotations

import pytest

from agents.coding_policies import PatchApprovalPolicy, SafetyDecision, ShellCommandPolicy


ADD_FILE_PATCH = """*** Begin Patch
*** Add File: notes.txt
+hello
*** End Patch"""

DELETE_FILE_PATCH = """*** Begin Patch
*** Delete File: old.txt
*** End Patch"""

LARGE_PATCH = """*** Begin Patch
*** Add File: one.txt
+one
*** Add File: two.txt
+two
*** Add File: three.txt
+three
*** Add File: four.txt
+four
*** End Patch"""

INVALID_PATCH = """*** Begin Patch
not a patch operation
*** End Patch"""


@pytest.mark.parametrize(
    ("command", "category"),
    [
        ("python -m pytest", "safe_shell_command"),
        ("  python   -m   pytest   tests  ", "safe_shell_command"),
        ("pytest -q", "safe_shell_command"),
        ("git status --short", "safe_shell_command"),
        ("git diff --stat", "safe_shell_command"),
    ],
)
def test_shell_policy_allows_safe_commands(command: str, category: str) -> None:
    decision = ShellCommandPolicy().classify(command)

    assert decision == SafetyDecision(
        action="allow",
        reason="Command matches safe shell prefix.",
        category=category,
    )
    assert decision.requires_approval is False
    assert decision.blocked is False


@pytest.mark.parametrize(
    "command",
    [
        "pip install requests",
        "python -m pip install requests",
        "npm install",
        "git commit -m lesson",
        "python scripts/custom_task.py",
        "pytestevil",
    ],
)
def test_shell_policy_requires_approval_for_risky_or_unknown_commands(
    command: str,
) -> None:
    decision = ShellCommandPolicy().classify(command)

    assert decision.action == "approve"
    assert decision.requires_approval is True
    assert decision.blocked is False


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "git reset --hard HEAD",
        "git clean -fd",
        "git status && rm -rf /",
        "Remove-Item -Recurse C:\\project",
    ],
)
def test_shell_policy_blocks_explicitly_destructive_commands(command: str) -> None:
    decision = ShellCommandPolicy().classify(command)

    assert decision.action == "block"
    assert decision.requires_approval is True
    assert decision.blocked is True
    assert decision.category == "blocked_shell_command"


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("python -m pytest", False),
        ("pip install requests", True),
        ("git reset --hard HEAD", True),
    ],
)
def test_shell_policy_needs_approval_matches_classification(
    command: str,
    expected: bool,
) -> None:
    decision = ShellCommandPolicy().needs_approval(
        None,
        {"command": command},
        "call-1",
    )

    assert decision is expected


def test_patch_policy_allows_dry_run_patch() -> None:
    decision = PatchApprovalPolicy().classify_patch_text(DELETE_FILE_PATCH, dry_run=True)

    assert decision == SafetyDecision(
        action="allow",
        reason="Dry-run patch validation does not write files.",
        category="dry_run_patch",
    )


def test_patch_policy_requires_approval_for_actual_delete() -> None:
    decision = PatchApprovalPolicy().classify_patch_text(
        DELETE_FILE_PATCH,
        dry_run=False,
    )

    assert decision.action == "approve"
    assert decision.category == "delete_patch"
    assert decision.requires_approval is True


def test_patch_policy_requires_approval_for_large_actual_patch() -> None:
    decision = PatchApprovalPolicy().classify_patch_text(LARGE_PATCH, dry_run=False)

    assert decision.action == "approve"
    assert decision.category == "large_patch"
    assert decision.requires_approval is True


def test_patch_policy_requires_approval_for_small_actual_patch() -> None:
    decision = PatchApprovalPolicy().classify_patch_text(ADD_FILE_PATCH, dry_run=False)

    assert decision == SafetyDecision(
        action="approve",
        reason="Actual patch writes require approval.",
        category="write_patch",
    )


def test_patch_policy_allows_invalid_patch_to_reach_patch_parser() -> None:
    decision = PatchApprovalPolicy().classify_patch_text(INVALID_PATCH, dry_run=False)

    assert decision == SafetyDecision(
        action="allow",
        reason="Invalid patch text should fail inside the patch tool without approval.",
        category="invalid_patch",
    )


@pytest.mark.parametrize(
    ("patch", "dry_run", "expected"),
    [
        (ADD_FILE_PATCH, False, True),
        (DELETE_FILE_PATCH, False, True),
        (DELETE_FILE_PATCH, True, False),
        (LARGE_PATCH, False, True),
        (INVALID_PATCH, False, False),
    ],
)
def test_patch_policy_needs_approval_matches_classification(
    patch: str,
    dry_run: bool,
    expected: bool,
) -> None:
    decision = PatchApprovalPolicy().needs_approval(
        None,
        {"patch": patch, "dry_run": dry_run},
        "call-1",
    )

    assert decision is expected
