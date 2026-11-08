from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from psycopg import Error, OperationalError, connect, sql
from psycopg.errors import CheckViolation, ForeignKeyViolation, UniqueViolation
from psycopg.types.json import Jsonb

from agents import (
    Agent,
    AgentMemory,
    FunctionTool,
    Runner,
    ToolArgument,
    ToolCall,
    ToolRegistry,
    ToolSpec,
)
from agents.chat_backend import (
    ChatRunError,
    ChatService,
    ConversationCursor,
    ConversationNotFoundError,
    ConversationPatch,
    KnowledgeChunkDraft,
    KnowledgeNotFoundError,
    KnowledgeSourceCreate,
    KnowledgeStore,
    MessageStore,
    PostgresSession,
)
from agents.contracts import ChatMessage, ModelResponse
from agents.run_config import RunConfig
from agents.chat_backend.database import Database
from agents.chat_backend.embedding import (
    EMBEDDING_DIMENSIONS,
    embedding_vector_literal,
)
from agents.chat_backend.conversation import ConversationStore
from agents.chat_backend.migration import MigrationError, MigrationRunner
from agents.chat_backend.model_profiles import ModelProfile
from agents.chat_backend.planning_store import (
    ActivePlanningRunError,
    CorruptPlanningRunError,
    PlanStore,
    PlanningConflictError,
)
from agents.chat_backend.planning_service import PlanningService
from agents.chat_backend.memory import (
    MemoryCreate,
    MemoryDuplicateError,
    MemoryEmbeddingConflictError,
    MemoryNotFoundError,
    MemoryPatch,
    MemorySource,
    MemoryStore,
    MemorySupersedeError,
    MemoryValidationError,
)
from agents.planning import (
    PlanDefinition,
    PlanExecution,
    PlanStepSpec,
    plan_definition_to_dict,
)
from agents.run_state import (
    ApprovalSnapshot,
    RunStateSnapshot,
    run_state_snapshot_to_dict,
)
from agents.task_events import task_event_to_dict
from agents.task_state import TaskStatus
from agents.verification import VerificationResult
from agents.workspace_manifest import WorkspaceManifest


_POSTGRES_DSN = os.getenv("MYAGENT_TEST_POSTGRES_DSN")


class RecordingModel:
    def __init__(self, actions) -> None:
        self.actions = iter(actions)
        self.message_batches = []

    def decide(self, messages, tool_specs):
        self.message_batches.append(list(messages))
        action = next(self.actions, None)
        if isinstance(action, Exception):
            raise action
        return action


def echo_tool() -> FunctionTool:
    return FunctionTool(
        spec=ToolSpec(
            name="echo_text",
            description="Return the same text.",
            arguments=[
                ToolArgument(
                    name="text",
                    description="Input text.",
                    schema={"type": "string"},
                )
            ],
            returns="string",
        ),
        handler=lambda text: text,
    )


class MigrationRunnerTests(unittest.TestCase):
    def test_project_migrations_include_conversation_management(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )

        migrations = MigrationRunner(Mock(), migrations_path)._discover_migrations()

        self.assertEqual(migrations[-4].version, "007")
        self.assertIn("ADD COLUMN title", migrations[-4].sql)
        self.assertIn("ADD COLUMN status", migrations[-4].sql)
        self.assertIn("New conversation", migrations[-4].sql)
        self.assertEqual(migrations[-3].version, "008")
        self.assertIn("ADD COLUMN run_trace", migrations[-3].sql)
        self.assertEqual(migrations[-2].version, "009")
        self.assertIn("ADD COLUMN mode", migrations[-2].sql)
        self.assertIn("CREATE TABLE planning_runs", migrations[-2].sql)
        self.assertIn(
            "CREATE UNIQUE INDEX planning_runs_active_conversation_key",
            migrations[-2].sql,
        )
        self.assertIn(
            "CREATE INDEX planning_runs_recoverable_running_idx",
            migrations[-2].sql,
        )
        self.assertIn("AND last_error IS NOT NULL", migrations[-2].sql)
        self.assertEqual(migrations[-1].version, "010")
        self.assertIn("'paused'", migrations[-1].sql)
        self.assertIn("planning_runs_state_check", migrations[-1].sql)

    def test_discovers_valid_files_in_version_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = b"SELECT 1;\n"
            second = b"SELECT 1;\r\n"
            (root / "002_second_table.sql").write_bytes(second)
            (root / "001_first_table.sql").write_bytes(first)
            (root / "notes.txt").write_text("ignored", encoding="utf-8")
            (root / "003_ignored.sql").mkdir()

            database = Mock()
            migrations = MigrationRunner(database, root)._discover_migrations()

        self.assertEqual(tuple(item.version for item in migrations), ("001", "002"))
        self.assertEqual(migrations[1].sql, second.decode("utf-8"))
        self.assertEqual(migrations[1].checksum, hashlib.sha256(second).hexdigest())
        self.assertNotEqual(migrations[0].checksum, migrations[1].checksum)
        self.assertEqual(len(migrations[1].checksum), 64)
        self.assertEqual(database.mock_calls, [])

    def test_rejects_invalid_name_and_duplicate_version(self) -> None:
        cases = {
            "invalid migration filename: 1_bad.sql": {
                "1_bad.sql": b"SELECT 1;",
            },
            "invalid migration filename: 001_Bad.sql": {
                "001_Bad.sql": b"SELECT 1;",
            },
            "invalid migration filename: 001_bad-name.sql": {
                "001_bad-name.sql": b"SELECT 1;",
            },
            "invalid migration filename: 001_bad.SQL": {
                "001_bad.SQL": b"SELECT 1;",
            },
            "duplicate migration version 001: 001_second.sql": {
                "001_first.sql": b"SELECT 1;",
                "001_second.sql": b"SELECT 2;",
            },
        }
        for message, files in cases.items():
            with (
                self.subTest(message=message),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                for name, contents in files.items():
                    (root / name).write_bytes(contents)

                with self.assertRaisesRegex(MigrationError, message):
                    MigrationRunner(Mock(), root)._discover_migrations()

    def test_rejects_empty_and_non_utf8_files(self) -> None:
        cases = {
            "migration file is empty: 001_empty.sql": {
                "001_empty.sql": b" \r\n\t"
            },
            "migration file is not UTF-8: 001_invalid.sql": {
                "001_invalid.sql": b"\xff"
            },
        }
        for message, files in cases.items():
            with (
                self.subTest(message=message),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                for name, contents in files.items():
                    (root / name).write_bytes(contents)

                with self.assertRaisesRegex(MigrationError, message):
                    MigrationRunner(Mock(), root)._discover_migrations()

    def test_reports_unreadable_directory_and_file(self) -> None:
        missing = Path("missing-migrations-for-lesson-07")
        with self.assertRaisesRegex(MigrationError, "cannot read migrations directory"):
            MigrationRunner(Mock(), missing)._discover_migrations()

        with tempfile.TemporaryDirectory() as directory:
            migration = Path(directory) / "001_first.sql"
            migration.write_text("SELECT 1;", encoding="utf-8")
            with patch.object(Path, "read_bytes", side_effect=PermissionError):
                with self.assertRaisesRegex(
                    MigrationError, "cannot read migration file: 001_first.sql"
                ):
                    MigrationRunner(Mock(), directory)._discover_migrations()

    def test_database_error_keeps_cause_without_leaking_detail(self) -> None:
        database = Mock()
        error = OperationalError("secret database detail")
        database.connection.side_effect = error

        with self.assertRaises(MigrationError) as caught:
            MigrationRunner(database, "unused").current_version()

        self.assertEqual(str(caught.exception), "cannot read migration history")
        self.assertNotIn("secret", str(caught.exception))
        self.assertIs(caught.exception.__cause__, error)


@unittest.skipUnless(_POSTGRES_DSN, "requires MYAGENT_TEST_POSTGRES_DSN")
class MigrationRunnerPostgresTests(unittest.TestCase):
    def setUp(self) -> None:
        assert _POSTGRES_DSN is not None
        parts = urlsplit(_POSTGRES_DSN)
        self.database_name = f"myagent_lesson08_{uuid4().hex}"
        self.admin_dsn = urlunsplit(parts._replace(path="/postgres"))
        self.test_dsn = urlunsplit(parts._replace(path=f"/{self.database_name}"))

        with connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(
                    sql.Identifier(self.database_name)
                )
            )
        self.addCleanup(self._drop_database)

        self.database = Database(self.test_dsn)
        self.database.open()
        self.addCleanup(self.database.close)

    def _drop_database(self) -> None:
        with connect(self.admin_dsn, autocommit=True) as connection:
            connection.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(self.database_name)
                )
            )

    def _upgrade(self, database: Database | None = None) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(database or self.database, migrations_path).upgrade()

    def _profile(self, api_key: str = "planning-test-key") -> ModelProfile:
        return ModelProfile(
            id=uuid4(),
            name="Planning Gate",
            provider="openai",
            protocol="responses",
            base_url="https://api.openai.com/v1",
            model="planning-test-model",
            api_key=api_key,
            is_active=True,
        )

    def _manifest(
        self,
        env_value: str = "planning-test-env",
    ) -> WorkspaceManifest:
        return WorkspaceManifest(
            root=Path.cwd(),
            allowed_paths=("src", "tests"),
            env={"PLANNING_GATE_SECRET": env_value},
        )

    def _definition(self) -> PlanDefinition:
        return PlanDefinition(
            objective="Persist and recover a plan",
            steps=(
                PlanStepSpec(
                    "work",
                    "Work",
                    "Complete the work",
                    "Work is complete",
                ),
                PlanStepSpec(
                    "verify",
                    "Verify",
                    "Run final verification",
                    "Verification passes",
                    ("work",),
                    True,
                ),
            ),
        )

    def _create_submitted(
        self,
        database: Database | None = None,
    ) -> tuple[object, PlanStore, object]:
        target = database or self.database
        conversation = ConversationStore(target).create("Planning Gate")
        store = PlanStore(target)
        draft = store.create_draft(
            conversation.id,
            self._definition().objective,
            model_profile=self._profile(),
            manifest=self._manifest(),
        )
        submitted = store.submit_for_approval(
            draft.id,
            draft.row_revision,
            self._definition(),
        )
        return conversation, store, submitted

    def test_real_postgres_migration_flow(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        runner = MigrationRunner(self.database, migrations_path)

        self.assertIsNone(runner.current_version())
        self.assertEqual(
            runner.upgrade(),
            (
                "001",
                "002",
                "003",
                "004",
                "005",
                "006",
                "007",
                "008",
                "009",
                "010",
            ),
        )
        self.assertEqual(runner.current_version(), "010")
        self.assertEqual(runner.upgrade(), ())

        with self.database.connection() as connection:
            extension_exists = connection.execute(
                "SELECT EXISTS ("
                "SELECT FROM pg_extension WHERE extname = %s"
                ")",
                ("vector",),
            ).fetchone()[0]
            history = connection.execute(
                "SELECT version, length(checksum), applied_at IS NOT NULL "
                "FROM schema_migrations WHERE version = '010'"
            ).fetchone()
        self.assertTrue(extension_exists)
        self.assertEqual(history, ("010", 64, True))

        original_first = (
            migrations_path / "001_vector_extension.sql"
        ).read_bytes()
        original_second = (
            migrations_path / "002_conversations_and_messages.sql"
        ).read_bytes()
        original_third = (
            migrations_path / "003_long_term_memories.sql"
        ).read_bytes()
        original_fourth = (
            migrations_path / "004_memory_supersedes.sql"
        ).read_bytes()
        original_fifth = (
            migrations_path / "005_memory_embeddings.sql"
        ).read_bytes()
        original_sixth = (
            migrations_path / "006_knowledge_sources_and_chunks.sql"
        ).read_bytes()
        original_seventh = (
            migrations_path / "007_conversation_management.sql"
        ).read_bytes()
        original_eighth = (
            migrations_path / "008_message_run_trace.sql"
        ).read_bytes()
        original_ninth = (
            migrations_path / "009_planning_runs.sql"
        ).read_bytes()
        original_tenth = (
            migrations_path / "010_planning_execution_control.sql"
        ).read_bytes()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "001_vector_extension.sql"
            first.write_bytes(original_first)
            (root / "002_conversations_and_messages.sql").write_bytes(
                original_second
            )
            (root / "003_long_term_memories.sql").write_bytes(original_third)
            (root / "004_memory_supersedes.sql").write_bytes(original_fourth)
            (root / "005_memory_embeddings.sql").write_bytes(original_fifth)
            (root / "006_knowledge_sources_and_chunks.sql").write_bytes(
                original_sixth
            )
            (root / "007_conversation_management.sql").write_bytes(
                original_seventh
            )
            (root / "008_message_run_trace.sql").write_bytes(original_eighth)
            (root / "009_planning_runs.sql").write_bytes(original_ninth)
            (root / "010_planning_execution_control.sql").write_bytes(
                original_tenth
            )
            (root / "011_failure.sql").write_text(
                "CREATE TABLE lesson08_partial (id integer);\n"
                "SELECT * FROM lesson08_missing;\n",
                encoding="utf-8",
            )
            failing_runner = MigrationRunner(self.database, root)

            with self.assertRaisesRegex(
                MigrationError, "cannot apply migration 011: 011_failure.sql"
            ) as caught:
                failing_runner.upgrade()
            self.assertIsInstance(caught.exception.__cause__, Error)

            with self.database.connection() as connection:
                partial_table = connection.execute(
                    "SELECT to_regclass('lesson08_partial')"
                ).fetchone()[0]
                failed_history = connection.execute(
                    "SELECT count(*) FROM schema_migrations WHERE version = '011'"
                ).fetchone()[0]
            self.assertIsNone(partial_table)
            self.assertEqual(failed_history, 0)
            self.assertEqual(failing_runner.current_version(), "010")
            self.assertEqual(self.database.health().status, "ready")

            first.write_bytes(original_first + b"\n")
            with self.assertRaisesRegex(MigrationError, "checksum mismatch"):
                failing_runner.upgrade()

            first.unlink()
            with self.assertRaisesRegex(MigrationError, "file is missing"):
                failing_runner.upgrade()

    def test_planning_010_upgrades_existing_009_without_changing_checksum(
        self,
    ) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        ninth = (migrations_path / "009_planning_runs.sql").read_bytes()
        tenth = (
            migrations_path / "010_planning_execution_control.sql"
        ).read_bytes()
        with self.database.connection() as connection:
            connection.execute("CREATE TABLE conversations (id uuid PRIMARY KEY)")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "009_planning_runs.sql").write_bytes(ninth)
            runner = MigrationRunner(self.database, root)
            self.assertEqual(runner.upgrade(), ("009",))
            with self.database.connection() as connection:
                checksum_before = connection.execute(
                    "SELECT checksum FROM schema_migrations WHERE version = '009'"
                ).fetchone()[0]

            (root / "010_planning_execution_control.sql").write_bytes(tenth)
            self.assertEqual(runner.upgrade(), ("010",))
            self.assertEqual(runner.upgrade(), ())

        running_conversation = uuid4()
        paused_conversation = uuid4()
        superseded_conversation = uuid4()
        running_id = uuid4()
        with self.database.connection() as connection:
            checksum_after = connection.execute(
                "SELECT checksum FROM schema_migrations WHERE version = '009'"
            ).fetchone()[0]
            self.assertEqual(checksum_before, hashlib.sha256(ninth).hexdigest())
            self.assertEqual(checksum_after, checksum_before)

            connection.execute(
                "INSERT INTO conversations (id) VALUES (%s), (%s), (%s)",
                (
                    running_conversation,
                    paused_conversation,
                    superseded_conversation,
                ),
            )
            connection.execute(
                "INSERT INTO planning_runs ("
                "id, conversation_id, version, status, objective, definition, "
                "task_events, model_snapshot, workspace_snapshot"
                ") VALUES (%s, %s, 1, 'running', 'Run', %s, %s, %s, %s)",
                (
                    running_id,
                    running_conversation,
                    Jsonb({}),
                    Jsonb([{}]),
                    Jsonb({}),
                    Jsonb({}),
                ),
            )
            connection.execute(
                "INSERT INTO planning_runs ("
                "id, conversation_id, version, status, objective, last_error, "
                "definition, task_events, model_snapshot, workspace_snapshot"
                ") VALUES (%s, %s, 1, 'paused', 'Pause', %s, %s, %s, %s, %s)",
                (
                    uuid4(),
                    paused_conversation,
                    "planning model step failed",
                    Jsonb({}),
                    Jsonb([{}]),
                    Jsonb({}),
                    Jsonb({}),
                ),
            )
            with self.assertRaises(UniqueViolation):
                connection.execute(
                    "INSERT INTO planning_runs ("
                    "id, conversation_id, version, status, objective, "
                    "model_snapshot, workspace_snapshot"
                    ") VALUES (%s, %s, 2, 'drafting', 'Second', %s, %s)",
                    (
                        uuid4(),
                        paused_conversation,
                        Jsonb({}),
                        Jsonb({}),
                    ),
                )
            connection.execute(
                "INSERT INTO planning_runs ("
                "id, conversation_id, version, status, objective, definition, "
                "task_events, model_snapshot, workspace_snapshot"
                ") VALUES (%s, %s, 1, 'superseded', 'Old', %s, %s, %s, %s)",
                (
                    uuid4(),
                    superseded_conversation,
                    Jsonb({}),
                    Jsonb([{}]),
                    Jsonb({}),
                    Jsonb({}),
                ),
            )
            with self.assertRaises(CheckViolation):
                connection.execute(
                    "UPDATE planning_runs SET last_error = NULL "
                    "WHERE conversation_id = %s",
                    (paused_conversation,),
                )

            recoverable = connection.execute(
                "SELECT id FROM planning_runs WHERE status = 'running' "
                "ORDER BY updated_at, id"
            ).fetchall()
            constraints = {
                row[0]
                for row in connection.execute(
                    "SELECT conname FROM pg_constraint "
                    "WHERE conrelid = 'planning_runs'::regclass"
                ).fetchall()
            }
            active_index = connection.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname = 'planning_runs_active_conversation_key'"
            ).fetchone()[0]
            recoverable_index = connection.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname = 'planning_runs_recoverable_running_idx'"
            ).fetchone()[0]

        self.assertEqual(recoverable, [(running_id,)])
        self.assertIn("planning_runs_state_check", constraints)
        self.assertNotIn("planning_runs_check1", constraints)
        self.assertIn("'paused'::text", active_index)
        self.assertIn("status = 'running'::text", recoverable_index)

    def test_planning_010_persists_running_and_paused_across_pools(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        with self.database.connection() as connection:
            connection.execute("SET TIME ZONE 'UTC'")
            connection.execute(
                sql.SQL("ALTER DATABASE {} SET timezone TO 'UTC'").format(
                    sql.Identifier(self.database_name)
                )
            )
            connection.execute(
                "CREATE TABLE conversations ("
                "id uuid PRIMARY KEY, "
                "status text NOT NULL DEFAULT 'active'"
                ")"
            )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in (
                "009_planning_runs.sql",
                "010_planning_execution_control.sql",
            ):
                (root / name).write_bytes((migrations_path / name).read_bytes())
            self.assertEqual(
                MigrationRunner(self.database, root).upgrade(),
                ("009", "010"),
            )

        conversation_id = uuid4()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO conversations (id) VALUES (%s)",
                (conversation_id,),
            )

        store = PlanStore(self.database)
        draft = store.create_draft(
            conversation_id,
            self._definition().objective,
            model_profile=self._profile(),
            manifest=self._manifest(),
        )
        submitted = store.submit_for_approval(
            draft.id,
            draft.row_revision,
            self._definition(),
        )
        approved = store.approve(submitted.id, submitted.row_revision)
        execution = PlanExecution.from_events(
            approved.definition,
            approved.task_events,
            owner_id=str(approved.id),
        )
        current = execution.next_ready()
        assert current is not None
        execution.task_store.transition(
            current.task_id,
            expected_revision=current.revision,
            to_status=TaskStatus.RUNNING,
        )
        running = store.save_execution(
            approved.id,
            approved.row_revision,
            execution=execution,
            status="running",
        )

        second_database = Database(self.test_dsn)
        second_database.open()
        self.addCleanup(second_database.close)
        second_store = PlanStore(second_database)
        restored = second_store.get(running.id)
        self.assertEqual(restored.status, "running")
        self.assertEqual(
            tuple(run.id for run in second_store.list_recoverable_running()),
            (running.id,),
        )

        paused = second_store.save_execution(
            running.id,
            running.row_revision,
            execution=execution,
            status="paused",
            last_error="planning model step failed",
        )
        self.assertEqual(paused.status, "paused")
        self.assertEqual(
            tuple(
                node.status
                for node in PlanExecution.from_events(
                    paused.definition,
                    paused.task_events,
                    owner_id=str(paused.id),
                ).task_store.list()
                if node.status is TaskStatus.RUNNING
            ),
            (TaskStatus.RUNNING,),
        )
        self.assertEqual(second_store.list_recoverable_running(), ())
        self.assertEqual(second_store.get_active(conversation_id).id, paused.id)
        with self.assertRaises(ActivePlanningRunError):
            second_store.create_draft(
                conversation_id,
                "Second plan",
                model_profile=self._profile(),
                manifest=self._manifest(),
            )
        with self.database.connection() as connection:
            mode = connection.execute(
                "SELECT mode FROM conversations WHERE id = %s",
                (conversation_id,),
            ).fetchone()[0]
        self.assertEqual(mode, "plan")

    def test_007_backfills_existing_conversations(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        names = (
            "001_vector_extension.sql",
            "002_conversations_and_messages.sql",
            "003_long_term_memories.sql",
            "004_memory_supersedes.sql",
            "005_memory_embeddings.sql",
            "006_knowledge_sources_and_chunks.sql",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in names:
                (root / name).write_bytes((migrations_path / name).read_bytes())
            runner = MigrationRunner(self.database, root)
            self.assertEqual(
                runner.upgrade(),
                ("001", "002", "003", "004", "005", "006"),
            )
            conversation_id = uuid4()
            with self.database.connection() as connection:
                connection.execute(
                    "INSERT INTO conversations (id) VALUES (%s)",
                    (conversation_id,),
                )

            (root / "007_conversation_management.sql").write_bytes(
                (migrations_path / "007_conversation_management.sql").read_bytes()
            )
            self.assertEqual(runner.upgrade(), ("007",))

        with self.database.connection() as connection:
            loaded = connection.execute(
                "SELECT title, status FROM conversations WHERE id = %s",
                (conversation_id,),
            ).fetchone()
        self.assertEqual(loaded, ("New conversation", "active"))

    def test_009_backfills_existing_conversations(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        names = (
            "001_vector_extension.sql",
            "002_conversations_and_messages.sql",
            "003_long_term_memories.sql",
            "004_memory_supersedes.sql",
            "005_memory_embeddings.sql",
            "006_knowledge_sources_and_chunks.sql",
            "007_conversation_management.sql",
            "008_message_run_trace.sql",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in names:
                (root / name).write_bytes((migrations_path / name).read_bytes())
            runner = MigrationRunner(self.database, root)
            self.assertEqual(
                runner.upgrade(),
                ("001", "002", "003", "004", "005", "006", "007", "008"),
            )
            active_id = uuid4()
            archived_id = uuid4()
            with self.database.connection() as connection:
                for values in (
                    (active_id, "Active before 009", "active", 2),
                    (archived_id, "Archived before 009", "archived", 1),
                ):
                    connection.execute(
                        "INSERT INTO conversations "
                        "(id, title, status, next_message_sequence) "
                        "VALUES (%s, %s, %s, %s)",
                        values,
                    )
                connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, sequence, role, content) "
                    "VALUES (%s, %s, 1, 'user', 'before 009')",
                    (uuid4(), active_id),
                )

            (root / "009_planning_runs.sql").write_bytes(
                (migrations_path / "009_planning_runs.sql").read_bytes()
            )
            self.assertEqual(runner.upgrade(), ("009",))

        with self.database.connection() as connection:
            conversations = connection.execute(
                "SELECT id, title, status, mode FROM conversations "
                "WHERE id IN (%s, %s) ORDER BY title",
                (active_id, archived_id),
            ).fetchall()
            message = connection.execute(
                "SELECT role, content FROM conversation_messages "
                "WHERE conversation_id = %s",
                (active_id,),
            ).fetchone()
            planning_table = connection.execute(
                "SELECT to_regclass('planning_runs')"
            ).fetchone()[0]
        self.assertEqual(
            conversations,
            [
                (active_id, "Active before 009", "active", "default"),
                (archived_id, "Archived before 009", "archived", "default"),
            ],
        )
        self.assertEqual(message, ("user", "before 009"))
        self.assertEqual(planning_table, "planning_runs")

    def test_planning_schema_constraints_indexes_and_cascade(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()

        def insert_run(
            connection,
            conversation_id,
            *,
            version=1,
            row_revision=1,
            status="drafting",
            objective="Ship the plan",
            supersedes_version=None,
            revision_note=None,
            last_error=None,
            definition=None,
            task_events=(),
            run_state_snapshot=None,
            model_snapshot=None,
            workspace_snapshot=None,
        ) -> None:
            connection.execute(
                "INSERT INTO planning_runs ("
                "id, conversation_id, version, row_revision, status, objective, "
                "supersedes_version, revision_note, last_error, definition, "
                "task_events, run_state_snapshot, model_snapshot, "
                "workspace_snapshot"
                ") VALUES ("
                "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s"
                ")",
                (
                    uuid4(),
                    conversation_id,
                    version,
                    row_revision,
                    status,
                    objective,
                    supersedes_version,
                    revision_note,
                    last_error,
                    None if definition is None else Jsonb(definition),
                    Jsonb(task_events),
                    (
                        None
                        if run_state_snapshot is None
                        else Jsonb(run_state_snapshot)
                    ),
                    Jsonb({} if model_snapshot is None else model_snapshot),
                    Jsonb(
                        {} if workspace_snapshot is None else workspace_snapshot
                    ),
                ),
            )

        valid_rows = {
            "drafting": {},
            "awaiting_approval": {"definition": {}},
            "running": {"definition": {}, "task_events": ({},)},
            "awaiting_tool_approval": {
                "definition": {},
                "task_events": ({},),
                "run_state_snapshot": {},
            },
            "paused": {
                "definition": {},
                "task_events": ({},),
                "last_error": "planning model step failed",
            },
            "completed": {"definition": {}, "task_events": ({},)},
            "failed": {
                "definition": {},
                "task_events": ({},),
                "last_error": "model failed",
            },
            "cancelled": {},
            "superseded": {"definition": {}, "task_events": ({},)},
        }
        conversations = {}
        with self.database.connection() as connection:
            for status, values in valid_rows.items():
                conversation_id = uuid4()
                conversations[status] = conversation_id
                connection.execute(
                    "INSERT INTO conversations (id) VALUES (%s)",
                    (conversation_id,),
                )
                insert_run(
                    connection,
                    conversation_id,
                    status=status,
                    **values,
                )

            with self.assertRaises(CheckViolation):
                connection.execute(
                    "UPDATE conversations SET mode = 'planning' WHERE id = %s",
                    (conversations["drafting"],),
                )
            with self.assertRaises(UniqueViolation):
                insert_run(
                    connection,
                    conversations["drafting"],
                    version=2,
                    status="awaiting_approval",
                    definition={},
                )
            with self.assertRaises(UniqueViolation):
                insert_run(
                    connection,
                    conversations["paused"],
                    version=2,
                    status="drafting",
                )

            insert_run(
                connection,
                conversations["drafting"],
                version=2,
                status="cancelled",
            )
            insert_run(
                connection,
                conversations["drafting"],
                version=3,
                status="completed",
                definition={},
                task_events=({},),
            )
            with self.assertRaises(UniqueViolation):
                insert_run(
                    connection,
                    conversations["drafting"],
                    version=2,
                    status="completed",
                    definition={},
                    task_events=({},),
                )

            invalid_conversation_id = uuid4()
            connection.execute(
                "INSERT INTO conversations (id) VALUES (%s)",
                (invalid_conversation_id,),
            )
            invalid_rows = {
                "invalid status": {"status": "sleeping"},
                "zero version": {"version": 0},
                "zero row revision": {"row_revision": 0},
                "bad supersedes version": {
                    "version": 2,
                    "supersedes_version": 2,
                },
                "blank objective": {"objective": "   "},
                "blank revision note": {"revision_note": "   "},
                "array model snapshot": {"model_snapshot": []},
                "array workspace snapshot": {"workspace_snapshot": []},
                "array definition": {
                    "status": "awaiting_approval",
                    "definition": [],
                },
                "object task events": {
                    "status": "running",
                    "definition": {},
                    "task_events": {},
                },
                "array run snapshot": {
                    "status": "awaiting_tool_approval",
                    "definition": {},
                    "task_events": ({},),
                    "run_state_snapshot": [],
                },
                "running without events": {
                    "status": "running",
                    "definition": {},
                },
                "failed without error": {
                    "status": "failed",
                    "definition": {},
                    "task_events": ({},),
                },
                "paused without error": {
                    "status": "paused",
                    "definition": {},
                    "task_events": ({},),
                },
                "paused with snapshot": {
                    "status": "paused",
                    "definition": {},
                    "task_events": ({},),
                    "run_state_snapshot": {},
                    "last_error": "planning model step failed",
                },
                "drafting with definition": {"definition": {}},
                "awaiting approval with events": {
                    "status": "awaiting_approval",
                    "definition": {},
                    "task_events": ({},),
                },
                "completed with snapshot": {
                    "status": "completed",
                    "definition": {},
                    "task_events": ({},),
                    "run_state_snapshot": {},
                },
            }
            for name, values in invalid_rows.items():
                with self.subTest(name=name), self.assertRaises(CheckViolation):
                    insert_run(connection, invalid_conversation_id, **values)

            with self.assertRaises(ForeignKeyViolation):
                insert_run(connection, uuid4())

            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT indexname FROM pg_indexes "
                    "WHERE schemaname = current_schema() "
                    "AND indexname IN ("
                    "'planning_runs_active_conversation_key', "
                    "'planning_runs_recoverable_running_idx'"
                    ")"
                ).fetchall()
            }
            recoverable_index = connection.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = current_schema() "
                "AND indexname = 'planning_runs_recoverable_running_idx'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, sequence, role, content) "
                "VALUES (%s, %s, 1, 'user', 'cascade planning')",
                (uuid4(), conversations["drafting"]),
            )
            connection.execute(
                "DELETE FROM conversations WHERE id = %s",
                (conversations["drafting"],),
            )
            remaining = connection.execute(
                "SELECT count(*) FROM planning_runs WHERE conversation_id = %s",
                (conversations["drafting"],),
            ).fetchone()[0]
            remaining_messages = connection.execute(
                "SELECT count(*) FROM conversation_messages "
                "WHERE conversation_id = %s",
                (conversations["drafting"],),
            ).fetchone()[0]

        self.assertEqual(
            indexes,
            {
                "planning_runs_active_conversation_key",
                "planning_runs_recoverable_running_idx",
            },
        )
        self.assertIn("(updated_at, id)", recoverable_index)
        self.assertIn("WHERE (status = 'running'::text)", recoverable_index)
        self.assertEqual(remaining, 0)
        self.assertEqual(remaining_messages, 0)

    def test_planning_store_real_lifecycle_versions_and_secrets(self) -> None:
        self._upgrade()
        api_key = f"api-key-{uuid4().hex}"
        env_value = f"env-value-{uuid4().hex}"
        conversation = ConversationStore(self.database).create("Plan lifecycle")
        store = PlanStore(self.database)

        draft = store.create_draft(
            conversation.id,
            self._definition().objective,
            model_profile=self._profile(api_key),
            manifest=self._manifest(env_value),
        )

        self.assertEqual(draft.status, "drafting")
        self.assertEqual(draft.version, 1)
        self.assertTrue(draft.model_snapshot["has_api_key"])
        self.assertEqual(
            draft.workspace_snapshot["env_keys"],
            ["PLANNING_GATE_SECRET"],
        )
        with self.database.connection() as connection:
            mode = connection.execute(
                "SELECT mode FROM conversations WHERE id = %s",
                (conversation.id,),
            ).fetchone()[0]
            snapshots = connection.execute(
                "SELECT model_snapshot::text, workspace_snapshot::text "
                "FROM planning_runs WHERE id = %s",
                (draft.id,),
            ).fetchone()
        self.assertEqual(mode, "plan")
        self.assertNotIn(api_key, snapshots[0])
        self.assertNotIn(api_key, snapshots[1])
        self.assertNotIn(env_value, snapshots[0])
        self.assertNotIn(env_value, snapshots[1])

        submitted = store.submit_for_approval(
            draft.id,
            draft.row_revision,
            self._definition(),
        )
        approved = store.approve(submitted.id, submitted.row_revision)
        execution = PlanExecution.from_events(
            approved.definition,
            approved.task_events,
            owner_id=str(approved.id),
        )
        work = execution.task_store.get("work")
        execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.RUNNING,
        )
        work = execution.task_store.get("work")
        execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.COMPLETED,
            result={"work": "done"},
        )
        boundary = store.save_execution(
            approved.id,
            approved.row_revision,
            execution=execution,
            status="running",
        )
        self.assertEqual(boundary.status, "running")
        self.assertEqual(
            ConversationStore(self.database).get(conversation.id).mode,
            "plan",
        )

        verify = execution.task_store.get("verify")
        execution.task_store.transition(
            "verify",
            expected_revision=verify.revision,
            to_status=TaskStatus.RUNNING,
        )
        verify = execution.task_store.get("verify")
        execution.task_store.transition(
            "verify",
            expected_revision=verify.revision,
            to_status=TaskStatus.COMPLETED,
            result={"checks": "passed"},
        )
        completed = store.save_execution(
            boundary.id,
            boundary.row_revision,
            execution=execution,
            status="completed",
        )

        self.assertEqual(completed.status, "completed")
        self.assertEqual(
            ConversationStore(self.database).get(conversation.id).mode,
            "default",
        )
        self.assertIsNone(store.get_active(conversation.id))

        second = store.create_draft(
            conversation.id,
            "A separate follow-up plan",
            model_profile=self._profile(),
            manifest=self._manifest(),
        )
        self.assertEqual(second.version, 2)
        self.assertIsNone(second.supersedes_version)
        cancelled = store.cancel(
            second.id,
            second.row_revision,
            "Do not continue",
        )
        history = store.list_versions(conversation.id)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(tuple(item.version for item in history), (2, 1))
        self.assertEqual(
            tuple(item.status for item in history),
            ("cancelled", "completed"),
        )

    def test_planning_store_real_revision_and_cancellation(self) -> None:
        self._upgrade()
        conversation, store, submitted = self._create_submitted()

        revised = store.revise(
            submitted.id,
            submitted.row_revision,
            "Use smaller steps",
        )

        old = store.get(submitted.id)
        self.assertEqual(old.status, "superseded")
        self.assertEqual(revised.status, "drafting")
        self.assertEqual(revised.version, 2)
        self.assertEqual(revised.supersedes_version, 1)
        self.assertEqual(store.get_active(conversation.id).id, revised.id)
        store.cancel(revised.id, revised.row_revision, "Stop revision")
        self.assertEqual(
            ConversationStore(self.database).get(conversation.id).mode,
            "default",
        )

        running_conversation, running_store, running_submitted = (
            self._create_submitted()
        )
        approved = running_store.approve(
            running_submitted.id,
            running_submitted.row_revision,
        )
        cancelled = running_store.cancel(
            approved.id,
            approved.row_revision,
            "Stop execution",
        )
        restored = PlanExecution.from_events(
            cancelled.definition,
            cancelled.task_events,
            owner_id=str(cancelled.id),
        )
        self.assertTrue(
            all(
                node.status
                in (
                    TaskStatus.COMPLETED,
                    TaskStatus.FAILED,
                    TaskStatus.CANCELLED,
                )
                for node in restored.task_store.list()
            )
        )
        self.assertEqual(
            ConversationStore(self.database).get(running_conversation.id).mode,
            "default",
        )

    def test_planning_store_real_concurrent_cas_has_one_winner(self) -> None:
        self._upgrade()
        conversation, _, submitted = self._create_submitted()
        barrier = Barrier(2)

        def compete(operation: str) -> tuple[str, str]:
            database = Database(
                self.test_dsn,
                min_size=1,
                max_size=1,
            )
            database.open()
            try:
                store = PlanStore(database)
                barrier.wait(timeout=10)
                if operation == "approve":
                    store.approve(submitted.id, submitted.row_revision)
                else:
                    store.revise(
                        submitted.id,
                        submitted.row_revision,
                        "Concurrent revision",
                    )
                return operation, "success"
            except PlanningConflictError:
                return operation, "conflict"
            finally:
                database.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(compete, ("approve", "revise"))
            )

        self.assertEqual(
            sorted(outcome for _, outcome in results),
            ["conflict", "success"],
        )
        winner = next(
            operation
            for operation, outcome in results
            if outcome == "success"
        )
        with self.database.connection() as connection:
            rows = connection.execute(
                "SELECT version, status FROM planning_runs "
                "WHERE conversation_id = %s ORDER BY version",
                (conversation.id,),
            ).fetchall()
            active_count = connection.execute(
                "SELECT count(*) FROM planning_runs "
                "WHERE conversation_id = %s "
                "AND status IN ("
                "'drafting', 'awaiting_approval', "
                "'running', 'awaiting_tool_approval'"
                ")",
                (conversation.id,),
            ).fetchone()[0]
            mode = connection.execute(
                "SELECT mode FROM conversations WHERE id = %s",
                (conversation.id,),
            ).fetchone()[0]

        self.assertEqual(active_count, 1)
        self.assertEqual(mode, "plan")
        if winner == "approve":
            self.assertEqual(rows, [(1, "running")])
        else:
            self.assertEqual(rows, [(1, "superseded"), (2, "drafting")])

    def test_planning_store_real_transactions_roll_back_completely(self) -> None:
        self._upgrade()
        conversation, store, submitted = self._create_submitted()
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO planning_runs "
                "(id, conversation_id, version, status, objective, "
                "model_snapshot, workspace_snapshot) "
                "VALUES (%s, %s, 2, 'cancelled', %s, %s, %s)",
                (
                    uuid4(),
                    conversation.id,
                    "Reserved version",
                    Jsonb({}),
                    Jsonb({}),
                ),
            )

        with self.assertRaises(UniqueViolation):
            store.revise(
                submitted.id,
                submitted.row_revision,
                "This insert must fail",
            )

        unchanged = store.get(submitted.id)
        self.assertEqual(unchanged.status, "awaiting_approval")
        self.assertEqual(unchanged.row_revision, submitted.row_revision)
        self.assertEqual(store.get_active(conversation.id).id, submitted.id)

        terminal_conversation, terminal_store, terminal_submitted = (
            self._create_submitted()
        )
        approved = terminal_store.approve(
            terminal_submitted.id,
            terminal_submitted.row_revision,
        )
        execution = PlanExecution.from_events(
            approved.definition,
            approved.task_events,
            owner_id=str(approved.id),
        )
        for task_id in ("work", "verify"):
            node = execution.task_store.get(task_id)
            execution.task_store.transition(
                task_id,
                expected_revision=node.revision,
                to_status=TaskStatus.RUNNING,
            )
            node = execution.task_store.get(task_id)
            execution.task_store.transition(
                task_id,
                expected_revision=node.revision,
                to_status=TaskStatus.COMPLETED,
                result={"task": task_id},
            )

        with self.database.connection() as connection:
            connection.execute(
                "CREATE FUNCTION planning_gate_fail_mode_update() "
                "RETURNS trigger LANGUAGE plpgsql AS $$ "
                "BEGIN RAISE EXCEPTION 'forced planning mode failure'; END $$"
            )
            connection.execute(
                "CREATE TRIGGER planning_gate_fail_mode_update "
                "BEFORE UPDATE OF mode ON conversations "
                "FOR EACH ROW EXECUTE FUNCTION planning_gate_fail_mode_update()"
            )

        with self.assertRaises(Error):
            terminal_store.save_execution(
                approved.id,
                approved.row_revision,
                execution=execution,
                status="completed",
            )

        terminal_unchanged = terminal_store.get(approved.id)
        self.assertEqual(terminal_unchanged.status, "running")
        self.assertEqual(
            terminal_unchanged.row_revision,
            approved.row_revision,
        )
        self.assertEqual(terminal_unchanged.task_events, approved.task_events)
        self.assertEqual(
            ConversationStore(self.database).get(terminal_conversation.id).mode,
            "plan",
        )

        create_conversation = ConversationStore(self.database).create(
            "Create rollback"
        )
        with self.assertRaises(Error):
            PlanStore(self.database).create_draft(
                create_conversation.id,
                "This draft must roll back",
                model_profile=self._profile(),
                manifest=self._manifest(),
            )
        with self.database.connection() as connection:
            planning_count = connection.execute(
                "SELECT count(*) FROM planning_runs WHERE conversation_id = %s",
                (create_conversation.id,),
            ).fetchone()[0]
            mode = connection.execute(
                "SELECT mode FROM conversations WHERE id = %s",
                (create_conversation.id,),
            ).fetchone()[0]
        self.assertEqual(planning_count, 0)
        self.assertEqual(mode, "default")

    def test_planning_store_real_approval_and_new_pool_recovery(self) -> None:
        writer = Database(self.test_dsn, min_size=1, max_size=2)
        writer.open()
        try:
            self._upgrade(writer)
            conversation, store, submitted = self._create_submitted(writer)
            approved = store.approve(
                submitted.id,
                submitted.row_revision,
            )
            execution = PlanExecution.from_events(
                approved.definition,
                approved.task_events,
                owner_id=str(approved.id),
            )
            work = execution.task_store.get("work")
            execution.task_store.transition(
                "work",
                expected_revision=work.revision,
                to_status=TaskStatus.RUNNING,
            )
            work = execution.task_store.get("work")
            snapshot = RunStateSnapshot(
                input="continue planning",
                last_agent_name="planning-agent",
                last_response_id=None,
                current_turn=1,
                steps_taken=1,
                max_turns=None,
                max_steps=None,
                tool_approvals=(
                    ApprovalSnapshot(
                        tool_name="edit_local",
                        call_id="planning-call-1",
                        arguments={"path": "safe.py"},
                        status="pending",
                    ),
                ),
                model_responses=(),
                new_items=(),
                task_id=work.task_id,
                task_revision=work.revision,
            )
            paused = store.save_execution(
                approved.id,
                approved.row_revision,
                execution=execution,
                status="awaiting_tool_approval",
                run_state=snapshot,
            )
            self.assertEqual(store.list_recoverable_running(), ())
        finally:
            writer.close()

        resume_database = Database(self.test_dsn, min_size=1, max_size=2)
        resume_database.open()
        try:
            resume_store = PlanStore(resume_database)
            loaded_pause = resume_store.get(paused.id)
            self.assertEqual(
                loaded_pause.run_state_snapshot.tool_approvals[0].status,
                "pending",
            )
            self.assertEqual(resume_store.list_recoverable_running(), ())
            resumed = PlanExecution.from_events(
                loaded_pause.definition,
                loaded_pause.task_events,
                owner_id=str(loaded_pause.id),
            )
            work = resumed.task_store.get("work")
            resumed.task_store.transition(
                "work",
                expected_revision=work.revision,
                to_status=TaskStatus.COMPLETED,
                result={"approved": True},
            )
            boundary = resume_store.save_execution(
                loaded_pause.id,
                loaded_pause.row_revision,
                execution=resumed,
                status="running",
            )
            expected_events = boundary.task_events
            expected_nodes = resumed.task_store.list()
            self.assertIsNone(boundary.run_state_snapshot)
        finally:
            resume_database.close()

        reader = Database(self.test_dsn, min_size=1, max_size=2)
        reader.open()
        try:
            recovered_runs = PlanStore(reader).list_recoverable_running()
            self.assertEqual(tuple(run.id for run in recovered_runs), (boundary.id,))
            recovered = PlanExecution.from_events(
                recovered_runs[0].definition,
                recovered_runs[0].task_events,
                owner_id=str(recovered_runs[0].id),
            )
            self.assertEqual(recovered_runs[0].task_events, expected_events)
            self.assertEqual(recovered.task_store.list(), expected_nodes)
            self.assertEqual(
                ConversationStore(reader).get(conversation.id).mode,
                "plan",
            )
        finally:
            reader.close()

    def test_planning_service_recovers_running_task_with_new_pool(self) -> None:
        writer = Database(self.test_dsn, min_size=1, max_size=2)
        writer.open()
        try:
            self._upgrade(writer)
            conversation, store, submitted = self._create_submitted(writer)
            approved = store.approve(submitted.id, submitted.row_revision)
            execution = PlanExecution.from_events(
                approved.definition,
                approved.task_events,
                owner_id=str(approved.id),
            )
            work = execution.task_store.get("work")
            execution.task_store.transition(
                work.task_id,
                expected_revision=work.revision,
                to_status=TaskStatus.RUNNING,
            )
            persisted = store.save_execution(
                approved.id,
                approved.row_revision,
                execution=execution,
                status="running",
            )
        finally:
            writer.close()

        frozen = persisted.model_snapshot
        profile = ModelProfile(
            id=UUID(str(frozen["id"])),
            name="Recovered profile",
            provider=str(frozen["provider"]),
            protocol=str(frozen["protocol"]),
            base_url=str(frozen["base_url"]),
            model=str(frozen["model"]),
            api_key="rotated-planning-key",
        )
        profiles = Mock()
        profiles.get.return_value = profile
        model = SimpleNamespace(
            get_response=Mock(
                return_value=ModelResponse(
                    response_id="recovered-work",
                    output=[],
                    output_text="Work recovered",
                    tool_calls=[],
                )
            )
        )
        command = str(persisted.workspace_snapshot["default_test_command"])
        passed = (
            VerificationResult(
                command=command,
                returncode=0,
                passed=True,
            ),
        )

        reader = Database(self.test_dsn, min_size=1, max_size=2)
        reader.open()
        try:
            service = PlanningService(
                PlanStore(reader),
                Mock(),
                profiles,
                self._manifest,
            )
            with (
                patch(
                    "agents.chat_backend.planning_service.model_for_profile",
                    return_value=model,
                ),
                patch(
                    "agents.chat_backend.planning_service.VerificationRunner.run",
                    return_value=passed,
                ),
            ):
                report = asyncio.run(service.recover_running())

            self.assertEqual(report.recovered_run_ids, (persisted.id,))
            self.assertEqual(report.deferred_run_ids, ())
            self.assertEqual(PlanStore(reader).get(persisted.id).status, "completed")
            self.assertEqual(
                ConversationStore(reader).get(conversation.id).mode,
                "default",
            )
        finally:
            reader.close()

    def test_planning_store_real_codec_corruption_is_not_skipped(self) -> None:
        self._upgrade()
        store = PlanStore(self.database)

        def insert_corrupt(
            *,
            run_id: UUID,
            status: str,
            definition: object,
            events: object,
            snapshot: object = None,
        ) -> UUID:
            conversation = ConversationStore(self.database).create(
                f"Corrupt {status}"
            )
            with self.database.connection() as connection:
                connection.execute(
                    "INSERT INTO planning_runs "
                    "(id, conversation_id, version, status, objective, "
                    "definition, task_events, run_state_snapshot, "
                    "model_snapshot, workspace_snapshot) "
                    "VALUES (%s, %s, 1, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        run_id,
                        conversation.id,
                        status,
                        "Corrupt planning row",
                        Jsonb(definition),
                        Jsonb(events),
                        None if snapshot is None else Jsonb(snapshot),
                        Jsonb({}),
                        Jsonb({}),
                    ),
                )
            return run_id

        invalid_definition_id = insert_corrupt(
            run_id=uuid4(),
            status="awaiting_approval",
            definition={"invalid": True},
            events=[],
        )

        bad_event_id = uuid4()
        bad_event_execution = PlanExecution.materialize(
            self._definition(),
            owner_id=str(bad_event_id),
        )
        bad_events = [
            task_event_to_dict(event)
            for event in bad_event_execution.task_store.events()
        ]
        bad_events[0]["schema_version"] = 999
        invalid_event_id = insert_corrupt(
            run_id=bad_event_id,
            status="running",
            definition=plan_definition_to_dict(self._definition()),
            events=bad_events,
        )

        wrong_owner_id = uuid4()
        wrong_owner_execution = PlanExecution.materialize(
            self._definition(),
            owner_id=str(uuid4()),
        )
        invalid_owner_id = insert_corrupt(
            run_id=wrong_owner_id,
            status="running",
            definition=plan_definition_to_dict(self._definition()),
            events=[
                task_event_to_dict(event)
                for event in wrong_owner_execution.task_store.events()
            ],
        )

        approval_id = uuid4()
        approval_execution = PlanExecution.materialize(
            self._definition(),
            owner_id=str(approval_id),
        )
        work = approval_execution.task_store.get("work")
        approval_execution.task_store.transition(
            "work",
            expected_revision=work.revision,
            to_status=TaskStatus.RUNNING,
        )
        work = approval_execution.task_store.get("work")
        invalid_snapshot = RunStateSnapshot(
            input="continue",
            last_agent_name="planning-agent",
            last_response_id=None,
            current_turn=1,
            steps_taken=1,
            max_turns=None,
            max_steps=None,
            tool_approvals=(
                ApprovalSnapshot(
                    tool_name="edit_local",
                    call_id="already-approved",
                    arguments={},
                    status="approved",
                ),
            ),
            model_responses=(),
            new_items=(),
            task_id=work.task_id,
            task_revision=work.revision,
        )
        invalid_approval_id = insert_corrupt(
            run_id=approval_id,
            status="awaiting_tool_approval",
            definition=plan_definition_to_dict(self._definition()),
            events=[
                task_event_to_dict(event)
                for event in approval_execution.task_store.events()
            ],
            snapshot=run_state_snapshot_to_dict(invalid_snapshot),
        )

        cases = (
            (invalid_definition_id, "definition invalid"),
            (invalid_event_id, "task events invalid"),
            (invalid_owner_id, "plan execution invalid"),
            (invalid_approval_id, "status combination invalid"),
        )
        for run_id, reason in cases:
            with self.subTest(reason=reason):
                with self.assertRaises(CorruptPlanningRunError) as caught:
                    store.get(run_id)
                self.assertEqual(caught.exception.reason, reason)

    def test_knowledge_store_constraints_pagination_and_cascade(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        store = KnowledgeStore(self.database)
        sources = tuple(
            store.create_source(
                KnowledgeSourceCreate(
                    f"Guide {index}",
                    "e\u0301\r\ncontent",
                    {"index": index},
                )
            )
            for index in range(3)
        )
        same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        vector = embedding_vector_literal((0.0,) * EMBEDDING_DIMENSIONS)

        with self.database.connection() as connection:
            connection.execute(
                "UPDATE knowledge_sources SET created_at = %s",
                (same_time,),
            )
            connection.execute(
                "INSERT INTO knowledge_chunks "
                "(id, source_id, ordinal, start_char, end_char, content, "
                "embedding, embedding_model) "
                "VALUES (%s, %s, 0, 0, 9, %s, %s::vector, %s)",
                (uuid4(), sources[0].id, "é\ncontent", vector, "model"),
            )
            with self.assertRaises(CheckViolation):
                connection.execute(
                    "UPDATE knowledge_sources SET status = 'indexed' "
                    "WHERE id = %s",
                    (sources[0].id,),
                )
            with self.assertRaises(UniqueViolation):
                connection.execute(
                    "INSERT INTO knowledge_chunks "
                    "(id, source_id, ordinal, start_char, end_char, content, "
                    "embedding, embedding_model) "
                    "VALUES (%s, %s, 0, 0, 1, 'x', %s::vector, 'model')",
                    (uuid4(), sources[0].id, vector),
                )
            with self.assertRaises(ForeignKeyViolation):
                connection.execute(
                    "INSERT INTO knowledge_chunks "
                    "(id, source_id, ordinal, start_char, end_char, content, "
                    "embedding, embedding_model) "
                    "VALUES (%s, %s, 0, 0, 1, 'x', %s::vector, 'model')",
                    (uuid4(), uuid4(), vector),
                )

        first_page = store.list_sources(2)
        second_page = store.list_sources(2, first_page.next_cursor)
        expected_ids = sorted((source.id for source in sources), reverse=True)
        self.assertEqual(
            [source.id for source in (*first_page.items, *second_page.items)],
            expected_ids,
        )
        self.assertEqual(store.get_epoch(), 3)
        self.assertEqual(store.get_source(sources[0].id).text, "é\ncontent")

        store.delete_source(sources[0].id)
        with self.database.connection() as connection:
            chunks = connection.execute(
                "SELECT count(*) FROM knowledge_chunks WHERE source_id = %s",
                (sources[0].id,),
            ).fetchone()[0]
        self.assertEqual(chunks, 0)
        self.assertEqual(store.get_epoch(), 4)
        with self.assertRaises(KnowledgeNotFoundError):
            store.get_source(sources[0].id)

    def test_knowledge_epoch_failures_roll_back_source_changes(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        store = KnowledgeStore(self.database)

        with self.database.connection() as connection:
            connection.execute("DELETE FROM knowledge_epoch WHERE id = 1")
        with self.assertRaisesRegex(RuntimeError, "knowledge epoch is missing"):
            store.create_source(KnowledgeSourceCreate("Guide", "text"))
        with self.database.connection() as connection:
            count = connection.execute(
                "SELECT count(*) FROM knowledge_sources"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO knowledge_epoch (id, value) VALUES (1, 0)"
            )
        self.assertEqual(count, 0)

        source = store.create_source(KnowledgeSourceCreate("Guide", "text"))
        vector = embedding_vector_literal((0.0,) * EMBEDDING_DIMENSIONS)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO knowledge_chunks "
                "(id, source_id, ordinal, start_char, end_char, content, "
                "embedding, embedding_model) "
                "VALUES (%s, %s, 0, 0, 4, 'text', %s::vector, 'model')",
                (uuid4(), source.id, vector),
            )
            connection.execute("DELETE FROM knowledge_epoch WHERE id = 1")

        with self.assertRaisesRegex(RuntimeError, "knowledge epoch is missing"):
            store.delete_source(source.id)
        self.assertEqual(store.get_source(source.id), source)
        with self.database.connection() as connection:
            chunk_count = connection.execute(
                "SELECT count(*) FROM knowledge_chunks WHERE source_id = %s",
                (source.id,),
            ).fetchone()[0]
        self.assertEqual(chunk_count, 1)

    def test_knowledge_publish_and_search_use_committed_status_model_and_filter(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        store = KnowledgeStore(self.database)
        first = store.create_source(KnowledgeSourceCreate("First", "alpha"))
        second = store.create_source(KnowledgeSourceCreate("Second", "alpha"))
        pending = store.create_source(KnowledgeSourceCreate("Pending", "alpha"))
        first_vector = (1.0,) + (0.0,) * (EMBEDDING_DIMENSIONS - 1)
        second_vector = (0.0, 1.0) + (0.0,) * (EMBEDDING_DIMENSIONS - 2)

        report = store.replace_chunks(
            first.id,
            (KnowledgeChunkDraft(0, 0, 5, "alpha"),),
            (first_vector,),
            "model-a",
        )
        store.replace_chunks(
            second.id,
            (KnowledgeChunkDraft(0, 0, 5, "alpha"),),
            (second_vector,),
            "model-b",
        )

        self.assertEqual((report.source_id, report.chunk_count), (first.id, 1))
        self.assertEqual(store.get_source(first.id).status, "indexed")
        self.assertEqual(store.get_epoch(), 5)
        self.assertEqual(
            {hit.source_id for hit in store.search_keyword("alpha", 10)},
            {first.id, second.id},
        )
        self.assertEqual(store.search_keyword("alpha", 10, (pending.id,)), ())
        vector_hits = store.search_vector(
            first_vector,
            "model-a",
            10,
            (first.id, second.id),
        )
        self.assertEqual([hit.source_id for hit in vector_hits], [first.id])
        self.assertAlmostEqual(vector_hits[0].distance or 0.0, 0.0)

    def test_knowledge_reindex_insert_failure_rolls_back_old_index_and_epoch(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        store = KnowledgeStore(self.database)
        source = store.create_source(KnowledgeSourceCreate("Guide", "old text"))
        vector = ((1.0,) + (0.0,) * (EMBEDDING_DIMENSIONS - 1),)
        store.replace_chunks(
            source.id,
            (KnowledgeChunkDraft(0, 0, 8, "old text"),),
            vector,
            "old-model",
        )
        before_source = store.get_source(source.id)
        before_epoch = store.get_epoch()
        with self.database.connection() as connection:
            before_chunks = connection.execute(
                "SELECT id, ordinal, content, embedding_model "
                "FROM knowledge_chunks WHERE source_id = %s ORDER BY ordinal",
                (source.id,),
            ).fetchall()
            connection.execute(
                "CREATE FUNCTION fail_second_knowledge_chunk() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.content = 'text' THEN RAISE EXCEPTION 'forced failure'; "
                "END IF; RETURN NEW; END $$"
            )
            connection.execute(
                "CREATE TRIGGER fail_second_knowledge_chunk "
                "BEFORE INSERT ON knowledge_chunks FOR EACH ROW "
                "EXECUTE FUNCTION fail_second_knowledge_chunk()"
            )

        with self.assertRaises(Error):
            store.replace_chunks(
                source.id,
                (
                    KnowledgeChunkDraft(0, 0, 3, "old"),
                    KnowledgeChunkDraft(1, 4, 8, "text"),
                ),
                (vector[0], vector[0]),
                "new-model",
            )

        after_source = store.get_source(source.id)
        with self.database.connection() as connection:
            after_chunks = connection.execute(
                "SELECT id, ordinal, content, embedding_model "
                "FROM knowledge_chunks WHERE source_id = %s ORDER BY ordinal",
                (source.id,),
            ).fetchall()
        self.assertEqual(after_chunks, before_chunks)
        self.assertEqual(after_source.status, before_source.status)
        self.assertEqual(after_source.embedding_model, before_source.embedding_model)
        self.assertEqual(after_source.indexed_at, before_source.indexed_at)
        self.assertEqual(store.get_epoch(), before_epoch)

    def test_conversation_schema_constraints(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation_id = uuid4()

        with self.database.connection() as connection:
            conversation = connection.execute(
                "INSERT INTO conversations (id) VALUES (%s) "
                "RETURNING next_message_sequence, created_at, title, status",
                (conversation_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO conversation_messages "
                "(id, conversation_id, sequence, role, content) "
                "VALUES (%s, %s, %s, %s, %s)",
                (uuid4(), conversation_id, 1, "user", "hello"),
            )

            with self.assertRaises(CheckViolation):
                connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, sequence, role, content) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (uuid4(), conversation_id, 2, "invalid", "hello"),
                )
            with self.assertRaises(CheckViolation):
                connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, sequence, role, content) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (uuid4(), conversation_id, 0, "user", "hello"),
                )
            with self.assertRaises(UniqueViolation):
                connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, sequence, role, content) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (uuid4(), conversation_id, 1, "assistant", "hello"),
                )
            with self.assertRaises(ForeignKeyViolation):
                connection.execute(
                    "INSERT INTO conversation_messages "
                    "(id, conversation_id, sequence, role, content) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    (uuid4(), uuid4(), 1, "user", "hello"),
                )
            with self.assertRaises(CheckViolation):
                connection.execute(
                    "INSERT INTO conversations (id, title) VALUES (%s, %s)",
                    (uuid4(), " padded "),
                )
            with self.assertRaises(CheckViolation):
                connection.execute(
                    "INSERT INTO conversations (id, status) VALUES (%s, %s)",
                    (uuid4(), "paused"),
                )

            connection.execute(
                "DELETE FROM conversations WHERE id = %s",
                (conversation_id,),
            )
            remaining = connection.execute(
                "SELECT count(*) FROM conversation_messages "
                "WHERE conversation_id = %s",
                (conversation_id,),
            ).fetchone()[0]

        self.assertEqual(conversation[0], 1)
        self.assertIsNotNone(conversation[1].utcoffset())
        self.assertEqual(conversation[2:], ("New conversation", "active"))
        self.assertEqual(remaining, 0)

    def test_memory_schema_constraints_and_partial_uniqueness(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        first_conversation = conversations.create()
        second_conversation = conversations.create()
        first_messages = messages.add_batch(
            first_conversation.id,
            (ChatMessage("user", "first"), ChatMessage("assistant", "answer")),
        )
        second_messages = messages.add_batch(
            second_conversation.id,
            (ChatMessage("user", "second"), ChatMessage("assistant", "answer")),
        )

        with self.database.connection() as connection:
            def insert_memory(**overrides):
                values = {
                    "id": uuid4(),
                    "kind": "fact",
                    "scope": "global",
                    "status": "active",
                    "content": "The user teaches Python.",
                    "tags": ["profile"],
                    "confidence": 0.9,
                    "fingerprint": uuid4().hex * 2,
                    "source_conversation_id": first_conversation.id,
                    "source_user_message_id": first_messages[0].id,
                    "source_assistant_message_id": first_messages[1].id,
                    "supersedes_id": None,
                }
                values.update(overrides)
                return connection.execute(
                    "INSERT INTO memories "
                    "(id, kind, scope, status, content, tags, confidence, "
                    "fingerprint, source_conversation_id, "
                    "source_user_message_id, source_assistant_message_id, "
                    "supersedes_id) "
                    "VALUES (%(id)s, %(kind)s, %(scope)s, %(status)s, "
                    "%(content)s, %(tags)s, %(confidence)s, %(fingerprint)s, "
                    "%(source_conversation_id)s, %(source_user_message_id)s, "
                    "%(source_assistant_message_id)s, %(supersedes_id)s) "
                    "RETURNING id",
                    values,
                ).fetchone()[0]

            global_fingerprint = "a" * 64
            conversation_fingerprint = "b" * 64
            valid_id = insert_memory(fingerprint=global_fingerprint)
            self.assertIsNotNone(valid_id)
            replacement_id = insert_memory(
                content="Replacement history",
                fingerprint="c" * 64,
                supersedes_id=valid_id,
            )

            invalid_cases = (
                ("kind", {"kind": "unknown"}, CheckViolation),
                ("scope", {"scope": "unknown"}, CheckViolation),
                ("status", {"status": "unknown"}, CheckViolation),
                ("blank content", {"content": " \t "}, CheckViolation),
                ("long content", {"content": "x" * 2001}, CheckViolation),
                ("low confidence", {"confidence": -0.1}, CheckViolation),
                ("nan confidence", {"confidence": float("nan")}, CheckViolation),
                ("fingerprint", {"fingerprint": "A" * 64}, CheckViolation),
                (
                    "same source message",
                    {"source_assistant_message_id": first_messages[0].id},
                    CheckViolation,
                ),
                (
                    "missing user source",
                    {"source_user_message_id": uuid4()},
                    ForeignKeyViolation,
                ),
                (
                    "cross conversation source",
                    {"source_user_message_id": second_messages[0].id},
                    ForeignKeyViolation,
                ),
                (
                    "missing superseded memory",
                    {"supersedes_id": uuid4()},
                    ForeignKeyViolation,
                ),
            )
            for name, overrides, error_type in invalid_cases:
                with self.subTest(name=name), self.assertRaises(error_type):
                    insert_memory(**overrides)

            self_id = uuid4()
            with self.assertRaises(CheckViolation):
                insert_memory(
                    id=self_id,
                    content="Self relation",
                    fingerprint="d" * 64,
                    supersedes_id=self_id,
                )

            with self.assertRaises(UniqueViolation):
                insert_memory(
                    content="duplicate global",
                    fingerprint=global_fingerprint,
                )
            disabled_id = insert_memory(
                status="disabled",
                content="disabled history",
                fingerprint=global_fingerprint,
            )
            first_conversation_memory = insert_memory(
                scope="conversation",
                content="first conversation",
                fingerprint=conversation_fingerprint,
            )
            with self.assertRaises(UniqueViolation):
                insert_memory(
                    scope="conversation",
                    content="duplicate first conversation",
                    fingerprint=conversation_fingerprint,
                )
            second_conversation_memory = insert_memory(
                scope="conversation",
                content="second conversation",
                fingerprint=conversation_fingerprint,
                source_conversation_id=second_conversation.id,
                source_user_message_id=second_messages[0].id,
                source_assistant_message_id=second_messages[1].id,
            )

            stored_ids = connection.execute(
                "SELECT id FROM memories ORDER BY id"
            ).fetchall()
            epoch = connection.execute(
                "SELECT value FROM memory_epochs WHERE name = 'memory'"
            ).fetchone()[0]

        self.assertEqual(
            {row[0] for row in stored_ids},
            {
                valid_id,
                replacement_id,
                disabled_id,
                first_conversation_memory,
                second_conversation_memory,
            },
        )
        self.assertEqual(epoch, 0)

        with self.database.connection() as connection:
            supersedes_index = connection.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = 'memories' "
                "AND indexname = 'memories_supersedes_id_idx'"
            ).fetchone()
            connection.execute("DELETE FROM memories WHERE id = %s", (valid_id,))
            cleared_relation = connection.execute(
                "SELECT supersedes_id FROM memories WHERE id = %s",
                (replacement_id,),
            ).fetchone()[0]
        self.assertEqual(supersedes_index, ("memories_supersedes_id_idx",))
        self.assertIsNone(cleared_relation)

        messages.clear(first_conversation.id)
        with self.database.connection() as connection:
            remaining_sources = connection.execute(
                "SELECT source_conversation_id FROM memories"
            ).fetchall()
        self.assertEqual(remaining_sources, [(second_conversation.id,)])

    def test_memory_store_reads_database_fact_and_epoch(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        source_messages = MessageStore(self.database).add_batch(
            conversation.id,
            (ChatMessage("user", "question"), ChatMessage("assistant", "answer")),
        )
        memory_id = uuid4()

        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO memories "
                "(id, kind, scope, content, tags, confidence, fingerprint, "
                "source_conversation_id, source_user_message_id, "
                "source_assistant_message_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    memory_id,
                    "preference",
                    "conversation",
                    "Use concise answers.",
                    ["style"],
                    0.8,
                    "b" * 64,
                    conversation.id,
                    source_messages[0].id,
                    source_messages[1].id,
                ),
            )

        store = MemoryStore(self.database)
        record = store.get(memory_id)

        self.assertEqual(record.id, memory_id)
        self.assertEqual(record.kind, "preference")
        self.assertEqual(record.scope, "conversation")
        self.assertEqual(record.status, "active")
        self.assertEqual(record.tags, ("style",))
        self.assertEqual(record.source.conversation_id, conversation.id)
        self.assertEqual(record.source.user_message_id, source_messages[0].id)
        self.assertEqual(
            record.source.assistant_message_id,
            source_messages[1].id,
        )
        self.assertIsNotNone(record.created_at.utcoffset())
        self.assertIsNotNone(record.updated_at.utcoffset())
        self.assertEqual(store.get_epoch(), 0)

        with self.assertRaises(MemoryNotFoundError):
            store.get(uuid4())
        with self.database.connection() as connection:
            connection.execute("DELETE FROM memory_epochs WHERE name = 'memory'")
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.get_epoch()

    def test_memory_store_create_normalizes_deduplicates_and_scopes(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        first_conversation = conversations.create()
        second_conversation = conversations.create()
        first_messages = messages.add_batch(
            first_conversation.id,
            (ChatMessage("user", "first"), ChatMessage("assistant", "answer")),
        )
        second_messages = messages.add_batch(
            second_conversation.id,
            (ChatMessage("user", "second"), ChatMessage("assistant", "answer")),
        )
        first_source = MemorySource(
            first_conversation.id,
            first_messages[0].id,
            first_messages[1].id,
        )
        second_source = MemorySource(
            second_conversation.id,
            second_messages[0].id,
            second_messages[1].id,
        )
        store = MemoryStore(self.database)

        created = store.create(
            MemoryCreate(
                kind="preference",
                scope="global",
                content="  Caf\u0065\u0301\nlikes   Python! ",
                source=first_source,
                tags=(" Style ", "STYLE", "Caf\u0065\u0301"),
                confidence=0.75,
            )
        )
        duplicate = store.create(
            MemoryCreate(
                kind="fact",
                scope="global",
                content="Café likes Python!",
                source=second_source,
            )
        )
        first_scoped = store.create(
            MemoryCreate(
                kind="fact",
                scope="conversation",
                content="Café likes Python!",
                source=first_source,
            )
        )
        first_scoped_duplicate = store.create(
            MemoryCreate(
                kind="instruction",
                scope="conversation",
                content="Café likes Python!",
                source=first_source,
            )
        )
        second_scoped = store.create(
            MemoryCreate(
                kind="fact",
                scope="conversation",
                content="Café likes Python!",
                source=second_source,
            )
        )

        self.assertTrue(created.created)
        self.assertEqual(created.record.content, "Café likes Python!")
        self.assertEqual(created.record.tags, ("café", "style"))
        self.assertEqual(created.record.confidence, 0.75)
        self.assertEqual(
            created.record.fingerprint,
            hashlib.sha256(
                "global\nCafé likes Python!".encode("utf-8")
            ).hexdigest(),
        )
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.record.id, created.record.id)
        self.assertEqual(duplicate.record.source, first_source)
        self.assertTrue(first_scoped.created)
        self.assertFalse(first_scoped_duplicate.created)
        self.assertEqual(
            first_scoped_duplicate.record.id,
            first_scoped.record.id,
        )
        self.assertTrue(second_scoped.created)
        self.assertNotEqual(second_scoped.record.id, first_scoped.record.id)
        self.assertEqual(store.get_epoch(), 3)

        with self.database.connection() as connection:
            active_count = connection.execute(
                "SELECT count(*) FROM memories WHERE status = 'active'"
            ).fetchone()[0]
        self.assertEqual(active_count, 3)

    def test_memory_embedding_lifecycle_is_atomic_and_model_aware(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        conversation = conversations.create()
        stored = messages.add_batch(
            conversation.id,
            (ChatMessage("user", "user"), ChatMessage("assistant", "assistant")),
        )
        source = MemorySource(conversation.id, stored[0].id, stored[1].id)
        store = MemoryStore(self.database)
        vector = (0.25,) * EMBEDDING_DIMENSIONS

        embedded = store.create(
            MemoryCreate("fact", "global", "already embedded", source),
            embedding=vector,
            embedding_model="model-a",
        ).record
        self.assertEqual(embedded.embedding_model, "model-a")
        self.assertIsNotNone(embedded.embedded_at)

        replacement = store.supersede(
            embedded.id,
            MemoryCreate("fact", "global", "replacement embedded", source),
            embedding=vector,
            embedding_model="model-a",
        )
        self.assertEqual(replacement.embedding_model, "model-a")
        self.assertEqual(store.get(embedded.id).status, "disabled")

        first = store.create(
            MemoryCreate("fact", "global", "first missing", source)
        ).record
        second = store.create(
            MemoryCreate("fact", "global", "second missing", source)
        ).record
        targets = store.list_embedding_targets("model-a", 10)
        self.assertEqual({target.id for target in targets}, {first.id, second.id})
        self.assertIn(
            replacement.id,
            {target.id for target in store.list_embedding_targets("model-b", 10)},
        )

        with self.database.connection() as connection:
            with self.assertRaises(CheckViolation):
                connection.execute(
                    "UPDATE memories SET embedding_model = %s WHERE id = %s",
                    ("model-a", first.id),
                )

        store.update(second.id, MemoryPatch(content="second changed"))
        epoch_before_conflict = store.get_epoch()
        with self.assertRaises(MemoryEmbeddingConflictError):
            store.save_embeddings(targets, (vector, vector), "model-a")
        self.assertEqual(store.get_epoch(), epoch_before_conflict)
        with self.database.connection() as connection:
            saved_models = connection.execute(
                "SELECT embedding_model FROM memories WHERE id IN (%s, %s) "
                "ORDER BY id",
                (first.id, second.id),
            ).fetchall()
        self.assertEqual(saved_models, [(None,), (None,)])

        refreshed = store.list_embedding_targets("model-a", 10)
        epoch_before_save = store.get_epoch()
        self.assertEqual(
            store.save_embeddings(refreshed, (vector,) * len(refreshed), "model-a"),
            2,
        )
        self.assertEqual(store.get_epoch(), epoch_before_save + 1)

        store.update(first.id, MemoryPatch(tags=("kept",)))
        self.assertEqual(store.get(first.id).embedding_model, "model-a")
        store.update(first.id, MemoryPatch(content="first changed"))
        changed = store.get(first.id)
        self.assertIsNone(changed.embedding_model)
        self.assertIsNone(changed.embedded_at)

    def test_memory_search_filters_scope_model_and_literal_wildcards(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        first_conversation = conversations.create()
        second_conversation = conversations.create()
        first_messages = messages.add_batch(
            first_conversation.id,
            (ChatMessage("user", "first"), ChatMessage("assistant", "answer")),
        )
        second_messages = messages.add_batch(
            second_conversation.id,
            (ChatMessage("user", "second"), ChatMessage("assistant", "answer")),
        )
        first_source = MemorySource(
            first_conversation.id,
            first_messages[0].id,
            first_messages[1].id,
        )
        second_source = MemorySource(
            second_conversation.id,
            second_messages[0].id,
            second_messages[1].id,
        )
        x_vector = (1.0,) + (0.0,) * (EMBEDDING_DIMENSIONS - 1)
        near_vector = (1.0, 0.1) + (0.0,) * (EMBEDDING_DIMENSIONS - 2)
        store = MemoryStore(self.database)

        global_first = store.create(
            MemoryCreate(
                "fact",
                "global",
                "literal 50%_ token global",
                first_source,
            ),
            embedding=x_vector,
            embedding_model="model-a",
        ).record
        global_tie = store.create(
            MemoryCreate(
                "fact",
                "global",
                "other vector tie",
                first_source,
                tags=("tag\\path",),
            ),
            embedding=x_vector,
            embedding_model="model-a",
        ).record
        local = store.create(
            MemoryCreate(
                "fact",
                "conversation",
                "literal 50%_ token local",
                first_source,
            ),
            embedding=near_vector,
            embedding_model="model-a",
        ).record
        other_conversation = store.create(
            MemoryCreate(
                "fact",
                "conversation",
                "literal 50%_ token other",
                second_source,
            ),
            embedding=x_vector,
            embedding_model="model-a",
        ).record
        other_model = store.create(
            MemoryCreate("fact", "global", "other model", first_source),
            embedding=x_vector,
            embedding_model="model-b",
        ).record
        disabled = store.create(
            MemoryCreate(
                "fact",
                "conversation",
                "literal 50%_ disabled",
                first_source,
            ),
            embedding=x_vector,
            embedding_model="model-a",
        ).record
        store.disable(disabled.id)

        keyword_ids = {
            hit.memory.id
            for hit in store.search_keyword("50%_", first_conversation.id, 10)
        }
        self.assertEqual(keyword_ids, {global_first.id, local.id})
        self.assertEqual(
            tuple(
                hit.memory.id
                for hit in store.search_keyword(
                    "tag\\path",
                    second_conversation.id,
                    10,
                )
            ),
            (global_tie.id,),
        )

        vector_hits = store.search_vector(
            x_vector,
            "model-a",
            first_conversation.id,
            10,
        )
        vector_ids = tuple(hit.memory.id for hit in vector_hits)
        self.assertEqual(
            vector_ids[:2],
            tuple(sorted((global_first.id, global_tie.id))),
        )
        self.assertEqual(vector_ids[2:], (local.id,))
        self.assertNotIn(other_conversation.id, vector_ids)
        self.assertNotIn(other_model.id, vector_ids)
        self.assertNotIn(disabled.id, vector_ids)

        nearest_global = store.find_nearest_active(
            x_vector,
            "model-a",
            "fact",
            "global",
            None,
        )
        nearest_local = store.find_nearest_active(
            x_vector,
            "model-a",
            "fact",
            "conversation",
            first_conversation.id,
        )
        self.assertIsNotNone(nearest_global)
        self.assertIsNotNone(nearest_local)
        assert nearest_global is not None and nearest_local is not None
        self.assertEqual(
            nearest_global.memory.id,
            min(global_first.id, global_tie.id),
        )
        self.assertEqual(nearest_local.memory.id, local.id)

    def test_memory_store_lists_stable_pages_and_combined_filters(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        first_conversation = conversations.create()
        second_conversation = conversations.create()
        first_messages = messages.add_batch(
            first_conversation.id,
            (
                ChatMessage("user", "first user"),
                ChatMessage("assistant", "first assistant"),
                ChatMessage("user", "second user"),
                ChatMessage("assistant", "second assistant"),
            ),
        )
        second_messages = messages.add_batch(
            second_conversation.id,
            (ChatMessage("user", "other user"), ChatMessage("assistant", "other")),
        )
        oldest, older, scoped, disabled, newest, inserted = (
            UUID(int=value) for value in range(1, 7)
        )
        newest_time = datetime(2026, 1, 3, tzinfo=timezone.utc)
        middle_time = datetime(2026, 1, 2, tzinfo=timezone.utc)
        older_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        oldest_time = datetime(2025, 12, 31, tzinfo=timezone.utc)
        inserted_time = datetime(2026, 1, 4, tzinfo=timezone.utc)
        first_source = MemorySource(
            first_conversation.id,
            first_messages[0].id,
            first_messages[1].id,
        )
        second_first_source = MemorySource(
            first_conversation.id,
            first_messages[2].id,
            first_messages[3].id,
        )
        other_source = MemorySource(
            second_conversation.id,
            second_messages[0].id,
            second_messages[1].id,
        )

        def insert_memory(
            memory_id: UUID,
            scope: str,
            status: str,
            source: MemorySource,
            created_at: datetime,
        ) -> None:
            with self.database.connection() as connection:
                connection.execute(
                    "INSERT INTO memories "
                    "(id, kind, scope, status, content, tags, confidence, "
                    "fingerprint, source_conversation_id, "
                    "source_user_message_id, source_assistant_message_id, "
                    "created_at, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s)",
                    (
                        memory_id,
                        "fact",
                        scope,
                        status,
                        f"memory-{memory_id.int}",
                        [],
                        1.0,
                        f"{memory_id.int:064x}",
                        source.conversation_id,
                        source.user_message_id,
                        source.assistant_message_id,
                        created_at,
                        created_at,
                    ),
                )

        for values in (
            (newest, "global", "active", first_source, newest_time),
            (disabled, "global", "disabled", second_first_source, newest_time),
            (scoped, "conversation", "active", first_source, middle_time),
            (older, "conversation", "active", other_source, older_time),
            (oldest, "global", "active", other_source, oldest_time),
        ):
            insert_memory(*values)

        store = MemoryStore(self.database)
        epoch = store.get_epoch()
        first_page = store.list(page_size=2)
        self.assertEqual(
            tuple(item.id for item in first_page.items),
            (newest, disabled),
        )
        self.assertIsNotNone(first_page.next_cursor)

        insert_memory(inserted, "global", "active", other_source, inserted_time)
        seen = list(first_page.items)
        cursor = first_page.next_cursor
        while cursor is not None:
            page = store.list(page_size=2, before=cursor)
            seen.extend(page.items)
            cursor = page.next_cursor
        self.assertEqual(
            tuple(item.id for item in seen),
            (newest, disabled, scoped, older, oldest),
        )
        self.assertEqual(len({item.id for item in seen}), 5)

        exact_page = store.list(page_size=6)
        self.assertEqual(
            tuple(item.id for item in exact_page.items),
            (inserted, newest, disabled, scoped, older, oldest),
        )
        self.assertIsNone(exact_page.next_cursor)
        self.assertEqual(
            tuple(item.id for item in store.list(status="disabled").items),
            (disabled,),
        )
        self.assertEqual(
            tuple(
                item.id
                for item in store.list(scope="conversation", page_size=100).items
            ),
            (scoped, older),
        )
        self.assertEqual(
            tuple(
                item.id
                for item in store.list(
                    source_conversation_id=first_conversation.id
                ).items
            ),
            (newest, disabled, scoped),
        )
        for source_message_id in (
            first_source.user_message_id,
            first_source.assistant_message_id,
        ):
            self.assertEqual(
                tuple(
                    item.id
                    for item in store.list(
                        source_message_id=source_message_id
                    ).items
                ),
                (newest, scoped),
            )
        combined = store.list(
            status="active",
            scope="conversation",
            source_conversation_id=first_conversation.id,
            source_message_id=first_source.user_message_id,
        )
        self.assertEqual(tuple(item.id for item in combined.items), (scoped,))
        self.assertEqual(
            store.list(source_conversation_id=uuid4()).items,
            (),
        )
        self.assertEqual(store.get_epoch(), epoch)

    def test_memory_store_updates_disables_and_deletes_atomically(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        source_messages = MessageStore(self.database).add_batch(
            conversation.id,
            (ChatMessage("user", "question"), ChatMessage("assistant", "answer")),
        )
        source = MemorySource(
            conversation.id,
            source_messages[0].id,
            source_messages[1].id,
        )
        store = MemoryStore(self.database)
        original = store.create(
            MemoryCreate(
                kind="fact",
                scope="global",
                content="Original content.",
                source=source,
                tags=("original",),
            )
        ).record
        duplicate = store.create(
            MemoryCreate(
                kind="fact",
                scope="global",
                content="Duplicate content.",
                source=source,
            )
        ).record
        old_time = datetime(2020, 1, 1, tzinfo=timezone.utc)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE memories SET created_at = %s, updated_at = %s "
                "WHERE id IN (%s, %s)",
                (old_time, old_time, original.id, duplicate.id),
            )
        original = store.get(original.id)
        duplicate = store.get(duplicate.id)
        self.assertEqual(store.get_epoch(), 2)

        with self.database.connection() as connection:
            connection.execute("DELETE FROM memory_epochs WHERE name = 'memory'")
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.update(original.id, MemoryPatch(content="Must roll back"))
        self.assertEqual(store.get(original.id), original)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO memory_epochs (name, value) VALUES ('memory', %s)",
                (2,),
            )

        updated = store.update(
            original.id,
            MemoryPatch(
                content="  Updated\ncontent. ",
                kind="preference",
                tags=(" Style ", "STYLE"),
                confidence=0.5,
            ),
        )
        self.assertEqual(updated.kind, "preference")
        self.assertEqual(updated.content, "Updated content.")
        self.assertEqual(updated.tags, ("style",))
        self.assertEqual(updated.confidence, 0.5)
        self.assertEqual(
            updated.fingerprint,
            hashlib.sha256("global\nUpdated content.".encode("utf-8")).hexdigest(),
        )
        self.assertEqual(updated.scope, original.scope)
        self.assertEqual(updated.source, original.source)
        self.assertEqual(updated.created_at, original.created_at)
        self.assertGreater(updated.updated_at, original.updated_at)
        self.assertEqual(store.get_epoch(), 3)

        self.assertEqual(store.update(updated.id, MemoryPatch()), updated)
        self.assertEqual(
            store.update(
                updated.id,
                MemoryPatch(
                    content="Updated   content.",
                    kind="preference",
                    tags=("STYLE",),
                    confidence=0.5,
                ),
            ),
            updated,
        )
        self.assertEqual(store.get_epoch(), 3)

        cleared = store.update(updated.id, MemoryPatch(tags=()))
        self.assertEqual(cleared.tags, ())
        self.assertEqual(store.get_epoch(), 4)
        for patch, reason in (
            (MemoryPatch(content=" "), "content_length"),
            (MemoryPatch(confidence=True), "confidence_type"),
        ):
            with self.subTest(reason=reason), self.assertRaises(
                MemoryValidationError
            ) as caught:
                store.update(cleared.id, patch)
            self.assertEqual(caught.exception.reason, reason)
        with self.assertRaises(MemoryNotFoundError):
            store.update(uuid4(), MemoryPatch(kind="fact"))
        with self.assertRaises(MemoryDuplicateError) as caught:
            store.update(cleared.id, MemoryPatch(content=duplicate.content))
        self.assertEqual(caught.exception.existing_id, duplicate.id)
        self.assertEqual(store.get(cleared.id), cleared)
        self.assertEqual(store.get_epoch(), 4)

        with self.database.connection() as connection:
            connection.execute("DELETE FROM memory_epochs WHERE name = 'memory'")
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.disable(cleared.id)
        self.assertEqual(store.get(cleared.id), cleared)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO memory_epochs (name, value) VALUES ('memory', %s)",
                (4,),
            )

        disabled = store.disable(cleared.id)
        self.assertEqual(disabled.status, "disabled")
        self.assertGreater(disabled.updated_at, cleared.updated_at)
        self.assertEqual(store.get_epoch(), 5)
        self.assertEqual(store.disable(disabled.id), disabled)
        self.assertEqual(store.get_epoch(), 5)
        with self.assertRaises(MemoryNotFoundError):
            store.disable(uuid4())

        with self.database.connection() as connection:
            connection.execute("DELETE FROM memory_epochs WHERE name = 'memory'")
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.delete(duplicate.id)
        self.assertEqual(store.get(duplicate.id), duplicate)
        with self.database.connection() as connection:
            connection.execute(
                "INSERT INTO memory_epochs (name, value) VALUES ('memory', %s)",
                (5,),
            )

        self.assertTrue(store.delete(duplicate.id))
        self.assertEqual(store.get_epoch(), 6)
        self.assertFalse(store.delete(duplicate.id))
        self.assertFalse(store.delete(uuid4()))
        self.assertEqual(store.get_epoch(), 6)
        with self.assertRaises(MemoryNotFoundError):
            store.get(duplicate.id)

    def test_memory_store_supersedes_explicit_old_fact_atomically(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        first_conversation = conversations.create()
        second_conversation = conversations.create()
        first_messages = messages.add_batch(
            first_conversation.id,
            (ChatMessage("user", "first"), ChatMessage("assistant", "answer")),
        )
        second_messages = messages.add_batch(
            second_conversation.id,
            (ChatMessage("user", "second"), ChatMessage("assistant", "answer")),
        )
        first_source = MemorySource(
            first_conversation.id,
            first_messages[0].id,
            first_messages[1].id,
        )
        second_source = MemorySource(
            second_conversation.id,
            second_messages[0].id,
            second_messages[1].id,
        )
        store = MemoryStore(self.database)

        old_global = store.create(
            MemoryCreate("fact", "global", "Old global fact", first_source)
        ).record
        duplicate_target = store.create(
            MemoryCreate("fact", "global", "Existing target", first_source)
        ).record
        old_for_failures = store.create(
            MemoryCreate("fact", "global", "Still valid", first_source)
        ).record
        old_disabled = store.create(
            MemoryCreate("fact", "global", "Disabled old", first_source)
        ).record
        store.disable(old_disabled.id)
        old_conversation = store.create(
            MemoryCreate(
                "fact",
                "conversation",
                "Conversation old",
                first_source,
            )
        ).record

        epoch = store.get_epoch()
        replacement = store.supersede(
            old_global.id,
            MemoryCreate(
                "preference",
                "global",
                "New global fact",
                second_source,
                tags=("updated",),
            ),
        )

        self.assertEqual(replacement.supersedes_id, old_global.id)
        self.assertEqual(replacement.status, "active")
        self.assertEqual(store.get(old_global.id).status, "disabled")
        self.assertEqual(store.get_epoch(), epoch + 1)

        failures = (
            (
                uuid4(),
                MemoryCreate("fact", "global", "Missing old", first_source),
                "old_not_found",
            ),
            (
                old_disabled.id,
                MemoryCreate("fact", "global", "Disabled replacement", first_source),
                "old_disabled",
            ),
            (
                old_for_failures.id,
                MemoryCreate(
                    "fact",
                    "conversation",
                    "Wrong scope",
                    first_source,
                ),
                "scope_mismatch",
            ),
            (
                old_conversation.id,
                MemoryCreate(
                    "fact",
                    "conversation",
                    "Wrong conversation",
                    second_source,
                ),
                "conversation_mismatch",
            ),
            (
                old_for_failures.id,
                MemoryCreate(
                    "fact",
                    "global",
                    duplicate_target.content,
                    first_source,
                ),
                "duplicate_target",
            ),
        )
        unchanged_epoch = store.get_epoch()
        for old_id, data, reason in failures:
            with self.subTest(reason=reason), self.assertRaises(
                MemorySupersedeError
            ) as caught:
                store.supersede(old_id, data)
            self.assertEqual(caught.exception.reason, reason)
            self.assertEqual(store.get_epoch(), unchanged_epoch)
        self.assertEqual(store.get(old_for_failures.id).status, "active")
        self.assertEqual(store.get(old_conversation.id).status, "active")

        rollback_old = store.create(
            MemoryCreate("fact", "global", "Rollback old", first_source)
        ).record
        rollback_epoch = store.get_epoch()
        with self.database.connection() as connection:
            connection.execute("DELETE FROM memory_epochs WHERE name = 'memory'")
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.supersede(
                rollback_old.id,
                MemoryCreate("fact", "global", "Must not persist", first_source),
            )
        self.assertEqual(store.get(rollback_old.id).status, "active")
        with self.database.connection() as connection:
            rejected_count = connection.execute(
                "SELECT count(*) FROM memories WHERE content = 'Must not persist'"
            ).fetchone()[0]
            connection.execute(
                "INSERT INTO memory_epochs (name, value) VALUES ('memory', %s)",
                (rollback_epoch,),
            )
        self.assertEqual(rejected_count, 0)

    def test_memory_store_concurrent_create_is_exactly_once(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        source_messages = MessageStore(self.database).add_batch(
            conversation.id,
            (ChatMessage("user", "question"), ChatMessage("assistant", "answer")),
        )
        data = MemoryCreate(
            kind="fact",
            scope="global",
            content="Concurrent fact",
            source=MemorySource(
                conversation.id,
                source_messages[0].id,
                source_messages[1].id,
            ),
        )
        store = MemoryStore(self.database)

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(store.create, (data, data)))

        self.assertEqual(sorted(result.created for result in results), [False, True])
        self.assertEqual(len({result.record.id for result in results}), 1)
        self.assertEqual(store.get_epoch(), 1)
        with self.database.connection() as connection:
            count = connection.execute("SELECT count(*) FROM memories").fetchone()[0]
        self.assertEqual(count, 1)

    def test_memory_store_rejects_bad_sources_and_rolls_back_epoch_failure(
        self,
    ) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        messages = MessageStore(self.database)
        conversation = conversations.create()
        other_conversation = conversations.create()
        source_messages = messages.add_batch(
            conversation.id,
            (
                ChatMessage("user", "valid user"),
                ChatMessage("assistant", "valid assistant"),
                ChatMessage("assistant", "early assistant"),
                ChatMessage("user", "late user"),
            ),
        )
        other_messages = messages.add_batch(
            other_conversation.id,
            (ChatMessage("user", "other"), ChatMessage("assistant", "answer")),
        )
        store = MemoryStore(self.database)
        cases = (
            (
                MemorySource(
                    conversation.id,
                    uuid4(),
                    source_messages[1].id,
                ),
                "source_user_missing",
            ),
            (
                MemorySource(
                    conversation.id,
                    source_messages[0].id,
                    uuid4(),
                ),
                "source_assistant_missing",
            ),
            (
                MemorySource(
                    conversation.id,
                    source_messages[1].id,
                    source_messages[2].id,
                ),
                "source_user_role",
            ),
            (
                MemorySource(
                    conversation.id,
                    source_messages[0].id,
                    source_messages[3].id,
                ),
                "source_assistant_role",
            ),
            (
                MemorySource(
                    conversation.id,
                    source_messages[3].id,
                    source_messages[2].id,
                ),
                "source_order",
            ),
            (
                MemorySource(
                    conversation.id,
                    other_messages[0].id,
                    source_messages[1].id,
                ),
                "source_user_missing",
            ),
        )
        for source, reason in cases:
            with self.subTest(reason=reason), self.assertRaises(
                MemoryValidationError
            ) as caught:
                store.create(
                    MemoryCreate(
                        kind="fact",
                        scope="global",
                        content=f"Rejected {reason}",
                        source=source,
                    )
                )
            self.assertEqual(caught.exception.reason, reason)
        self.assertEqual(store.get_epoch(), 0)

        valid_source = MemorySource(
            conversation.id,
            source_messages[0].id,
            source_messages[1].id,
        )
        with self.database.connection() as connection:
            connection.execute("DELETE FROM memory_epochs WHERE name = 'memory'")
        with self.assertRaisesRegex(RuntimeError, "memory epoch is missing"):
            store.create(
                MemoryCreate(
                    kind="fact",
                    scope="global",
                    content="Must roll back",
                    source=valid_source,
                )
            )
        with self.database.connection() as connection:
            count = connection.execute("SELECT count(*) FROM memories").fetchone()[0]
        self.assertEqual(count, 0)

    def test_conversation_store_survives_a_new_database_pool(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        created = ConversationStore(self.database).create()

        second_database = Database(self.test_dsn)
        second_database.open()
        self.addCleanup(second_database.close)
        loaded = ConversationStore(second_database).get(created.id)

        self.assertEqual(loaded, created)
        self.assertEqual(loaded.next_message_sequence, 1)
        self.assertIsNotNone(loaded.created_at.utcoffset())

    def test_conversation_management_pages_updates_and_cascades(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        created = tuple(conversations.create(f"Chat {index}") for index in range(3))
        same_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
        with self.database.connection() as connection:
            connection.execute(
                "UPDATE conversations SET created_at = %s",
                (same_time,),
            )

        first = conversations.list(page_size=2)
        second = conversations.list(page_size=2, before=first.next_cursor)
        expected_ids = sorted((item.id for item in created), reverse=True)
        self.assertEqual(
            [item.id for item in (*first.items, *second.items)],
            expected_ids,
        )
        self.assertEqual(
            first.next_cursor,
            ConversationCursor(same_time, first.items[-1].id),
        )

        target = created[0]
        updated = conversations.update(
            target.id,
            ConversationPatch(title="  Archived chat  ", status="archived"),
        )
        self.assertEqual((updated.title, updated.status), ("Archived chat", "archived"))
        self.assertEqual(
            conversations.list(status="archived").items,
            (updated,),
        )

        messages = MessageStore(self.database).add_batch(
            target.id,
            (ChatMessage("user", "question"), ChatMessage("assistant", "answer")),
        )
        memory = MemoryStore(self.database).create(
            MemoryCreate(
                kind="fact",
                scope="conversation",
                content="Delete with its source conversation",
                source=MemorySource(target.id, messages[0].id, messages[1].id),
            )
        ).record
        conversations.delete(target.id)

        with self.database.connection() as connection:
            counts = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM conversation_messages "
                "WHERE conversation_id = %s), "
                "(SELECT count(*) FROM memories WHERE id = %s)",
                (target.id, memory.id),
            ).fetchone()
        self.assertEqual(counts, (0, 0))
        with self.assertRaises(ConversationNotFoundError):
            conversations.get(target.id)
        with self.assertRaises(ConversationNotFoundError):
            conversations.delete(target.id)

    def test_message_store_writes_lists_pages_and_keeps_sequences(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        store = MessageStore(self.database)

        first = store.add_batch(
            conversation.id,
            (
                ChatMessage("user", "one"),
                ChatMessage("assistant", "two"),
                ChatMessage("user", "three"),
                ChatMessage("assistant", "four"),
            ),
        )
        second = store.add_batch(
            conversation.id,
            (
                ChatMessage("user", "five"),
                ChatMessage("assistant", "six"),
                ChatMessage("user", "seven"),
            ),
        )

        self.assertEqual(
            tuple(message.sequence for message in (*first, *second)),
            (1, 2, 3, 4, 5, 6, 7),
        )
        self.assertEqual(
            tuple(message.sequence for message in store.list(conversation.id, 3)),
            (5, 6, 7),
        )
        newest = store.page(conversation.id, 3)
        middle = store.page(
            conversation.id,
            3,
            before_sequence=newest.before_sequence,
        )
        oldest = store.page(
            conversation.id,
            3,
            before_sequence=middle.before_sequence,
        )
        self.assertEqual(
            tuple(message.sequence for message in newest.items),
            (5, 6, 7),
        )
        self.assertEqual(newest.before_sequence, 5)
        self.assertEqual(
            tuple(message.sequence for message in middle.items),
            (2, 3, 4),
        )
        self.assertEqual(middle.before_sequence, 2)
        self.assertEqual(
            tuple(message.sequence for message in oldest.items),
            (1,),
        )
        self.assertIsNone(oldest.before_sequence)

        with self.database.connection() as connection:
            connection.execute(
                "DELETE FROM conversation_messages WHERE conversation_id = %s",
                (conversation.id,),
            )
        after_clear = store.add_batch(
            conversation.id,
            (ChatMessage("user", "eight"),),
        )
        self.assertEqual(after_clear[0].sequence, 8)

    def test_message_store_rolls_back_the_whole_batch(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        store = MessageStore(self.database)

        with self.assertRaises(CheckViolation):
            store.add_batch(
                conversation.id,
                (
                    ChatMessage("user", "valid"),
                    ChatMessage("invalid", "rejected"),  # type: ignore[arg-type]
                ),
            )

        with self.database.connection() as connection:
            state = connection.execute(
                "SELECT next_message_sequence, "
                "(SELECT count(*) FROM conversation_messages "
                "WHERE conversation_id = %s) "
                "FROM conversations WHERE id = %s",
                (conversation.id, conversation.id),
            ).fetchone()
        self.assertEqual(state, (1, 0))

        missing_id = uuid4()
        with self.assertRaises(ConversationNotFoundError):
            store.add_batch(missing_id, (ChatMessage("user", "missing"),))
        with self.assertRaises(ConversationNotFoundError):
            store.list(missing_id)

    def test_message_store_allocates_unique_concurrent_ranges(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        store = MessageStore(self.database)
        batches = (
            (ChatMessage("user", "a"), ChatMessage("assistant", "b")),
            (ChatMessage("user", "c"), ChatMessage("assistant", "d")),
        )

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(
                executor.map(
                    lambda batch: store.add_batch(conversation.id, batch),
                    batches,
                )
            )

        self.assertEqual(
            sorted(message.sequence for batch in results for message in batch),
            [1, 2, 3, 4],
        )
        for batch in results:
            self.assertEqual(batch[1].sequence, batch[0].sequence + 1)

    def test_postgres_session_persists_pops_clears_and_rebuilds(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        store = MessageStore(self.database)
        session = PostgresSession(conversation.id, store)
        self.assertIs(RunConfig(session=session).session, session)

        session.add_items(
            [
                ChatMessage("system", "rules"),
                {"role": "user", "content": "question"},
                SimpleNamespace(role="assistant", content="answer"),
            ]
        )
        rebuilt = PostgresSession(conversation.id, MessageStore(self.database))
        self.assertEqual(
            rebuilt.get_items(),
            [
                ChatMessage("system", "rules"),
                ChatMessage("user", "question"),
                ChatMessage("assistant", "answer"),
            ],
        )
        self.assertEqual(
            rebuilt.get_items(2),
            [
                ChatMessage("user", "question"),
                ChatMessage("assistant", "answer"),
            ],
        )
        self.assertEqual(
            rebuilt.pop_item(),
            ChatMessage("assistant", "answer"),
        )

        rebuilt.clear_session()
        self.assertEqual(rebuilt.get_items(), [])
        self.assertIsNone(rebuilt.pop_item())
        rebuilt.add_items([ChatMessage("user", "after clear")])
        remaining = store.list(conversation.id)
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0].sequence, 4)
        self.assertEqual(
            ConversationStore(self.database).get(conversation.id)
            .next_message_sequence,
            5,
        )

        missing = PostgresSession(uuid4(), store)
        for operation in (
            missing.get_items,
            missing.pop_item,
            missing.clear_session,
        ):
            with self.subTest(operation=operation):
                with self.assertRaises(ConversationNotFoundError):
                    operation()

    def test_runner_recovers_postgres_history_with_new_dependencies(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        first_store = MessageStore(self.database)
        first_model = RecordingModel(
            [
                ToolCall("echo_text", {"text": "first tool result"}, "call-1"),
                ToolCall("final_answer", {"answer": "first answer"}, "call-2"),
            ]
        )
        registry = ToolRegistry()
        registry.register(echo_tool())

        first_result = asyncio.run(
            Runner.run_async(
                Agent(
                    memory=AgentMemory(),
                    model=first_model,
                    tool_registry=registry,
                ),
                "first question",
                RunConfig(
                    session=PostgresSession(conversation.id, first_store)
                ),
            )
        )
        first_messages = first_store.list(conversation.id)

        second_database = Database(self.test_dsn)
        second_database.open()
        self.addCleanup(second_database.close)
        second_store = MessageStore(second_database)
        second_model = RecordingModel(
            [ToolCall("final_answer", {"answer": "second answer"}, "call-3")]
        )
        second_result = asyncio.run(
            Runner.run_async(
                Agent(memory=AgentMemory(), model=second_model),
                "second question",
                RunConfig(
                    session=PostgresSession(conversation.id, second_store)
                ),
            )
        )

        self.assertEqual(first_result.final_answer, "first answer")
        self.assertEqual(second_result.final_answer, "second answer")
        self.assertEqual(
            tuple(message.role for message in first_messages),
            (
                "user",
                "tool_call",
                "tool_response",
                "tool_call",
                "tool_response",
                "assistant",
            ),
        )
        self.assertEqual(
            second_model.message_batches[0],
            [
                *(
                    ChatMessage(message.role, message.content)
                    for message in first_messages
                ),
                ChatMessage("user", "second question"),
            ],
        )
        saved = second_store.list(conversation.id)
        self.assertEqual(
            tuple(message.sequence for message in saved),
            tuple(range(1, 11)),
        )
        self.assertEqual(
            tuple(message.role for message in saved),
            (
                "user",
                "tool_call",
                "tool_response",
                "tool_call",
                "tool_response",
                "assistant",
                "user",
                "tool_call",
                "tool_response",
                "assistant",
            ),
        )

    def test_runner_model_error_currently_saves_the_user_input(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversation = ConversationStore(self.database).create()
        store = MessageStore(self.database)

        result = asyncio.run(
            Runner.run_async(
                Agent(
                    memory=AgentMemory(),
                    model=RecordingModel([RuntimeError("model unavailable")]),
                ),
                "unsuccessful question",
                RunConfig(session=PostgresSession(conversation.id, store)),
            )
        )

        self.assertFalse(result.reached_final_answer)
        self.assertEqual(result.new_items[-1].item_type, "model_error")
        self.assertEqual(
            tuple(
                (message.role, message.content)
                for message in store.list(conversation.id)
            ),
            (("user", "unsuccessful question"),),
        )

    def test_chat_service_commits_only_successful_runner_turns(self) -> None:
        migrations_path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "agents"
            / "chat_backend"
            / "migrations"
        )
        MigrationRunner(self.database, migrations_path).upgrade()
        conversations = ConversationStore(self.database)
        conversation = conversations.create()
        store = MessageStore(self.database)
        first_model = RecordingModel(
            [
                ToolCall("missing_tool", {}, "call-service-1"),
                ToolCall(
                    "final_answer",
                    {"answer": "recovered answer"},
                    "call-service-2",
                ),
            ]
        )
        second_model = RecordingModel(
            [
                ToolCall(
                    "final_answer",
                    {"answer": "second answer"},
                    "call-service-3",
                )
            ]
        )
        models = iter(
            (
                first_model,
                second_model,
                RecordingModel([RuntimeError("model unavailable")]),
                RecordingModel([]),
            )
        )
        service = ChatService(
            lambda: Agent(memory=AgentMemory(), model=next(models)),
            conversations,
            store,
        )

        first_reply = asyncio.run(
            service.send_message(conversation.id, "  first question  ")
        )
        first_messages = store.list(conversation.id)

        second_reply = asyncio.run(
            service.send_message(conversation.id, "second question")
        )
        saved = store.list(conversation.id)

        self.assertEqual(first_reply.message_id, first_messages[-1].id)
        self.assertEqual(first_reply.sequence, 6)
        self.assertEqual(first_reply.content, "recovered answer")
        self.assertEqual(
            tuple(message.role for message in first_messages),
            (
                "user",
                "tool_call",
                "tool_response",
                "tool_call",
                "tool_response",
                "assistant",
            ),
        )
        self.assertEqual(
            second_model.message_batches[0],
            [
                *(
                    ChatMessage(message.role, message.content)
                    for message in first_messages
                ),
                ChatMessage("user", "second question"),
            ],
        )
        self.assertEqual(second_reply.message_id, saved[-1].id)
        self.assertEqual(second_reply.sequence, 10)
        self.assertEqual(
            tuple(message.sequence for message in saved),
            tuple(range(1, 11)),
        )

        before_failure = saved
        for expected_error in ("model unavailable", "no final answer"):
            with self.subTest(expected_error=expected_error):
                with self.assertRaises(ChatRunError):
                    asyncio.run(
                        service.send_message(
                            conversation.id,
                            "unsuccessful question",
                        )
                    )
                self.assertEqual(store.list(conversation.id), before_failure)


if __name__ == "__main__":
    unittest.main()
