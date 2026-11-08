from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from psycopg import OperationalError

from agents.contracts import ChatMessage
from agents.chat_backend import (
    Conversation,
    ConversationArchivedError,
    ConversationCursor,
    ConversationNotFoundError,
    ConversationPage,
    ConversationPatch,
    ConversationStore,
    MessagePage,
    MessageStore,
    PostgresSession,
    StoredMessage,
)


class ConversationContractTests(unittest.TestCase):
    def test_records_are_frozen_and_keep_database_values(self) -> None:
        conversation_id = uuid4()
        message_id = uuid4()
        created_at = datetime.now(timezone.utc)
        conversation = Conversation(conversation_id, created_at, 2)
        cursor = ConversationCursor(created_at, conversation_id)
        conversation_page = ConversationPage((conversation,), cursor)
        patch = ConversationPatch(title="Renamed", status="archived")
        message = StoredMessage(
            message_id,
            conversation_id,
            1,
            "user",
            "hello",
            created_at,
        )
        page = MessagePage((message,), before_sequence=None)

        self.assertEqual(conversation.id, conversation_id)
        self.assertEqual(conversation.next_message_sequence, 2)
        self.assertEqual(conversation.title, "New conversation")
        self.assertEqual(conversation.status, "active")
        self.assertEqual(conversation.mode, "default")
        self.assertEqual(conversation_page.next_cursor, cursor)
        self.assertEqual(patch.status, "archived")
        self.assertFalse(hasattr(patch, "mode"))
        self.assertEqual(page.items, (message,))
        self.assertIsNone(page.before_sequence)
        with self.assertRaises(FrozenInstanceError):
            conversation.next_message_sequence = 3  # type: ignore[misc]

    def test_not_found_error_keeps_only_the_conversation_id(self) -> None:
        conversation_id = uuid4()
        error = ConversationNotFoundError(conversation_id)

        self.assertIsInstance(error, LookupError)
        self.assertEqual(error.conversation_id, conversation_id)
        self.assertEqual(str(error), f"conversation not found: {conversation_id}")

        archived = ConversationArchivedError(conversation_id)
        self.assertIsInstance(archived, RuntimeError)
        self.assertEqual(archived.conversation_id, conversation_id)
        self.assertEqual(str(archived), f"conversation is archived: {conversation_id}")


class ConversationStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.connection = (
            self.database.connection.return_value.__enter__.return_value
        )

    @patch("agents.chat_backend.conversation.uuid4")
    def test_create_normalizes_title_and_returns_database_values(
        self,
        new_uuid,
    ) -> None:
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        new_uuid.return_value = conversation_id
        self.connection.execute.return_value.fetchone.return_value = (
            conversation_id,
            created_at,
            1,
            "Project",
            "active",
            "default",
        )

        conversation = ConversationStore(self.database).create("  Project  ")

        self.assertEqual(
            conversation,
            Conversation(
                conversation_id,
                created_at,
                1,
                "Project",
                "active",
                "default",
            ),
        )
        self.connection.execute.assert_called_once_with(
            "INSERT INTO conversations (id, title) VALUES (%s, %s) "
            "RETURNING id, created_at, next_message_sequence, title, status, mode",
            (conversation_id, "Project"),
        )

    def test_get_returns_row_or_raises_not_found(self) -> None:
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        statement = (
            "SELECT id, created_at, next_message_sequence, title, status, mode "
            "FROM conversations WHERE id = %s"
        )
        self.connection.execute.return_value.fetchone.return_value = (
            conversation_id,
            created_at,
            1,
            "New conversation",
            "active",
            "plan",
        )
        store = ConversationStore(self.database)

        self.assertEqual(
            store.get(conversation_id),
            Conversation(
                conversation_id,
                created_at,
                1,
                "New conversation",
                "active",
                "plan",
            ),
        )
        self.connection.execute.assert_called_once_with(
            statement,
            (conversation_id,),
        )

        self.connection.reset_mock()
        self.connection.execute.return_value.fetchone.return_value = None
        missing_id = uuid4()
        with self.assertRaises(ConversationNotFoundError) as caught:
            store.get(missing_id)
        self.assertEqual(caught.exception.conversation_id, missing_id)
        self.connection.execute.assert_called_once_with(statement, (missing_id,))

    def test_list_uses_stable_cursor_and_page_size_plus_one(self) -> None:
        created_at = datetime.now(timezone.utc)
        ids = tuple(uuid4() for _ in range(3))
        self.connection.execute.return_value.fetchall.return_value = [
            (ids[0], created_at, 1, "First", "active", "default"),
            (ids[1], created_at, 1, "Second", "active", "plan"),
            (ids[2], created_at, 1, "Third", "active", "default"),
        ]
        before = ConversationCursor(created_at, uuid4())

        page = ConversationStore(self.database).list(
            page_size=2,
            before=before,
            status="active",
        )

        self.assertEqual(tuple(item.id for item in page.items), ids[:2])
        self.assertEqual(
            tuple(item.mode for item in page.items),
            ("default", "plan"),
        )
        self.assertEqual(
            page.next_cursor,
            ConversationCursor(created_at, ids[1]),
        )
        self.connection.execute.assert_called_once_with(
            "SELECT id, created_at, next_message_sequence, title, status, mode "
            "FROM conversations WHERE (created_at, id) < (%s, %s) "
            "AND status = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (before.created_at, before.conversation_id, "active", 3),
        )

    def test_update_and_delete_return_rows_or_raise_not_found(self) -> None:
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        row = (conversation_id, created_at, 1, "Renamed", "archived", "plan")
        self.connection.execute.return_value.fetchone.return_value = row
        store = ConversationStore(self.database)

        updated = store.update(
            conversation_id,
            ConversationPatch(title="  Renamed  ", status="archived"),
        )

        self.assertEqual(updated, Conversation(*row))
        self.connection.execute.assert_called_once_with(
            "UPDATE conversations SET title = %s, status = %s WHERE id = %s "
            "RETURNING id, created_at, next_message_sequence, title, status, mode",
            ("Renamed", "archived", conversation_id),
        )

        self.connection.reset_mock()
        self.connection.execute.return_value.fetchone.return_value = (
            conversation_id,
        )
        store.delete(conversation_id)
        self.connection.execute.assert_called_once_with(
            "DELETE FROM conversations WHERE id = %s RETURNING id",
            (conversation_id,),
        )

        self.connection.execute.return_value.fetchone.return_value = None
        with self.assertRaises(ConversationNotFoundError):
            store.update(conversation_id, ConversationPatch(title="Missing"))
        with self.assertRaises(ConversationNotFoundError):
            store.delete(conversation_id)

    def test_management_input_is_checked_before_database_access(self) -> None:
        store = ConversationStore(self.database)
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        invalid_operations = (
            lambda: store.create("   "),
            lambda: store.create("x" * 201),
            lambda: store.create(1),  # type: ignore[arg-type]
            lambda: store.list(page_size=0),
            lambda: store.list(page_size=True),
            lambda: store.list(page_size=101),
            lambda: store.list(status="paused"),  # type: ignore[arg-type]
            lambda: store.list(
                before=ConversationCursor(  # type: ignore[arg-type]
                    "bad",
                    conversation_id,
                )
            ),
            lambda: store.update(conversation_id, ConversationPatch()),
            lambda: store.update(
                conversation_id,
                ConversationPatch(status="paused"),  # type: ignore[arg-type]
            ),
        )

        for operation in invalid_operations:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        self.database.connection.assert_not_called()

    def test_invalid_mode_is_rejected_from_constructor_and_database_row(
        self,
    ) -> None:
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        with self.assertRaisesRegex(ValueError, "mode must be default or plan"):
            Conversation(
                conversation_id,
                created_at,
                1,
                mode="invalid",  # type: ignore[arg-type]
            )

        self.connection.execute.return_value.fetchone.return_value = (
            conversation_id,
            created_at,
            1,
            "New conversation",
            "active",
            "invalid",
        )
        with self.assertRaisesRegex(ValueError, "mode must be default or plan"):
            ConversationStore(self.database).get(conversation_id)

    def test_database_error_is_not_replaced(self) -> None:
        error = OperationalError("database unavailable")
        self.database.connection.side_effect = error

        with self.assertRaises(OperationalError) as caught:
            ConversationStore(self.database).get(uuid4())

        self.assertIs(caught.exception, error)


class MessageStoreValidationTests(unittest.TestCase):
    def test_empty_batch_is_a_no_op(self) -> None:
        database = MagicMock()

        self.assertEqual(MessageStore(database).add_batch(uuid4(), ()), ())
        database.transaction.assert_not_called()

    def test_read_limits_are_checked_before_opening_a_connection(self) -> None:
        database = MagicMock()
        store = MessageStore(database)
        conversation_id = uuid4()

        for operation in (
            lambda: store.list(conversation_id, 0),
            lambda: store.page(conversation_id, 0),
            lambda: store.page(conversation_id, 101),
            lambda: store.page(conversation_id, 1, before_sequence=0),
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):
                    operation()
        database.connection.assert_not_called()

    def test_run_trace_is_validated_and_returned_with_assistant_message(self) -> None:
        database = MagicMock()
        connection = database.connection.return_value.__enter__.return_value
        message_id = uuid4()
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        trace = ({"stage": "model", "message": "正在请求模型", "step_number": 1},)
        connection.execute.return_value.fetchone.return_value = (
            message_id,
            conversation_id,
            2,
            "assistant",
            "answer",
            created_at,
            list(trace),
        )
        store = MessageStore(database)

        stored = store.set_run_trace(message_id, trace)

        self.assertEqual(stored.run_trace, trace)
        self.assertIn("SET run_trace = %s", connection.execute.call_args.args[0])

        database.reset_mock()
        with self.assertRaises(ValueError):
            store.set_run_trace(
                message_id,
                ({"stage": "secret", "message": "bad"},),
            )
        database.connection.assert_not_called()


class PostgresSessionTests(unittest.TestCase):
    def test_non_positive_limit_skips_the_store(self) -> None:
        store = MagicMock()
        session = PostgresSession(uuid4(), store)

        self.assertEqual(session.get_items(0), [])
        self.assertEqual(session.get_items(-1), [])
        store.list.assert_not_called()

    def test_translates_session_items_and_stored_messages(self) -> None:
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        stored = StoredMessage(
            uuid4(),
            conversation_id,
            1,
            "assistant",
            "stored",
            created_at,
        )
        store = MagicMock()
        store.list.return_value = (stored,)
        store.pop_last.side_effect = (stored, None)
        session = PostgresSession(conversation_id, store)

        self.assertEqual(
            session.get_items(2),
            [ChatMessage("assistant", "stored")],
        )
        store.list.assert_called_once_with(conversation_id, 2)

        session.add_items(
            [
                ChatMessage("user", "hello"),
                {"role": "assistant", "content": "world"},
            ]
        )
        store.add_batch.assert_called_once_with(
            conversation_id,
            [
                ChatMessage("user", "hello"),
                ChatMessage("assistant", "world"),
            ],
        )

        self.assertEqual(session.pop_item(), ChatMessage("assistant", "stored"))
        self.assertIsNone(session.pop_item())
        session.clear_session()
        store.clear.assert_called_once_with(conversation_id)


if __name__ == "__main__":
    unittest.main()
