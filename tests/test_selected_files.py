from dataclasses import FrozenInstanceError

import pytest


def test_selected_file_serializes_read_only_entry() -> None:
    from agents.selected_files import SelectedFile

    selected_file = SelectedFile(
        path=" src\\agents\\run_context.py ",
        mode="read_only",
        reason="mentioned_by_user",
        source="context_mentions",
    )

    assert selected_file.path == "src/agents/run_context.py"
    assert selected_file.to_dict() == {
        "path": "src/agents/run_context.py",
        "mode": "read_only",
        "reason": "mentioned_by_user",
        "source": "context_mentions",
    }
    assert (
        selected_file.summary_line()
        == "- src/agents/run_context.py [read_only] reason=mentioned_by_user source=context_mentions"
    )


def test_selected_file_serializes_editable_entry() -> None:
    from agents.selected_files import SelectedFile

    selected_file = SelectedFile(
        path="my_agent/src/agents/selected_files.py",
        mode="editable",
        reason="manual_add",
        source="cli",
    )

    assert selected_file.to_dict()["mode"] == "editable"
    assert "manual_add" in selected_file.summary_line()


def test_selected_file_rejects_empty_path() -> None:
    from agents.selected_files import SelectedFile

    with pytest.raises(ValueError, match="path"):
        SelectedFile(path="  ", mode="read_only", reason="manual_add", source="cli")


def test_selected_file_rejects_unknown_mode() -> None:
    from agents.selected_files import SelectedFile

    with pytest.raises(ValueError, match="mode"):
        SelectedFile(path="README.md", mode="write", reason="manual_add", source="cli")


def test_selected_file_is_frozen() -> None:
    from agents.selected_files import SelectedFile

    selected_file = SelectedFile(
        path="README.md",
        mode="read_only",
        reason="manual_add",
        source="cli",
    )

    with pytest.raises(FrozenInstanceError):
        selected_file.path = "other.md"


def test_selected_files_state_adds_gets_and_lists_sorted_files() -> None:
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()

    first = state.add_file(
        "src\\agents\\model_turn.py",
        mode="read_only",
        reason="mentioned_by_user",
        source="context_mentions",
    )
    second = state.add_file(
        "README.md",
        mode="editable",
        reason="manual_add",
        source="cli",
    )

    assert state.get("src/agents/model_turn.py") is first
    assert state.get("README.md") is second
    assert [selected_file.path for selected_file in state.files()] == [
        "README.md",
        "src/agents/model_turn.py",
    ]


def test_selected_files_state_deduplicates_same_path() -> None:
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()

    first = state.add_file(
        "README.md",
        mode="read_only",
        reason="mentioned_by_user",
        source="context_mentions",
    )
    second = state.add_file(
        "README.md",
        mode="read_only",
        reason="auto_selected",
        source="repo_context",
    )

    assert first is second
    assert len(state.files()) == 1
    assert state.get("README.md").reason == "mentioned_by_user"


def test_selected_files_state_promotes_read_only_to_editable() -> None:
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()

    state.add_file(
        "src/agents/context_chunks.py",
        mode="read_only",
        reason="mentioned_by_user",
        source="context_mentions",
    )
    promoted = state.add_file(
        "src/agents/context_chunks.py",
        mode="editable",
        reason="manual_add",
        source="cli",
    )

    assert promoted.mode == "editable"
    assert promoted.reason == "manual_add"
    assert state.get("src\\agents\\context_chunks.py") is promoted


def test_selected_files_state_does_not_downgrade_editable_to_read_only() -> None:
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()

    editable = state.add_file(
        "src/agents/context_chunks.py",
        mode="editable",
        reason="manual_add",
        source="cli",
    )
    readonly = state.add_file(
        "src/agents/context_chunks.py",
        mode="read_only",
        reason="auto_selected",
        source="repo_context",
    )

    assert readonly is editable
    assert state.get("src/agents/context_chunks.py").mode == "editable"
    assert state.get("src/agents/context_chunks.py").reason == "manual_add"


def test_selected_files_state_drops_files_and_summarizes_remaining_files() -> None:
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()
    state.add_file(
        "src/agents/model_turn.py",
        mode="read_only",
        reason="mentioned_by_user",
        source="context_mentions",
    )
    state.add_file(
        "README.md",
        mode="editable",
        reason="manual_add",
        source="cli",
    )

    assert state.drop_file("src\\agents\\model_turn.py") is True
    assert state.drop_file("missing.py") is False
    assert state.get("src/agents/model_turn.py") is None
    assert state.summary() == (
        "- README.md [editable] reason=manual_add source=cli"
    )


def test_selected_files_state_empty_summary_is_blank() -> None:
    from agents.selected_files import SelectedFilesState

    assert SelectedFilesState().summary() == ""


def test_selected_files_state_adds_resolved_mentions_as_read_only_files() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()

    added = state.add_mentions(
        [
            MentionCandidate(
                text="run_loop.py",
                kind="filename",
                confidence=0.8,
                matched_path="src/agents/run_loop.py",
                source="inventory",
            ),
            MentionCandidate(
                text="README.md",
                kind="filename",
                confidence=0.8,
                matched_path="README.md",
                source="inventory",
            ),
        ]
    )

    assert [selected_file.path for selected_file in added] == [
        "src/agents/run_loop.py",
        "README.md",
    ]
    assert [selected_file.path for selected_file in state.files()] == [
        "README.md",
        "src/agents/run_loop.py",
    ]
    assert state.get("src/agents/run_loop.py").mode == "read_only"
    assert state.get("src/agents/run_loop.py").reason == "mentioned_by_user"
    assert state.get("src/agents/run_loop.py").source == "context_mentions"


def test_selected_files_state_add_mentions_ignores_unresolved_candidates() -> None:
    from agents.context_mentions import MentionCandidate
    from agents.selected_files import SelectedFilesState

    state = SelectedFilesState()

    added = state.add_mentions(
        [
            MentionCandidate(
                text="RunState",
                kind="symbol",
                confidence=0.7,
            ),
            MentionCandidate(
                text="run_loop.py",
                kind="filename",
                confidence=0.8,
                matched_path=None,
            ),
        ],
        mode="editable",
        source="task_parser",
    )

    assert added == ()
    assert state.files() == ()


def test_add_task_mentions_to_selected_files_updates_context_state(tmp_path) -> None:
    from agents.run_context import (
        CONTEXT_SELECTED_FILES_KEY,
        CONTEXT_WORKSPACE_KEY,
        RunContextWrapper,
    )
    from agents.selected_files import (
        SelectedFilesState,
        add_task_mentions_to_selected_files,
    )
    from agents.workspace import Workspace

    source_dir = tmp_path / "src" / "agents"
    source_dir.mkdir(parents=True)
    (source_dir / "run_loop.py").write_text("def run_loop():\n    pass\n", encoding="utf-8")
    selected_files = SelectedFilesState()
    context_wrapper = RunContextWrapper(
        context={
            CONTEXT_WORKSPACE_KEY: Workspace(tmp_path),
            CONTEXT_SELECTED_FILES_KEY: selected_files,
        }
    )

    added = add_task_mentions_to_selected_files(
        "请修改 run_loop.py，然后说明影响",
        context_wrapper,
    )

    assert [selected_file.path for selected_file in added] == ["src/agents/run_loop.py"]
    assert selected_files.get("src/agents/run_loop.py").mode == "read_only"
    assert selected_files.get("src/agents/run_loop.py").reason == "mentioned_by_user"
    assert selected_files.get("src/agents/run_loop.py").source == "context_mentions"


def test_run_loop_adds_task_mentions_to_selected_files_before_model_input(tmp_path) -> None:
    from agents import Runner, ToolCall, build_coding_agent

    class FinalAnswerModel:
        def __init__(self) -> None:
            self.last_messages = []

        def decide(self, messages, tool_specs):
            self.last_messages = list(messages)
            return ToolCall("final_answer", {"answer": "done"}, "call_1")

    source_dir = tmp_path / "src" / "agents"
    source_dir.mkdir(parents=True)
    (source_dir / "run_loop.py").write_text("def run_loop():\n    pass\n", encoding="utf-8")
    model = FinalAnswerModel()
    setup = build_coding_agent(model=model, workspace=tmp_path)

    result = Runner.run_sync(
        setup.agent,
        "请修改 run_loop.py",
        config=setup.run_config,
    )

    selected_files = result.context_wrapper.selected_files
    assert selected_files is not None
    assert selected_files.get("src/agents/run_loop.py") is not None
    assert any(
        "Selected files:\n- src/agents/run_loop.py [read_only] "
        "reason=mentioned_by_user source=context_mentions" in message.content
        for message in model.last_messages
    )


def test_selected_files_api_is_public_from_package_root() -> None:
    import agents
    from agents import (
        SelectedFile as ExportedSelectedFile,
        SelectedFilesState as ExportedSelectedFilesState,
        add_task_mentions_to_selected_files as exported_add_task_mentions_to_selected_files,
    )
    from agents.selected_files import (
        SelectedFile,
        SelectedFilesState,
        add_task_mentions_to_selected_files,
    )

    assert ExportedSelectedFile is SelectedFile
    assert ExportedSelectedFilesState is SelectedFilesState
    assert exported_add_task_mentions_to_selected_files is add_task_mentions_to_selected_files
    assert "SelectedFile" in agents.__all__
    assert "SelectedFilesState" in agents.__all__
    assert "add_task_mentions_to_selected_files" in agents.__all__
