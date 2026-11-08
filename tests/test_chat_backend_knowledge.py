from __future__ import annotations

import math
import unittest
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
from unittest.mock import MagicMock, call, patch
from uuid import UUID, uuid4

from psycopg import OperationalError

from agents.chat_backend.embedding import (
    EMBEDDING_DIMENSIONS,
    EmbeddingContractError,
    EmbeddingRequestError,
)
from agents.chat_backend.knowledge import (
    IndexReport,
    KnowledgeChunk,
    KnowledgeChunkDraft,
    KnowledgeCursor,
    KnowledgeNotFoundError,
    KnowledgePage,
    KnowledgeIndexer,
    KnowledgeSource,
    KnowledgeSourceCreate,
    KnowledgeSearchHit,
    KnowledgeStore,
    KnowledgeValidationError,
    _PreparedKnowledgeIndex,
    _normalize_metadata,
    _normalize_text,
    _normalize_title,
    split_knowledge_text,
)


def source_row(
    source_id: UUID,
    created_at: datetime,
    *,
    title: str = "Guide",
    text: str = "Knowledge text",
    metadata: dict[str, object] | None = None,
) -> tuple[object, ...]:
    return (
        source_id,
        title,
        text,
        {} if metadata is None else metadata,
        "pending",
        None,
        created_at,
        created_at,
        None,
    )


def knowledge_source(text: str) -> KnowledgeSource:
    now = datetime.now(timezone.utc)
    return KnowledgeSource(
        uuid4(), "Guide", text, {}, "pending", None, now, now, None
    )


def vectors_for(texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
    return tuple(
        (float(ord(text[0])),) * EMBEDDING_DIMENSIONS for text in texts
    )


class KnowledgeChunkingTests(unittest.TestCase):
    def test_rejects_invalid_text_and_character_limit(self) -> None:
        for text in (None, "", " \n\t"):
            with self.subTest(text=text), self.assertRaises(
                KnowledgeValidationError
            ):
                split_knowledge_text(text)  # type: ignore[arg-type]

        for max_chars in (0, -1, True, 1.5):
            with self.subTest(max_chars=max_chars), self.assertRaises(
                KnowledgeValidationError
            ):
                split_knowledge_text("text", max_chars)  # type: ignore[arg-type]

    def test_merges_complete_paragraphs_and_preserves_source_slice(self) -> None:
        text = "\n \nalpha\nline two\n\n  \n\nbeta\n\n"

        drafts = split_knowledge_text(text, 40)

        start = text.index("alpha")
        end = text.index("beta") + len("beta")
        self.assertEqual(
            drafts,
            (KnowledgeChunkDraft(0, start, end, text[start:end]),),
        )
        self.assertEqual(drafts, split_knowledge_text(text, 40))

    def test_keeps_paragraphs_whole_until_the_next_one_does_not_fit(self) -> None:
        text = "alpha\n\nbeta\n\ncharlie"

        drafts = split_knowledge_text(text, 11)

        self.assertEqual(
            tuple(draft.content for draft in drafts),
            ("alpha\n\nbeta", "charlie"),
        )
        self.assertEqual(
            tuple((draft.start_char, draft.end_char) for draft in drafts),
            ((0, 11), (13, 20)),
        )

    def test_splits_long_paragraph_into_fixed_unicode_character_windows(self) -> None:
        for text, expected in (
            ("甲乙🙂丁", ("甲乙", "🙂丁")),
            ("甲乙🙂丁戊", ("甲乙", "🙂丁", "戊")),
        ):
            with self.subTest(text=text):
                drafts = split_knowledge_text(text, 2)
                self.assertEqual(
                    tuple(draft.content for draft in drafts), expected
                )

    def test_every_draft_has_continuous_ordinal_and_exact_ordered_range(self) -> None:
        text = "first\nline\n\nsecond paragraph\n\n尾段"

        drafts = split_knowledge_text(text, 7)

        self.assertEqual(
            tuple(draft.ordinal for draft in drafts),
            tuple(range(len(drafts))),
        )
        for previous, current in zip(drafts, drafts[1:]):
            self.assertLessEqual(previous.end_char, current.start_char)
        for draft in drafts:
            self.assertLessEqual(len(draft.content), 7)
            self.assertEqual(
                draft.content,
                text[draft.start_char : draft.end_char],
            )


class KnowledgeIndexerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MagicMock(spec=KnowledgeStore)
        self.client = MagicMock()

    def test_constructor_rejects_invalid_model_and_sizes(self) -> None:
        for model in (None, "", "   "):
            with self.subTest(model=model), self.assertRaises(
                KnowledgeValidationError
            ):
                KnowledgeIndexer(
                    self.store,
                    self.client,
                    model,  # type: ignore[arg-type]
                )

        for field_name, values in (
            ("chunk_chars", (0, -1, True, 1.5)),
            ("batch_size", (0, -1, True, 1.5)),
        ):
            for value in values:
                arguments = {field_name: value}
                with self.subTest(**arguments), self.assertRaises(
                    KnowledgeValidationError
                ):
                    KnowledgeIndexer(
                        self.store,
                        self.client,
                        "model",
                        **arguments,  # type: ignore[arg-type]
                    )

    def test_prepare_reads_source_and_embeds_in_ordered_64_plus_1_batches(self) -> None:
        contents = tuple(chr(0x4E00 + index) for index in range(65))
        source = knowledge_source("\n\n".join(contents))
        self.store.get_source.return_value = source
        self.client.embed.side_effect = lambda texts: vectors_for(tuple(texts))
        indexer = KnowledgeIndexer(
            self.store,
            self.client,
            "  embedding-model  ",
            chunk_chars=1,
        )

        prepared = indexer._prepare(source.id)

        self.assertIsInstance(prepared, _PreparedKnowledgeIndex)
        self.assertEqual(prepared.source_id, source.id)
        self.assertEqual(prepared.embedding_model, "embedding-model")
        self.assertEqual(
            tuple(draft.content for draft in prepared.drafts), contents
        )
        self.assertEqual(
            tuple(vector[0] for vector in prepared.vectors),
            tuple(float(ord(content)) for content in contents),
        )
        self.assertEqual(
            self.client.embed.call_args_list,
            [call(contents[:64]), call(contents[64:])],
        )
        self.store.get_source.assert_called_once_with(source.id)
        with self.assertRaises(FrozenInstanceError):
            prepared.embedding_model = "changed"  # type: ignore[misc]

    def test_second_batch_failure_returns_no_partial_preparation_or_write(self) -> None:
        contents = tuple(chr(0x4E00 + index) for index in range(65))
        source = knowledge_source("\n\n".join(contents))
        self.store.get_source.return_value = source
        error = EmbeddingRequestError("second batch failed")

        def embed(texts: tuple[str, ...]):
            if self.client.embed.call_count == 2:
                raise error
            return vectors_for(tuple(texts))

        self.client.embed.side_effect = embed

        with self.assertRaises(EmbeddingRequestError) as caught:
            KnowledgeIndexer(
                self.store,
                self.client,
                "model",
                chunk_chars=1,
            )._prepare(source.id)

        self.assertIs(caught.exception, error)
        self.assertEqual(self.store.method_calls, [call.get_source(source.id)])

    def test_prepare_rejects_a_batch_with_the_wrong_vector_count(self) -> None:
        source = knowledge_source("text")
        self.store.get_source.return_value = source
        indexer = KnowledgeIndexer(self.store, self.client, "model")

        for vectors in ((), vectors_for(("a", "b"))):
            with self.subTest(vector_count=len(vectors)):
                self.client.embed.return_value = vectors
                with self.assertRaises(EmbeddingContractError):
                    indexer._prepare(source.id)

    def test_source_lookup_failure_does_not_call_embedding(self) -> None:
        source_id = uuid4()
        error = KnowledgeNotFoundError(source_id)
        self.store.get_source.side_effect = error

        with self.assertRaises(KnowledgeNotFoundError) as caught:
            KnowledgeIndexer(self.store, self.client, "model")._prepare(
                source_id
            )

        self.assertIs(caught.exception, error)
        self.client.embed.assert_not_called()

    def test_reindex_publishes_only_the_complete_prepared_result(self) -> None:
        source_id = uuid4()
        now = datetime.now(timezone.utc)
        report = IndexReport(source_id, 1, "model", now)
        indexer = KnowledgeIndexer(self.store, self.client, "model")
        prepared = _PreparedKnowledgeIndex(
            source_id,
            (KnowledgeChunkDraft(0, 0, 4, "text"),),
            vectors_for(("text",)),
            "model",
        )
        self.store.replace_chunks.return_value = report

        with patch.object(indexer, "_prepare", return_value=prepared):
            self.assertEqual(indexer.reindex(source_id), report)

        self.store.replace_chunks.assert_called_once_with(
            source_id,
            prepared.drafts,
            prepared.vectors,
            prepared.embedding_model,
        )


class KnowledgeValidationTests(unittest.TestCase):
    def test_normalizes_title_text_and_json_metadata(self) -> None:
        self.assertEqual(_normalize_title("  Guide  "), "Guide")
        self.assertEqual(
            _normalize_text("e\u0301\r\nfirst\rsecond"),
            "é\nfirst\nsecond",
        )
        self.assertEqual(
            _normalize_metadata({"tags": ("a", "b"), "enabled": True}),
            {"tags": ["a", "b"], "enabled": True},
        )

    def test_rejects_invalid_title_and_text_boundaries(self) -> None:
        for title in (None, "", "   ", "x" * 201):
            with self.subTest(title=title), self.assertRaises(
                KnowledgeValidationError
            ):
                _normalize_title(title)

        for text in (None, "", " \r\n\t", "x" * 200_001):
            with self.subTest(text_length=getattr(text, "__len__", lambda: -1)()):
                with self.assertRaises(KnowledgeValidationError):
                    _normalize_text(text)

        self.assertEqual(len(_normalize_title("x" * 200)), 200)
        self.assertEqual(len(_normalize_text("x" * 200_000)), 200_000)

    def test_metadata_must_be_a_bounded_json_object(self) -> None:
        invalid = (
            [],
            {1: "not a JSON object key"},
            {"number": math.nan},
            {"value": object()},
            {"text": "é" * 8192},
        )
        for metadata in invalid:
            with self.subTest(metadata_type=type(metadata).__name__):
                with self.assertRaises(KnowledgeValidationError):
                    _normalize_metadata(metadata)

        self.assertEqual(
            _normalize_metadata({"text": "é" * 8000})["text"],
            "é" * 8000,
        )

    def test_public_values_are_frozen_and_chunk_values_exclude_embedding(self) -> None:
        now = datetime.now(timezone.utc)
        source = KnowledgeSource(
            uuid4(), "Guide", "text", {}, "pending", None, now, now, None
        )
        draft = KnowledgeChunkDraft(0, 0, 4, "text")
        chunk = KnowledgeChunk(uuid4(), source.id, 0, 0, 4, "text", "model", now)

        with self.assertRaises(FrozenInstanceError):
            source.title = "changed"  # type: ignore[misc]
        self.assertEqual(
            (draft.content, chunk.content, chunk.embedding_model),
            ("text", "text", "model"),
        )
        self.assertNotIn("embedding", KnowledgeChunk.__dataclass_fields__)

        report = IndexReport(source.id, 1, "model", now)
        hit = KnowledgeSearchHit(
            source.id,
            chunk.id,
            source.title,
            chunk.content,
            chunk.ordinal,
            chunk.start_char,
            chunk.end_char,
        )
        with self.assertRaises(FrozenInstanceError):
            report.chunk_count = 2  # type: ignore[misc]
        with self.assertRaises(FrozenInstanceError):
            hit.source_title = "changed"  # type: ignore[misc]


class KnowledgeStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database = MagicMock()
        self.connection = self.database.connection.return_value.__enter__.return_value
        self.transaction = (
            self.database.transaction.return_value.__enter__.return_value
        )
        self.store = KnowledgeStore(self.database)

    def test_create_normalizes_before_transaction_and_bumps_epoch_once(self) -> None:
        source_id = uuid4()
        now = datetime.now(timezone.utc)
        row = source_row(
            source_id,
            now,
            title="Guide",
            text="é\nline",
            metadata={"tags": ["python"]},
        )
        inserted = MagicMock()
        inserted.fetchone.return_value = row
        bumped = MagicMock()
        bumped.fetchone.return_value = (1,)
        self.transaction.execute.side_effect = (inserted, bumped)

        with patch(
            "agents.chat_backend.knowledge.uuid4", return_value=source_id
        ):
            result = self.store.create_source(
                KnowledgeSourceCreate(
                    "  Guide  ",
                    "e\u0301\r\nline",
                    {"tags": ("python",)},
                )
            )

        self.assertEqual(result, KnowledgeSource(*row))
        parameters = self.transaction.execute.call_args_list[0].args[1]
        self.assertEqual(parameters[0], source_id)
        self.assertEqual(parameters[1:3], ("Guide", "é\nline"))
        self.assertEqual(parameters[3].obj, {"tags": ["python"]})
        self.assertEqual(self.transaction.execute.call_count, 2)

    def test_invalid_create_never_opens_transaction(self) -> None:
        with self.assertRaises(KnowledgeValidationError):
            self.store.create_source(KnowledgeSourceCreate(" ", "text"))
        self.database.transaction.assert_not_called()

    def test_create_epoch_failure_propagates_inside_same_transaction(self) -> None:
        now = datetime.now(timezone.utc)
        inserted = MagicMock()
        inserted.fetchone.return_value = source_row(uuid4(), now)
        missing_epoch = MagicMock()
        missing_epoch.fetchone.return_value = None
        self.transaction.execute.side_effect = (inserted, missing_epoch)

        with self.assertRaisesRegex(RuntimeError, "knowledge epoch is missing"):
            self.store.create_source(KnowledgeSourceCreate("Guide", "text"))

    def test_get_returns_database_fact_or_not_found(self) -> None:
        source_id = uuid4()
        row = source_row(source_id, datetime.now(timezone.utc))
        self.connection.execute.return_value.fetchone.return_value = row

        self.assertEqual(self.store.get_source(source_id), KnowledgeSource(*row))

        self.connection.execute.return_value.fetchone.return_value = None
        missing_id = uuid4()
        with self.assertRaises(KnowledgeNotFoundError) as caught:
            self.store.get_source(missing_id)
        self.assertEqual(caught.exception.source_id, missing_id)

    def test_list_uses_stable_tuple_cursor_and_limit_plus_one(self) -> None:
        now = datetime.now(timezone.utc)
        ids = (uuid4(), uuid4(), uuid4())
        rows = [source_row(source_id, now) for source_id in ids]
        self.connection.execute.return_value.fetchall.return_value = rows
        cursor = KnowledgeCursor(now, uuid4())

        page = self.store.list_sources(2, cursor)

        statement, parameters = self.connection.execute.call_args.args
        self.assertIn("(created_at, id) < (%s, %s)", statement)
        self.assertIn("ORDER BY created_at DESC, id DESC", statement)
        self.assertEqual(parameters, (cursor.created_at, cursor.id, 3))
        self.assertEqual([item.id for item in page.items], list(ids[:2]))
        self.assertEqual(page.next_cursor, KnowledgeCursor(now, ids[1]))

    def test_list_rejects_invalid_limit_and_cursor_before_database_access(self) -> None:
        now = datetime.now(timezone.utc)
        invalid = (
            (0, None),
            (101, None),
            (True, None),
            (1, "bad"),
            (1, KnowledgeCursor("bad", uuid4())),
            (1, KnowledgeCursor(now, "bad")),
        )
        for limit, cursor in invalid:
            with self.subTest(limit=limit, cursor=cursor):
                with self.assertRaises(KnowledgeValidationError):
                    self.store.list_sources(limit, cursor)
        self.database.connection.assert_not_called()

    def test_empty_and_exact_pages_have_no_cursor(self) -> None:
        self.connection.execute.return_value.fetchall.return_value = []
        self.assertEqual(self.store.list_sources(2), KnowledgePage((), None))

        row = source_row(uuid4(), datetime.now(timezone.utc))
        self.connection.execute.return_value.fetchall.return_value = [row]
        self.assertIsNone(self.store.list_sources(1).next_cursor)

    def test_delete_and_epoch_share_one_transaction(self) -> None:
        deleted = MagicMock()
        deleted.fetchone.return_value = (uuid4(),)
        bumped = MagicMock()
        bumped.fetchone.return_value = (4,)
        self.transaction.execute.side_effect = (deleted, bumped)

        self.assertIsNone(self.store.delete_source(uuid4()))
        self.assertEqual(self.transaction.execute.call_count, 2)

        missing = MagicMock()
        missing.fetchone.return_value = None
        self.transaction.execute.reset_mock(side_effect=True)
        self.transaction.execute.side_effect = (missing,)
        missing_id = uuid4()
        with self.assertRaises(KnowledgeNotFoundError):
            self.store.delete_source(missing_id)
        self.assertEqual(self.transaction.execute.call_count, 1)

    def test_get_epoch_returns_nonnegative_fact_and_rejects_bad_state(self) -> None:
        self.connection.execute.return_value.fetchone.return_value = (7,)
        self.assertEqual(self.store.get_epoch(), 7)

        for row in (None, (-1,), (True,)):
            with self.subTest(row=row):
                self.connection.execute.return_value.fetchone.return_value = row
                with self.assertRaises(RuntimeError):
                    self.store.get_epoch()

    def test_database_errors_are_not_replaced(self) -> None:
        error = OperationalError("database unavailable")
        self.database.connection.side_effect = error

        with self.assertRaises(OperationalError) as caught:
            self.store.get_source(uuid4())
        self.assertIs(caught.exception, error)

    def test_source_ids_must_be_uuid_values(self) -> None:
        for operation in (self.store.get_source, self.store.delete_source):
            with self.subTest(operation=operation.__name__):
                with self.assertRaises(KnowledgeValidationError):
                    operation("bad")

    def test_replace_chunks_locks_rechecks_and_publishes_one_epoch(self) -> None:
        source_id = uuid4()
        chunk_ids = (uuid4(), uuid4())
        now = datetime.now(timezone.utc)
        drafts = (
            KnowledgeChunkDraft(0, 0, 3, "abc"),
            KnowledgeChunkDraft(1, 3, 6, "def"),
        )
        vectors = vectors_for(("abc", "def"))
        locked = MagicMock()
        locked.fetchone.return_value = ("abcdef",)
        deleted = MagicMock()
        inserted = (MagicMock(), MagicMock())
        updated = MagicMock()
        updated.fetchone.return_value = (now,)
        bumped = MagicMock()
        bumped.fetchone.return_value = (8,)
        self.transaction.execute.side_effect = (
            locked,
            deleted,
            *inserted,
            updated,
            bumped,
        )

        with patch(
            "agents.chat_backend.knowledge.uuid4", side_effect=chunk_ids
        ):
            report = self.store.replace_chunks(
                source_id, drafts, vectors, "  model-a  "
            )

        self.assertEqual(report, IndexReport(source_id, 2, "model-a", now))
        statements = [item.args[0] for item in self.transaction.execute.call_args_list]
        self.assertIn("FOR UPDATE", statements[0])
        self.assertTrue(statements[1].startswith("DELETE FROM knowledge_chunks"))
        self.assertEqual(sum("INSERT INTO knowledge_chunks" in sql for sql in statements), 2)
        self.assertIn("%s::vector", statements[2])
        self.assertIn("status = 'indexed'", statements[-2])
        self.assertIn("UPDATE knowledge_epoch", statements[-1])

    def test_replace_chunks_rejects_invalid_batch_before_transaction(self) -> None:
        source_id = uuid4()
        valid_draft = KnowledgeChunkDraft(0, 0, 4, "text")
        valid_vector = vectors_for(("text",))[0]
        invalid = (
            ((), (), "model"),
            ((valid_draft,), (), "model"),
            ((KnowledgeChunkDraft(1, 0, 4, "text"),), (valid_vector,), "model"),
            ((KnowledgeChunkDraft(0, 0, 3, "text"),), (valid_vector,), "model"),
            ((valid_draft,), ((math.nan,) * EMBEDDING_DIMENSIONS,), "model"),
            ((valid_draft,), (valid_vector,), " "),
        )
        for drafts, vectors, model in invalid:
            with self.subTest(drafts=drafts, model=model):
                with self.assertRaises(KnowledgeValidationError):
                    self.store.replace_chunks(source_id, drafts, vectors, model)
        self.database.transaction.assert_not_called()

    def test_replace_chunks_stops_before_delete_when_source_changed(self) -> None:
        locked = MagicMock()
        locked.fetchone.return_value = ("changed",)
        self.transaction.execute.side_effect = (locked,)

        with self.assertRaises(KnowledgeValidationError):
            self.store.replace_chunks(
                uuid4(),
                (KnowledgeChunkDraft(0, 0, 4, "text"),),
                vectors_for(("text",)),
                "model",
            )

        self.assertEqual(self.transaction.execute.call_count, 1)

    def test_search_keyword_filters_indexed_sources_and_escapes_wildcards(self) -> None:
        source_id = uuid4()
        chunk_id = uuid4()
        self.connection.execute.return_value.fetchall.return_value = [
            (source_id, chunk_id, "Guide", "content", 0, 0, 7)
        ]

        hits = self.store.search_keyword("  50%_off  ", 3, (source_id,))

        self.assertEqual(
            hits,
            (KnowledgeSearchHit(source_id, chunk_id, "Guide", "content", 0, 0, 7),),
        )
        statement, parameters = self.connection.execute.call_args.args
        self.assertIn("s.status = 'indexed'", statement)
        self.assertIn("s.id = ANY(%s::uuid[])", statement)
        self.assertLess(statement.index("s.id = ANY"), statement.index("ORDER BY"))
        self.assertIn("ESCAPE E'\\\\'", statement)
        self.assertEqual(parameters, ("%50\\%\\_off%", "%50\\%\\_off%", [source_id], 3))

    def test_search_vector_filters_model_before_exact_distance_order(self) -> None:
        source_id = uuid4()
        chunk_id = uuid4()
        self.connection.execute.return_value.fetchall.return_value = [
            (source_id, chunk_id, "Guide", "content", 0, 0, 7, 0.25)
        ]

        hits = self.store.search_vector(
            _embedding := (0.0,) * EMBEDDING_DIMENSIONS,
            " model-a ",
            4,
            (source_id,),
        )

        self.assertEqual(hits[0].distance, 0.25)
        statement, parameters = self.connection.execute.call_args.args
        self.assertIn("s.status = 'indexed'", statement)
        self.assertIn("s.embedding_model = %s", statement)
        self.assertIn("c.embedding_model = s.embedding_model", statement)
        self.assertLess(statement.index("s.id = ANY"), statement.index("ORDER BY"))
        self.assertIn("ORDER BY distance, c.id", statement)
        self.assertEqual(parameters[0], "[" + ",".join("0.0" for _ in _embedding) + "]")
        self.assertEqual(parameters[1:], ("model-a", [source_id], 4))

    def test_empty_source_filter_returns_without_database_access(self) -> None:
        self.assertEqual(self.store.search_keyword("query", 3, ()), ())
        self.assertEqual(
            self.store.search_vector(
                (0.0,) * EMBEDDING_DIMENSIONS, "model", 3, ()
            ),
            (),
        )
        self.database.connection.assert_not_called()


if __name__ == "__main__":
    unittest.main()
