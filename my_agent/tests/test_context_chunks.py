from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


class ContextChunksTestCase(unittest.TestCase):
    def _load_context_api(self):
        try:
            from agents.context_chunks import (
                ContextChunk,
                build_turn_context,
                render_context_messages,
            )
        except ModuleNotFoundError as exc:
            self.fail(f"context chunk API is missing: {exc}")
        return ContextChunk, build_turn_context, render_context_messages

    def test_render_context_messages_uses_priority_then_original_order(self) -> None:
        ContextChunk, _, render_context_messages = self._load_context_api()
        chunks = [
            ContextChunk(
                name="current_task",
                role="user",
                content="Fix the failing test.",
                priority=100,
                source="task",
            ),
            ContextChunk(
                name="system_instructions",
                role="system",
                content="You are a coding agent.",
                priority=0,
                source="agent",
            ),
            ContextChunk(
                name="memory",
                role="assistant",
                content="Previous step inspected run_loop.py.",
                priority=50,
                source="memory",
            ),
        ]

        messages = render_context_messages(chunks)

        self.assertEqual(
            [(message.role, message.content) for message in messages],
            [
                ("system", "You are a coding agent."),
                ("assistant", "Previous step inspected run_loop.py."),
                ("user", "Fix the failing test."),
            ],
        )

    def test_render_context_messages_skips_empty_content(self) -> None:
        ContextChunk, _, render_context_messages = self._load_context_api()
        messages = render_context_messages(
            [
                ContextChunk(
                    name="empty_repo_context",
                    role="user",
                    content="",
                    priority=60,
                    source="repo",
                ),
                ContextChunk(
                    name="current_task",
                    role="user",
                    content="Explain the plan.",
                    priority=100,
                    source="task",
                ),
            ]
        )

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].content, "Explain the plan.")

    def test_build_turn_context_preserves_agent_message_semantics(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentMemory, ChatMessage, StepRecord

        class RecordingModel:
            pass

        memory = AgentMemory()
        memory.add_task("Inspect model_turn.py.")
        memory.add_step(
            StepRecord(
                step_number=1,
                messages=[
                    ChatMessage(
                        role="assistant",
                        content="I inspected the current turn input path.",
                    )
                ],
                observation="prepare_turn_input delegates message construction.",
            )
        )
        agent = Agent(
            memory=memory,
            model=RecordingModel(),
            instructions="You are a coding agent.",
        )

        bundle = build_turn_context(agent)

        self.assertEqual(bundle.to_messages(), agent._messages_for_model())
        self.assertEqual(
            [chunk.name for chunk in bundle.chunks],
            ["system_instructions", "memory", "memory", "memory"],
        )

    def test_build_turn_context_separates_session_summary_from_memory(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentSession, MemorySummary

        class RecordingModel:
            pass

        session = AgentSession(
            summary=MemorySummary(
                content="Earlier turns established the repository layout.",
                source_turn_count=2,
            )
        )
        session.add_task("Continue the context layering work.")
        agent = Agent(
            memory=session,
            model=RecordingModel(),
            instructions="You are a coding agent.",
        )

        bundle = build_turn_context(agent)

        self.assertEqual(
            [chunk.name for chunk in bundle.chunks],
            ["system_instructions", "session_summary", "memory"],
        )
        self.assertEqual(
            [message.content for message in bundle.to_messages()],
            [
                "You are a coding agent.",
                "Conversation summary:\nEarlier turns established the repository layout.",
                "Continue the context layering work.",
            ],
        )

    def test_reserved_workspace_chunk_names_do_not_emit_empty_messages(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentMemory
        from agents.context_chunks import (
            CONTEXT_CHUNK_REPO_CONTEXT,
            CONTEXT_CHUNK_SELECTED_FILES,
            CONTEXT_CHUNK_VERIFICATION_SUMMARY,
            CONTEXT_CHUNK_WORKSPACE_CONTEXT,
            RESERVED_CONTEXT_CHUNK_NAMES,
        )

        class RecordingModel:
            pass

        memory = AgentMemory()
        memory.add_task("Inspect the repository.")
        agent = Agent(memory=memory, model=RecordingModel())

        bundle = build_turn_context(agent)

        self.assertEqual(
            RESERVED_CONTEXT_CHUNK_NAMES,
            (
                CONTEXT_CHUNK_WORKSPACE_CONTEXT,
                CONTEXT_CHUNK_REPO_CONTEXT,
                CONTEXT_CHUNK_SELECTED_FILES,
                CONTEXT_CHUNK_VERIFICATION_SUMMARY,
            ),
        )
        self.assertNotIn(CONTEXT_CHUNK_WORKSPACE_CONTEXT, [chunk.name for chunk in bundle.chunks])
        self.assertNotIn(CONTEXT_CHUNK_REPO_CONTEXT, [chunk.name for chunk in bundle.chunks])
        self.assertNotIn(CONTEXT_CHUNK_SELECTED_FILES, [chunk.name for chunk in bundle.chunks])
        self.assertNotIn(CONTEXT_CHUNK_VERIFICATION_SUMMARY, [chunk.name for chunk in bundle.chunks])
        self.assertTrue(all(message.content.strip() for message in bundle.to_messages()))

    def test_build_turn_context_skips_empty_selected_files_state(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentMemory
        from agents.context_chunks import CONTEXT_CHUNK_SELECTED_FILES
        from agents.run_context import CONTEXT_SELECTED_FILES_KEY, RunContextWrapper
        from agents.selected_files import SelectedFilesState

        class RecordingModel:
            pass

        agent = Agent(memory=AgentMemory(), model=RecordingModel())
        context_wrapper = RunContextWrapper(
            context={CONTEXT_SELECTED_FILES_KEY: SelectedFilesState()}
        )

        bundle = build_turn_context(agent, context_wrapper=context_wrapper)

        self.assertNotIn(CONTEXT_CHUNK_SELECTED_FILES, [chunk.name for chunk in bundle.chunks])
        self.assertTrue(all(message.content.strip() for message in bundle.to_messages()))

    def test_build_turn_context_renders_selected_files_state(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentMemory
        from agents.context_chunks import CONTEXT_CHUNK_SELECTED_FILES
        from agents.run_context import CONTEXT_SELECTED_FILES_KEY, RunContextWrapper
        from agents.selected_files import SelectedFilesState

        class RecordingModel:
            pass

        selected_files = SelectedFilesState()
        selected_files.add_file(
            "src/app.py",
            mode="editable",
            reason="manual_add",
            source="cli",
        )
        selected_files.add_file(
            "tests/test_app.py",
            mode="read_only",
            reason="mentioned_by_user",
            source="context_mentions",
        )
        agent = Agent(memory=AgentMemory(), model=RecordingModel())
        context_wrapper = RunContextWrapper(
            context={CONTEXT_SELECTED_FILES_KEY: selected_files}
        )

        bundle = build_turn_context(agent, context_wrapper=context_wrapper)

        selected_chunks = [
            chunk for chunk in bundle.chunks if chunk.name == CONTEXT_CHUNK_SELECTED_FILES
        ]
        self.assertEqual(len(selected_chunks), 1)
        self.assertEqual(selected_chunks[0].role, "user")
        self.assertEqual(selected_chunks[0].source, "selected_files")
        self.assertEqual(
            selected_chunks[0].content,
            "Selected files:\n"
            "- src/app.py [editable] reason=manual_add source=cli\n"
            "- tests/test_app.py [read_only] reason=mentioned_by_user "
            "source=context_mentions",
        )
        self.assertIn(selected_chunks[0].to_message(), bundle.to_messages())

    def test_prepare_turn_input_renders_repo_context_from_context_wrapper(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentMemory
        from agents.context_chunks import CONTEXT_CHUNK_REPO_CONTEXT
        from agents.model_turn import prepare_turn_input
        from agents.repo_context import RepoContext, RepoContextSection
        from agents.run_context import CONTEXT_REPO_CONTEXT_KEY, RunContextWrapper

        class RecordingModel:
            pass

        repo_context = RepoContext(
            sections=(
                RepoContextSection(
                    title="Selected files",
                    content="- src/agents/context_chunks.py [read_only]",
                    source="selected_files",
                    priority=10,
                ),
            ),
            selected_paths=("src/agents/context_chunks.py",),
        )
        agent = Agent(
            memory=AgentMemory(),
            model=RecordingModel(),
            instructions="You are a coding agent.",
        )
        context_wrapper = RunContextWrapper(
            context={CONTEXT_REPO_CONTEXT_KEY: repo_context}
        )

        turn_input = prepare_turn_input(agent, context_wrapper=context_wrapper)

        self.assertEqual(turn_input.messages[0].content, "You are a coding agent.")
        self.assertEqual(
            turn_input.messages[1].content,
            "Repo context:\n"
            "Selected paths: src/agents/context_chunks.py\n\n"
            "## Selected files\n"
            "- src/agents/context_chunks.py [read_only]",
        )
        bundle = build_turn_context(agent, context_wrapper=context_wrapper)
        self.assertIn(CONTEXT_CHUNK_REPO_CONTEXT, [chunk.name for chunk in bundle.chunks])

    def test_build_turn_context_skips_empty_repo_context(self) -> None:
        _, build_turn_context, _ = self._load_context_api()
        from agents import Agent, AgentMemory
        from agents.context_chunks import CONTEXT_CHUNK_REPO_CONTEXT
        from agents.repo_context import RepoContext
        from agents.run_context import CONTEXT_REPO_CONTEXT_KEY, RunContextWrapper

        class RecordingModel:
            pass

        agent = Agent(memory=AgentMemory(), model=RecordingModel())
        context_wrapper = RunContextWrapper(
            context={CONTEXT_REPO_CONTEXT_KEY: RepoContext()}
        )

        bundle = build_turn_context(agent, context_wrapper=context_wrapper)

        self.assertNotIn(CONTEXT_CHUNK_REPO_CONTEXT, [chunk.name for chunk in bundle.chunks])
        self.assertTrue(all(message.content.strip() for message in bundle.to_messages()))


if __name__ == "__main__":
    unittest.main()
