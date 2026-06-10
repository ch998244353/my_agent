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


if __name__ == "__main__":
    unittest.main()
