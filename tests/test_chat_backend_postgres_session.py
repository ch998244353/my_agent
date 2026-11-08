from __future__ import annotations

import unittest
from unittest.mock import Mock
from uuid import uuid4

from agents.chat_backend.postgres_session import (
    CommitOnSuccessSession,
    PostgresSession,
)
from agents.contracts import ChatMessage


class CommitOnSuccessSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conversation_id = uuid4()
        self.store = Mock()
        self.session = CommitOnSuccessSession(
            PostgresSession(self.conversation_id, self.store),
            self.store,
        )

    def test_commit_preserves_the_existing_chat_path(self) -> None:
        items = [
            ChatMessage("user", "question"),
            ChatMessage("assistant", "answer"),
        ]
        self.store.add_batch.return_value = ()
        self.session.add_items(items)

        self.assertEqual(self.session.commit("answer"), ())
        self.store.add_batch.assert_called_once_with(
            self.conversation_id,
            items,
        )
        with self.assertRaisesRegex(RuntimeError, "already committed"):
            self.session.commit("answer")

    def test_commit_can_replace_only_the_final_assistant_text(self) -> None:
        self.store.add_batch.return_value = ()
        self.session.add_items(
            [
                ChatMessage("user", "question"),
                ChatMessage("assistant", '{"kind":"question"}'),
            ]
        )

        self.session.commit(
            '{"kind":"question"}',
            stored_answer="Which database should be used?",
        )

        self.store.add_batch.assert_called_once_with(
            self.conversation_id,
            [
                ChatMessage("user", "question"),
                ChatMessage("assistant", "Which database should be used?"),
            ],
        )

    def test_invalid_final_or_replacement_does_not_write(self) -> None:
        self.session.add_items(
            [
                ChatMessage("user", "question"),
                ChatMessage("assistant", "answer"),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "final answer"):
            self.session.commit("different")
        with self.assertRaisesRegex(ValueError, "stored_answer"):
            self.session.commit("answer", stored_answer="  ")

        self.store.add_batch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
