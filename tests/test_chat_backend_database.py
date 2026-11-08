from __future__ import annotations

import unittest
from unittest.mock import call, patch

from psycopg import OperationalError

from agents.chat_backend.database import Database


class DatabaseTests(unittest.TestCase):
    @patch("agents.chat_backend.database.ConnectionPool")
    def test_builds_closed_pool_and_delegates_lifecycle(self, pool_type) -> None:
        pool = pool_type.return_value
        database = Database(
            "postgresql://localhost/myagent",
            min_size=2,
            max_size=6,
            timeout=1.5,
        )

        pool_type.assert_called_once_with(
            "postgresql://localhost/myagent",
            min_size=2,
            max_size=6,
            timeout=1.5,
            open=False,
            kwargs={"autocommit": True},
        )
        pool.open.assert_not_called()

        database.open()
        database.open()
        database.close()
        database.close()

        self.assertEqual(
            pool.open.call_args_list,
            [
                call(wait=True, timeout=1.5),
                call(wait=True, timeout=1.5),
            ],
        )
        self.assertEqual(pool.close.call_count, 2)

    @patch("agents.chat_backend.database.ConnectionPool")
    def test_open_preserves_pool_error(self, pool_type) -> None:
        error = RuntimeError("connection failed")
        pool_type.return_value.open.side_effect = error

        with self.assertRaises(RuntimeError) as caught:
            Database("postgresql://localhost/myagent").open()

        self.assertIs(caught.exception, error)

    @patch("agents.chat_backend.database.ConnectionPool")
    def test_connection_returns_resource_and_preserves_body_error(
        self, pool_type
    ) -> None:
        pool = pool_type.return_value
        borrowed = pool.connection.return_value.__enter__.return_value
        database = Database("postgresql://localhost/myagent", timeout=1.5)

        with database.connection() as connection:
            self.assertIs(connection, borrowed)

        error = RuntimeError("body failed")
        with self.assertRaises(RuntimeError) as caught:
            with database.connection():
                raise error

        self.assertIs(caught.exception, error)
        self.assertEqual(pool.connection.call_args_list, [call(timeout=1.5)] * 2)
        self.assertEqual(pool.connection.return_value.__exit__.call_count, 2)

    @patch("agents.chat_backend.database.ConnectionPool")
    def test_transaction_uses_same_connection_and_preserves_body_error(
        self, pool_type
    ) -> None:
        pool = pool_type.return_value
        connection_context = pool.connection.return_value
        connection = connection_context.__enter__.return_value
        transaction_context = connection.transaction.return_value
        database = Database("postgresql://localhost/myagent")

        with database.transaction() as transaction_connection:
            self.assertIs(transaction_connection, connection)

        error = RuntimeError("transaction body failed")
        with self.assertRaises(RuntimeError) as caught:
            with database.transaction():
                raise error

        self.assertIs(caught.exception, error)
        self.assertEqual(connection.transaction.call_count, 2)
        self.assertEqual(transaction_context.__exit__.call_count, 2)
        self.assertEqual(connection_context.__exit__.call_count, 2)

    @patch("agents.chat_backend.database.ConnectionPool")
    def test_health_returns_fixed_status_and_preserves_code_errors(
        self, pool_type
    ) -> None:
        pool = pool_type.return_value
        connection = pool.connection.return_value.__enter__.return_value
        database = Database("postgresql://localhost/myagent")

        ready = database.health()

        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.detail, "postgres ready")
        connection.execute.assert_called_once_with("SELECT 1")

        pool.connection.side_effect = OperationalError("secret database detail")
        unavailable = database.health()

        self.assertEqual(unavailable.status, "unavailable")
        self.assertEqual(unavailable.detail, "postgres unavailable")
        self.assertNotIn("secret", unavailable.detail)

        pool.connection.side_effect = None
        connection.execute.side_effect = RuntimeError("code bug")
        with self.assertRaisesRegex(RuntimeError, "code bug"):
            database.health()


if __name__ == "__main__":
    unittest.main()
