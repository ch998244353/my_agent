from __future__ import annotations

import hashlib
import unittest
from dataclasses import FrozenInstanceError, fields
from datetime import datetime, timezone
from unittest.mock import MagicMock
from uuid import uuid4

from psycopg import OperationalError

from agents.chat_backend.embedding import EMBEDDING_DIMENSIONS
from agents.chat_backend.memory import (
    MemoryCreate,
    MemoryCursor,
    MemoryDuplicateError,
    MemoryEmbeddingConflictError,
    MemoryEmbeddingTarget,
    MemoryNotFoundError,
    MemoryPage,
    MemoryPatch,
    MemoryRecord,
    MemorySaveResult,
    MemorySearchHit,
    MemorySource,
    MemoryStore,
    MemorySupersedeError,
    MemoryValidationError,
    embed_missing_memories,
    _memory_from_row,
    _normalize_content,
    _normalize_tags,
    _validate_memory_create,
    _validate_source,
    memory_fingerprint,
)


def _embedding(value: float = 0.0) -> tuple[float, ...]:
    return (value,) * EMBEDDING_DIMENSIONS


class MemoryContractTests(unittest.TestCase):
    def test_records_are_frozen_and_combine_source_values(self) -> None:
        memory_id = uuid4()
        conversation_id = uuid4()
        user_message_id = uuid4()
        assistant_message_id = uuid4()
        created_at = datetime.now(timezone.utc)

        record = _memory_from_row(
            (
                memory_id,
                "preference",
                "conversation",
                "active",
                "Use concise answers.",
                ["style", "answer"],
                0.9,
                "a" * 64,
                conversation_id,
                user_message_id,
                assistant_message_id,
                created_at,
                created_at,
                None,
                None,
                None,
            )
        )

        self.assertEqual(record.id, memory_id)
        self.assertEqual(record.tags, ("style", "answer"))
        self.assertIsNone(record.supersedes_id)
        self.assertEqual(
            record.source,
            MemorySource(
                conversation_id,
                user_message_id,
                assistant_message_id,
            ),
        )
        with self.assertRaises(FrozenInstanceError):
            record.content = "changed"  # type: ignore[misc]

    def test_not_found_error_keeps_only_the_memory_id(self) -> None:
        memory_id = uuid4()
        error = MemoryNotFoundError(memory_id)

        self.assertIsInstance(error, LookupError)
        self.assertEqual(error.memory_id, memory_id)
        self.assertEqual(str(error), f"memory not found: {memory_id}")

    def test_create_and_save_result_are_frozen(self) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        create = MemoryCreate(
            kind="fact",
            scope="global",
            content="A fact",
            source=source,
        )
        now = datetime.now(timezone.utc)
        record = MemoryRecord(
            uuid4(),
            "fact",
            "global",
            "active",
            "A fact",
            (),
            1.0,
            "a" * 64,
            source,
            now,
            now,
        )
        result = MemorySaveResult(record, created=True)

        self.assertEqual(create.tags, ())
        self.assertEqual(create.confidence, 1.0)
        self.assertIs(result.record, record)
        with self.assertRaises(FrozenInstanceError):
            create.content = "changed"  # type: ignore[misc]

    def test_cursor_and_page_are_frozen(self) -> None:
        cursor = MemoryCursor(datetime.now(timezone.utc), uuid4())
        page = MemoryPage((), cursor)

        self.assertIs(page.next_cursor, cursor)
        with self.assertRaises(FrozenInstanceError):
            cursor.memory_id = uuid4()  # type: ignore[misc]

    def test_patch_fields_and_duplicate_error_are_stable(self) -> None:
        patch = MemoryPatch(tags=())
        existing_id = uuid4()
        error = MemoryDuplicateError(existing_id)

        self.assertEqual(
            {field.name for field in fields(MemoryPatch)},
            {"content", "kind", "tags", "confidence"},
        )
        self.assertEqual(patch.tags, ())
        self.assertIsNone(patch.content)
        self.assertEqual(error.existing_id, existing_id)
        self.assertEqual(str(error), f"memory already exists: {existing_id}")
        with self.assertRaises(FrozenInstanceError):
            patch.kind = "fact"  # type: ignore[misc]

    def test_supersede_error_exposes_only_a_fixed_reason(self) -> None:
        error = MemorySupersedeError("old_disabled")

        self.assertIsInstance(error, ValueError)
        self.assertEqual(error.reason, "old_disabled")
        self.assertEqual(str(error), "memory supersede rejected: old_disabled")


class MemoryNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MemorySource(uuid4(), uuid4(), uuid4())

    def test_content_normalizes_unicode_and_whitespace(self) -> None:
        self.assertEqual(
            _normalize_content("  Caf\u0065\u0301\nUses   Python!  "),
            "Café Uses Python!",
        )
        self.assertNotEqual(
            memory_fingerprint("global", "Uses Python!"),
            memory_fingerprint("global", "uses python!"),
        )
        self.assertEqual(
            memory_fingerprint("global", "Café"),
            hashlib.sha256("global\nCafé".encode("utf-8")).hexdigest(),
        )

    def test_content_rejects_wrong_type_blank_and_too_long(self) -> None:
        for content, reason in (
            (None, "content_type"),
            (" \n\t ", "content_length"),
            ("x" * 2001, "content_length"),
        ):
            with self.subTest(content=content), self.assertRaises(
                MemoryValidationError
            ) as caught:
                _normalize_content(content)
            self.assertEqual(caught.exception.reason, reason)

    def test_tags_normalize_sort_and_remove_exact_duplicates(self) -> None:
        self.assertEqual(
            _normalize_tags([" Style ", "PYTHON", "style", "Caf\u0065\u0301"]),
            ("café", "python", "style"),
        )

    def test_tags_reject_bad_container_items_lengths_and_count(self) -> None:
        cases = (
            ("tag", "tags_type"),
            ([1], "tag_type"),
            ([" "], "tag_length"),
            (["x" * 41], "tag_length"),
            ([f"tag-{index}" for index in range(17)], "tags_count"),
        )
        for tags, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(
                MemoryValidationError
            ) as caught:
                _normalize_tags(tags)
            self.assertEqual(caught.exception.reason, reason)

    def test_create_validation_returns_database_ready_values(self) -> None:
        data = MemoryCreate(
            kind="preference",
            scope="conversation",
            content="  Use\nshort answers. ",
            source=self.source,
            tags=(" Style ", "STYLE"),
            confidence=1,
        )

        self.assertEqual(
            _validate_memory_create(data),
            (
                "preference",
                "conversation",
                "Use short answers.",
                ("style",),
                1.0,
            ),
        )

    def test_create_validation_rejects_fields_before_database_access(self) -> None:
        cases = (
            ({"kind": "unknown"}, "kind"),
            ({"scope": "unknown"}, "scope"),
            ({"confidence": True}, "confidence_type"),
            ({"confidence": float("nan")}, "confidence_range"),
            ({"confidence": float("inf")}, "confidence_range"),
            ({"confidence": -0.1}, "confidence_range"),
            ({"confidence": 1.1}, "confidence_range"),
            ({"confidence": 10**10000}, "confidence_range"),
        )
        for changes, reason in cases:
            values = {
                "kind": "fact",
                "scope": "global",
                "content": "fact",
                "source": self.source,
                **changes,
            }
            with self.subTest(reason=reason), self.assertRaises(
                MemoryValidationError
            ) as caught:
                _validate_memory_create(MemoryCreate(**values))
            self.assertEqual(caught.exception.reason, reason)

    def test_source_validation_checks_presence_roles_and_order(self) -> None:
        connection = MagicMock()
        user_id = self.source.user_message_id
        assistant_id = self.source.assistant_message_id
        cases = (
            (
                [(user_id, "user", 1), (assistant_id, "assistant", 2)],
                None,
            ),
            ([(assistant_id, "assistant", 2)], "source_user_missing"),
            ([(user_id, "user", 1)], "source_assistant_missing"),
            (
                [(user_id, "assistant", 1), (assistant_id, "assistant", 2)],
                "source_user_role",
            ),
            (
                [(user_id, "user", 1), (assistant_id, "user", 2)],
                "source_assistant_role",
            ),
            (
                [(user_id, "user", 2), (assistant_id, "assistant", 1)],
                "source_order",
            ),
        )
        for rows, reason in cases:
            connection.execute.return_value.fetchall.return_value = rows
            if reason is None:
                _validate_source(connection, self.source)
            else:
                with self.assertRaises(MemoryValidationError) as caught:
                    _validate_source(connection, self.source)
                self.assertEqual(caught.exception.reason, reason)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.connection = (
            self.database.connection.return_value.__enter__.return_value
        )

    def test_get_returns_database_row_or_raises_not_found(self) -> None:
        memory_id = uuid4()
        conversation_id = uuid4()
        created_at = datetime.now(timezone.utc)
        row = (
            memory_id,
            "fact",
            "global",
            "active",
            "The user teaches Python.",
            ["profile"],
            1.0,
            "b" * 64,
            conversation_id,
            uuid4(),
            uuid4(),
            created_at,
            created_at,
            None,
            None,
            None,
        )
        self.connection.execute.return_value.fetchone.return_value = row
        store = MemoryStore(self.database)

        self.assertEqual(
            store.get(memory_id),
            MemoryRecord(
                id=row[0],
                kind=row[1],
                scope=row[2],
                status=row[3],
                content=row[4],
                tags=("profile",),
                confidence=row[6],
                fingerprint=row[7],
                source=MemorySource(row[8], row[9], row[10]),
                created_at=row[11],
                updated_at=row[12],
            ),
        )
        self.connection.execute.assert_called_once_with(
            "SELECT id, kind, scope, status, content, tags, confidence, "
            "fingerprint, source_conversation_id, source_user_message_id, "
            "source_assistant_message_id, created_at, updated_at, supersedes_id, "
            "embedding_model, embedded_at "
            "FROM memories WHERE id = %s",
            (memory_id,),
        )

        self.connection.execute.return_value.fetchone.return_value = None
        missing_id = uuid4()
        with self.assertRaises(MemoryNotFoundError) as caught:
            store.get(missing_id)
        self.assertEqual(caught.exception.memory_id, missing_id)

    def test_get_epoch_returns_value_or_rejects_missing_fact(self) -> None:
        store = MemoryStore(self.database)
        self.connection.execute.return_value.fetchone.return_value = (0,)

        self.assertEqual(store.get_epoch(), 0)
        self.connection.execute.assert_called_once_with(
            "SELECT value FROM memory_epochs WHERE name = %s",
            ("memory",),
        )

        self.connection.execute.return_value.fetchone.return_value = None
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.get_epoch()

    def test_database_error_is_not_replaced(self) -> None:
        error = OperationalError("database unavailable")
        self.database.connection.side_effect = error

        with self.assertRaises(OperationalError) as caught:
            MemoryStore(self.database).get(uuid4())

        self.assertIs(caught.exception, error)

    def test_invalid_create_does_not_open_a_transaction(self) -> None:
        with self.assertRaises(MemoryValidationError):
            MemoryStore(self.database).create(
                MemoryCreate(
                    kind="fact",
                    scope="global",
                    content=" ",
                    source=MemorySource(uuid4(), uuid4(), uuid4()),
                )
            )

        self.database.transaction.assert_not_called()

    def test_list_rejects_invalid_parameters_before_database_access(self) -> None:
        store = MemoryStore(self.database)
        now = datetime.now(timezone.utc)
        cases = (
            {"page_size": 0},
            {"page_size": 101},
            {"page_size": True},
            {"page_size": 1.5},
            {"before": "bad"},
            {"before": MemoryCursor("bad", uuid4())},
            {"before": MemoryCursor(now, "bad")},
            {"status": "deleted"},
            {"scope": "local"},
            {"source_conversation_id": "bad"},
            {"source_message_id": "bad"},
        )

        for arguments in cases:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                store.list(**arguments)
        self.database.connection.assert_not_called()

    def test_list_uses_stable_cursor_and_parameterized_filters(self) -> None:
        now = datetime.now(timezone.utc)
        conversation_id = uuid4()
        source_message_id = uuid4()
        memory_ids = (uuid4(), uuid4(), uuid4())
        rows = [
            (
                memory_id,
                "fact",
                "conversation",
                "active",
                f"fact-{index}",
                [],
                1.0,
                f"{index:064x}",
                conversation_id,
                source_message_id,
                uuid4(),
                now,
                now,
                None,
                None,
                None,
            )
            for index, memory_id in enumerate(memory_ids, start=1)
        ]
        self.connection.execute.return_value.fetchall.return_value = rows
        before = MemoryCursor(now, uuid4())

        page = MemoryStore(self.database).list(
            page_size=2,
            before=before,
            status="active",
            scope="conversation",
            source_conversation_id=conversation_id,
            source_message_id=source_message_id,
        )

        self.assertEqual(tuple(item.id for item in page.items), memory_ids[:2])
        self.assertEqual(page.next_cursor, MemoryCursor(now, memory_ids[1]))
        self.connection.execute.assert_called_once_with(
            "SELECT id, kind, scope, status, content, tags, confidence, "
            "fingerprint, source_conversation_id, source_user_message_id, "
            "source_assistant_message_id, created_at, updated_at, supersedes_id, "
            "embedding_model, embedded_at "
            "FROM memories "
            "WHERE (created_at, id) < (%s, %s) AND status = %s "
            "AND scope = %s AND source_conversation_id = %s "
            "AND %s IN (source_user_message_id, source_assistant_message_id) "
            "ORDER BY created_at DESC, id DESC LIMIT %s",
            (
                now,
                before.memory_id,
                "active",
                "conversation",
                conversation_id,
                source_message_id,
                3,
            ),
        )

    def test_list_empty_and_exact_pages_have_no_cursor(self) -> None:
        now = datetime.now(timezone.utc)
        row = (
            uuid4(),
            "fact",
            "global",
            "active",
            "fact",
            [],
            1.0,
            "a" * 64,
            uuid4(),
            uuid4(),
            uuid4(),
            now,
            now,
            None,
            None,
            None,
        )
        self.connection.execute.return_value.fetchall.side_effect = ([], [row])
        store = MemoryStore(self.database)

        self.assertEqual(store.list(page_size=1), MemoryPage((), None))
        exact = store.list(page_size=1)
        self.assertEqual(tuple(item.id for item in exact.items), (row[0],))
        self.assertIsNone(exact.next_cursor)

    def test_lifecycle_rejects_bad_types_before_opening_a_transaction(self) -> None:
        store = MemoryStore(self.database)

        for operation in (
            lambda: store.update("bad", MemoryPatch()),
            lambda: store.update(uuid4(), object()),
            lambda: store.disable("bad"),
            lambda: store.delete("bad"),
            lambda: store.supersede(
                uuid4(),
                MemoryCreate(
                    kind="fact",
                    scope="global",
                    content=" ",
                    source=MemorySource(uuid4(), uuid4(), uuid4()),
                ),
            ),
        ):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                operation()
        self.database.transaction.assert_not_called()

    def test_lifecycle_preserves_database_errors(self) -> None:
        error = OperationalError("database unavailable")

        for operation in (
            lambda store: store.update(uuid4(), MemoryPatch(kind="fact")),
            lambda store: store.disable(uuid4()),
            lambda store: store.delete(uuid4()),
            lambda store: store.supersede(
                uuid4(),
                MemoryCreate(
                    kind="fact",
                    scope="global",
                    content="new fact",
                    source=MemorySource(uuid4(), uuid4(), uuid4()),
                ),
            ),
        ):
            database = MagicMock()
            database.transaction.side_effect = error
            with self.subTest(operation=operation), self.assertRaises(
                OperationalError
            ) as caught:
                operation(MemoryStore(database))
            self.assertIs(caught.exception, error)


class MemoryEmbeddingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.connection = (
            self.database.connection.return_value.__enter__.return_value
        )
        self.transaction = (
            self.database.transaction.return_value.__enter__.return_value
        )

    def test_lists_only_stable_active_targets_missing_current_model(self) -> None:
        rows = (
            (uuid4(), "first", "a" * 64),
            (uuid4(), "second", "b" * 64),
        )
        self.connection.execute.return_value.fetchall.return_value = rows

        targets = MemoryStore(self.database).list_embedding_targets(
            " model-a ",
            10,
        )

        self.assertEqual(
            targets,
            tuple(MemoryEmbeddingTarget(*row) for row in rows),
        )
        self.connection.execute.assert_called_once_with(
            "SELECT id, content, fingerprint FROM memories "
            "WHERE status = 'active' "
            "AND (embedding IS NULL OR embedding_model <> %s) "
            "ORDER BY created_at, id LIMIT %s",
            ("model-a", 10),
        )

    def test_target_query_rejects_invalid_values_without_database_access(self) -> None:
        store = MemoryStore(self.database)
        invalid_values = (("", 1), (1, 1), ("model", 0), ("model", True))
        for model, limit in invalid_values:
            with self.subTest(model=model, limit=limit), self.assertRaises(
                MemoryValidationError
            ):
                store.list_embedding_targets(model, limit)  # type: ignore[arg-type]
        self.database.connection.assert_not_called()

    def test_saves_one_batch_and_bumps_epoch_once(self) -> None:
        targets = (
            MemoryEmbeddingTarget(uuid4(), "first", "a" * 64),
            MemoryEmbeddingTarget(uuid4(), "second", "b" * 64),
        )
        lock_one = MagicMock()
        lock_one.fetchone.return_value = ("active", targets[0].fingerprint)
        lock_two = MagicMock()
        lock_two.fetchone.return_value = ("active", targets[1].fingerprint)
        epoch = MagicMock()
        epoch.fetchone.return_value = (1,)
        self.transaction.execute.side_effect = (
            lock_one,
            lock_two,
            MagicMock(),
            MagicMock(),
            epoch,
        )

        saved = MemoryStore(self.database).save_embeddings(
            targets,
            (_embedding(1.0), _embedding(2.0)),
            "model-a",
        )

        self.assertEqual(saved, 2)
        calls = self.transaction.execute.call_args_list
        self.assertEqual(len(calls), 5)
        self.assertIn("%s::vector", calls[2].args[0])
        self.assertNotIn(calls[2].args[1][0], calls[2].args[0])
        self.assertEqual(calls[2].args[1][1:], ("model-a", targets[0].id))
        self.assertEqual(
            sum("UPDATE memory_epochs" in call.args[0] for call in calls),
            1,
        )

    def test_conflict_stops_before_updates_and_invalid_batch_stays_outside_txn(
        self,
    ) -> None:
        target = MemoryEmbeddingTarget(uuid4(), "fact", "a" * 64)
        conflict = MagicMock()
        conflict.fetchone.return_value = ("active", "b" * 64)
        self.transaction.execute.return_value = conflict

        with self.assertRaises(MemoryEmbeddingConflictError) as caught:
            MemoryStore(self.database).save_embeddings(
                (target,),
                (_embedding(),),
                "model-a",
            )
        self.assertEqual(caught.exception.memory_id, target.id)
        self.assertEqual(len(self.transaction.execute.call_args_list), 1)

        fresh_database = MagicMock()
        with self.assertRaises(MemoryValidationError):
            MemoryStore(fresh_database).save_embeddings(
                (target,),
                (),
                "model-a",
            )
        fresh_database.transaction.assert_not_called()

    def test_backfill_calls_network_between_target_query_and_save(self) -> None:
        target = MemoryEmbeddingTarget(uuid4(), "fact", "a" * 64)
        store = MagicMock()
        store.list_embedding_targets.side_effect = ((target,), ())
        store.save_embeddings.return_value = 1
        client = MagicMock()
        client.embed.return_value = (_embedding(),)

        self.assertEqual(
            embed_missing_memories(store, client, "model-a", batch_size=1),
            1,
        )
        client.embed.assert_called_once_with(("fact",))
        store.save_embeddings.assert_called_once_with(
            (target,),
            (_embedding(),),
            "model-a",
        )

    def test_create_and_supersede_require_a_complete_embedding_pair(self) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        data = MemoryCreate("fact", "global", "fact", source)
        store = MemoryStore(self.database)

        for operation in (
            lambda: store.create(data, embedding=_embedding()),
            lambda: store.create(data, embedding_model="model-a"),
            lambda: store.supersede(uuid4(), data, embedding=_embedding()),
            lambda: store.supersede(uuid4(), data, embedding_model="model-a"),
        ):
            with self.subTest(operation=operation), self.assertRaises(
                MemoryValidationError
            ):
                operation()
        self.database.transaction.assert_not_called()

    def test_update_clears_embedding_only_when_content_changes(self) -> None:
        memory_id = uuid4()
        source = MemorySource(uuid4(), uuid4(), uuid4())
        now = datetime.now(timezone.utc)

        def row(
            content: str,
            tags: list[str],
            fingerprint: str,
            model: str | None,
            embedded_at: datetime | None,
        ) -> tuple[object, ...]:
            return (
                memory_id,
                "fact",
                "global",
                "active",
                content,
                tags,
                1.0,
                fingerprint,
                source.conversation_id,
                source.user_message_id,
                source.assistant_message_id,
                now,
                now,
                None,
                model,
                embedded_at,
            )

        cases = (
            (
                MemoryPatch(tags=("kept",)),
                row("fact", ["kept"], "a" * 64, "model-a", now),
                False,
            ),
            (
                MemoryPatch(content="changed"),
                row(
                    "changed",
                    [],
                    memory_fingerprint("global", "changed"),
                    None,
                    None,
                ),
                True,
            ),
        )
        for patch, updated_row, clears_embedding in cases:
            database = MagicMock()
            connection = database.transaction.return_value.__enter__.return_value
            locked = MagicMock()
            locked.fetchone.return_value = row(
                "fact",
                [],
                memory_fingerprint("global", "fact"),
                "model-a",
                now,
            )
            updated = MagicMock()
            updated.fetchone.return_value = updated_row
            epoch = MagicMock()
            epoch.fetchone.return_value = (1,)
            connection.execute.side_effect = (locked, updated, epoch)

            result = MemoryStore(database).update(memory_id, patch)

            update_sql = connection.execute.call_args_list[1].args[0]
            self.assertEqual("embedding = NULL" in update_sql, clears_embedding)
            self.assertEqual(result.embedding_model, updated_row[14])
            self.assertEqual(result.embedded_at, updated_row[15])


class MemorySearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.connection = (
            self.database.connection.return_value.__enter__.return_value
        )
        self.now = datetime.now(timezone.utc)

    def row(self, *, distance: float | None = None) -> tuple[object, ...]:
        values: tuple[object, ...] = (
            uuid4(),
            "fact",
            "global",
            "active",
            "Uses 50%_C:\\tmp",
            ["python"],
            1.0,
            "a" * 64,
            uuid4(),
            uuid4(),
            uuid4(),
            self.now,
            self.now,
            None,
            "model-a",
            self.now,
        )
        return values if distance is None else values + (distance,)

    def test_keyword_search_escapes_wildcards_and_keeps_scope_in_sql(self) -> None:
        row = self.row()
        self.connection.execute.return_value.fetchall.return_value = (row,)
        conversation_id = uuid4()

        hits = MemoryStore(self.database).search_keyword(
            " 50%_C:\\tmp ",
            conversation_id,
            5,
        )

        self.assertEqual(hits, (MemorySearchHit(_memory_from_row(row)),))
        statement, parameters = self.connection.execute.call_args.args
        self.assertIn("status = 'active'", statement)
        self.assertIn("scope = 'global'", statement)
        self.assertIn("source_conversation_id = %s", statement)
        self.assertIn("ESCAPE E'\\\\'", statement)
        self.assertEqual(
            parameters,
            (
                conversation_id,
                "%50\\%\\_C:\\\\tmp%",
                "%50\\%\\_C:\\\\tmp%",
                "%50\\%\\_C:\\\\tmp%",
                5,
            ),
        )

    def test_vector_search_filters_before_exact_cosine_ordering(self) -> None:
        row = self.row(distance=0.25)
        self.connection.execute.return_value.fetchall.return_value = (row,)
        conversation_id = uuid4()

        hits = MemoryStore(self.database).search_vector(
            _embedding(1.0),
            " model-a ",
            conversation_id,
            3,
        )

        self.assertEqual(hits[0].distance, 0.25)
        statement, parameters = self.connection.execute.call_args.args
        self.assertIn("embedding <=> %s::vector AS distance", statement)
        self.assertIn("status = 'active' AND embedding IS NOT NULL", statement)
        self.assertIn("embedding_model = %s", statement)
        self.assertIn("ORDER BY distance, id", statement)
        self.assertEqual(parameters[1:], ("model-a", conversation_id, 3))

    def test_nearest_search_puts_kind_scope_and_conversation_in_sql(self) -> None:
        row = self.row(distance=0.05)
        cursor = self.connection.execute.return_value
        cursor.fetchone.side_effect = (None, row)
        store = MemoryStore(self.database)

        self.assertIsNone(
            store.find_nearest_active(
                _embedding(1.0),
                "model-a",
                "preference",
                "global",
                None,
            )
        )
        conversation_id = uuid4()
        hit = store.find_nearest_active(
            _embedding(1.0),
            "model-a",
            "fact",
            "conversation",
            conversation_id,
        )

        self.assertEqual(hit, MemorySearchHit(_memory_from_row(row[:-1]), 0.05))
        first_statement = self.connection.execute.call_args_list[0].args[0]
        second_statement, second_parameters = (
            self.connection.execute.call_args_list[1].args
        )
        self.assertNotIn("AND source_conversation_id = %s", first_statement)
        self.assertIn("AND source_conversation_id = %s", second_statement)
        self.assertEqual(
            second_parameters[1:],
            ("fact", "conversation", "model-a", conversation_id),
        )

    def test_search_rejects_invalid_values_before_database_access(self) -> None:
        store = MemoryStore(self.database)
        invalid_calls = (
            lambda: store.search_keyword(" ", uuid4(), 1),
            lambda: store.search_keyword("query", "bad", 1),
            lambda: store.search_keyword("query", uuid4(), True),
            lambda: store.search_vector((1.0,), "model-a", uuid4(), 1),
            lambda: store.find_nearest_active(
                _embedding(), "model-a", "bad", "global", None
            ),
            lambda: store.find_nearest_active(
                _embedding(), "model-a", "fact", "bad", None
            ),
            lambda: store.find_nearest_active(
                _embedding(), "model-a", "fact", "conversation", None
            ),
        )
        for call in invalid_calls:
            with self.subTest(call=call), self.assertRaises(MemoryValidationError):
                call()
        self.database.connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
