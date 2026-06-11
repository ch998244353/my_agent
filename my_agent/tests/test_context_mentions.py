from dataclasses import FrozenInstanceError

import pytest

from agents.context_mentions import (
    MentionCandidate,
    detect_file_mentions,
    resolve_mentions_against_inventory,
)
from agents.workspace_inventory import WorkspaceFileEntry, WorkspaceInventory


def test_mention_candidate_serializes_default_fields() -> None:
    candidate = MentionCandidate(text="run_loop.py", kind="filename", confidence=0.9)

    assert candidate.to_dict() == {
        "text": "run_loop.py",
        "kind": "filename",
        "confidence": 0.9,
        "matched_path": None,
        "source": "text",
    }


def test_mention_candidate_is_frozen_and_hashable() -> None:
    candidate = MentionCandidate(text="RunState", kind="symbol", confidence=0.8)
    duplicate = MentionCandidate(text="RunState", kind="symbol", confidence=0.8)

    assert {candidate, duplicate} == {candidate}
    with pytest.raises(FrozenInstanceError):
        candidate.text = "Other"


def test_mention_candidate_normalizes_text_for_later_deduplication() -> None:
    candidate = MentionCandidate(
        text="`src\\agents\\run_loop.py`,",
        kind="path",
        confidence=1.0,
    )

    assert candidate.normalized_text == "src/agents/run_loop.py"


def test_detect_file_mentions_finds_explicit_path() -> None:
    candidates = detect_file_mentions("请修改 src/agents/run_loop.py")

    assert candidates == [
        MentionCandidate(
            text="src/agents/run_loop.py",
            kind="path",
            confidence=1.0,
        )
    ]


def test_detect_file_mentions_strips_backticks_and_normalizes_windows_path() -> None:
    candidates = detect_file_mentions(
        "先看 `src\\agents\\context_chunks.py`, 再看 src\\agents\\run_loop.py"
    )

    assert [candidate.text for candidate in candidates] == [
        "src/agents/context_chunks.py",
        "src/agents/run_loop.py",
    ]
    assert all(candidate.kind == "path" for candidate in candidates)


def test_detect_file_mentions_deduplicates_repeated_paths() -> None:
    candidates = detect_file_mentions(
        "请改 src/agents/run_loop.py，然后再检查 `src/agents/run_loop.py`"
    )

    assert candidates == [
        MentionCandidate(
            text="src/agents/run_loop.py",
            kind="path",
            confidence=1.0,
        )
    ]


def test_detect_file_mentions_finds_bare_filename() -> None:
    candidates = detect_file_mentions("看看 runner.py 和 README.md")

    assert candidates == [
        MentionCandidate(text="runner.py", kind="filename", confidence=0.8),
        MentionCandidate(text="README.md", kind="filename", confidence=0.8),
    ]


def test_detect_file_mentions_marks_test_filenames() -> None:
    candidates = detect_file_mentions("跑一下 test_runner.py 和 tests/test_context.py")

    assert candidates == [
        MentionCandidate(text="tests/test_context.py", kind="test", confidence=1.0),
        MentionCandidate(text="test_runner.py", kind="test", confidence=0.85),
    ]


def test_detect_file_mentions_does_not_duplicate_filename_inside_path() -> None:
    candidates = detect_file_mentions("请修改 src/agents/run_loop.py")

    assert candidates == [
        MentionCandidate(text="src/agents/run_loop.py", kind="path", confidence=1.0)
    ]


def test_detect_file_mentions_finds_symbol_tokens() -> None:
    candidates = detect_file_mentions(
        "看一下 RunState、WorkspaceInventory、build_turn_context 和 run_state"
    )

    assert candidates == [
        MentionCandidate(text="RunState", kind="symbol", confidence=0.7),
        MentionCandidate(text="WorkspaceInventory", kind="symbol", confidence=0.7),
        MentionCandidate(text="build_turn_context", kind="symbol", confidence=0.7),
        MentionCandidate(text="run_state", kind="symbol", confidence=0.7),
    ]


def test_detect_file_mentions_filters_common_words_from_symbols() -> None:
    candidates = detect_file_mentions("please update code and run the test")

    assert candidates == []


def test_detect_file_mentions_does_not_duplicate_symbols_inside_file_mentions() -> None:
    candidates = detect_file_mentions("检查 run_loop.py 和 build_turn_context")

    assert candidates == [
        MentionCandidate(text="run_loop.py", kind="filename", confidence=0.8),
        MentionCandidate(text="build_turn_context", kind="symbol", confidence=0.7),
    ]


def test_detect_file_mentions_finds_common_project_file_extensions() -> None:
    candidates = detect_file_mentions(
        "检查 app.tsx style.css query.sql script.sh Program.cs config.ini package.lock"
    )

    assert candidates == [
        MentionCandidate(text="app.tsx", kind="filename", confidence=0.8),
        MentionCandidate(text="style.css", kind="filename", confidence=0.8),
        MentionCandidate(text="query.sql", kind="filename", confidence=0.8),
        MentionCandidate(text="script.sh", kind="filename", confidence=0.8),
        MentionCandidate(text="Program.cs", kind="filename", confidence=0.8),
        MentionCandidate(text="config.ini", kind="filename", confidence=0.8),
        MentionCandidate(text="package.lock", kind="filename", confidence=0.8),
    ]


def test_resolve_mentions_matches_full_path_against_inventory() -> None:
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="src/agents/run_loop.py", kind="file"),
        ),
    )

    candidates = resolve_mentions_against_inventory(
        "请修改 src/agents/run_loop.py",
        inventory,
    )

    assert candidates == [
        MentionCandidate(
            text="src/agents/run_loop.py",
            kind="path",
            confidence=1.0,
            matched_path="src/agents/run_loop.py",
            source="inventory",
        )
    ]


def test_resolve_mentions_matches_unique_basename_against_inventory() -> None:
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="src/agents/run_loop.py", kind="file"),
            WorkspaceFileEntry(path="src/agents/run_state.py", kind="file"),
        ),
    )

    candidates = resolve_mentions_against_inventory("看看 run_loop.py", inventory)

    assert candidates == [
        MentionCandidate(
            text="run_loop.py",
            kind="filename",
            confidence=0.8,
            matched_path="src/agents/run_loop.py",
            source="inventory",
        )
    ]


def test_resolve_mentions_returns_multiple_candidates_for_ambiguous_basename() -> None:
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="src/runner.py", kind="file"),
            WorkspaceFileEntry(path="tests/runner.py", kind="file"),
        ),
    )

    candidates = resolve_mentions_against_inventory("看看 runner.py", inventory)

    assert candidates == [
        MentionCandidate(
            text="runner.py",
            kind="filename",
            confidence=0.5,
            matched_path="src/runner.py",
            source="inventory",
        ),
        MentionCandidate(
            text="runner.py",
            kind="filename",
            confidence=0.5,
            matched_path="tests/runner.py",
            source="inventory",
        ),
    ]


def test_resolve_mentions_ignores_unreadable_ignored_and_directory_entries() -> None:
    inventory = WorkspaceInventory(
        root="/workspace",
        base_path=".",
        entries=(
            WorkspaceFileEntry(path="src/run_loop.py", kind="directory"),
            WorkspaceFileEntry(path="ignored/run_loop.py", kind="file", ignored=True),
            WorkspaceFileEntry(path="secret/run_loop.py", kind="file", readable=False),
        ),
    )

    candidates = resolve_mentions_against_inventory("看看 run_loop.py", inventory)

    assert candidates == [
        MentionCandidate(text="run_loop.py", kind="filename", confidence=0.8)
    ]


def test_context_mentions_public_api_is_exported_from_agents_package() -> None:
    from agents import MentionCandidate as PackageMentionCandidate
    from agents import detect_file_mentions as package_detect_file_mentions
    from agents import resolve_mentions_against_inventory as package_resolve_mentions_against_inventory

    assert PackageMentionCandidate is MentionCandidate
    assert package_detect_file_mentions is detect_file_mentions
    assert package_resolve_mentions_against_inventory is resolve_mentions_against_inventory


def test_detect_file_mentions_returns_empty_list_for_empty_input() -> None:
    assert detect_file_mentions("") == []
    assert detect_file_mentions("   ") == []


def test_detect_file_mentions_handles_mixed_task_text_with_path_and_symbol() -> None:
    candidates = detect_file_mentions(
        "请检查 src/agents/context_chunks.py，然后看 build_turn_context"
    )

    assert candidates == [
        MentionCandidate(
            text="src/agents/context_chunks.py",
            kind="path",
            confidence=1.0,
        ),
        MentionCandidate(text="build_turn_context", kind="symbol", confidence=0.7),
    ]
