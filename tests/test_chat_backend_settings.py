from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import agents
import agents.chat_backend as chat_backend
from agents.chat_backend import conversation, planning_store
from agents.chat_backend.settings import AppSettings, HealthStatus, SettingsError


WORKSPACE_ROOT = str(Path(__file__).resolve().parents[1])


def app_settings(environ: dict[str, str]) -> AppSettings:
    return AppSettings.from_env(
        {"MYAGENT_WORKSPACE_ROOT": WORKSPACE_ROOT, **environ}
    )


class ChatBackendSettingsTests(unittest.TestCase):
    def test_package_exports_only_the_stable_chat_backend_api(self) -> None:
        self.assertEqual(
            chat_backend.__all__,
            (
                "ActivePlanningRunError",
                "AppSettings",
                "ChatInputError",
                "ChatPersistenceError",
                "ChatReply",
                "ChatRunError",
                "ChatService",
                "Citation",
                "Conversation",
                "ConversationArchivedError",
                "ConversationCursor",
                "ConversationMode",
                "ConversationNotFoundError",
                "ConversationPage",
                "ConversationPatch",
                "ConversationStatus",
                "ConversationStore",
                "CorruptPlanningRunError",
                "Database",
                "ExtractionReport",
                "HealthStatus",
                "HybridRetrievalService",
                "IndexReport",
                "InvalidPlanningTransition",
                "KnowledgeChunk",
                "KnowledgeChunkDraft",
                "KnowledgeCursor",
                "KnowledgeIndexer",
                "KnowledgeNotFoundError",
                "KnowledgePage",
                "KnowledgeRetrievalService",
                "KnowledgeSearchHit",
                "KnowledgeSource",
                "KnowledgeSourceCreate",
                "KnowledgeStatus",
                "KnowledgeStore",
                "KnowledgeValidationError",
                "MemoryCandidate",
                "MemoryCreate",
                "MemoryCursor",
                "MemoryDuplicateError",
                "MemoryExtractionService",
                "MemoryKind",
                "MemoryNotFoundError",
                "MemoryPage",
                "MemoryPatch",
                "MemoryRecord",
                "MemoryRetrievalService",
                "MemorySearchHit",
                "MemorySaveResult",
                "MemoryScope",
                "MemorySource",
                "MemoryStatus",
                "MemoryStore",
                "MemorySupersedeError",
                "MemoryValidationError",
                "MessagePage",
                "MessageStore",
                "ModelProfile",
                "ModelProfileError",
                "ModelProfileNotFoundError",
                "ModelProfileStore",
                "ModelProfileStoreError",
                "PROVIDER_PRESETS",
                "MigrationError",
                "MigrationRunner",
                "PlanStore",
                "PlanningConflictError",
                "PlanningApprovalSnapshot",
                "PlanningDraftOutcome",
                "PlanningInputError",
                "PlanningProgressEvent",
                "PlanningRecoveryReport",
                "PlanningRun",
                "PlanningRunError",
                "PlanningRunNotFoundError",
                "PlanningService",
                "PlanningSnapshot",
                "PlanningStepSnapshot",
                "PlanningStatus",
                "PlanningVersion",
                "PostgresSession",
                "RetrievalBundle",
                "RetrievalHit",
                "SettingsError",
                "StoredMessage",
                "create_app",
                "create_memory_extraction_agent",
                "parse_citations",
                "reciprocal_rank_fusion",
                "split_knowledge_text",
            ),
        )
        for name in (
            "MEMORY_EXTRACTION_SCHEMA",
            "_Migration",
            "_build_extraction_input",
            "_memory_from_row",
            "_normalize_content",
            "memory_fingerprint",
            "parse_memory_candidates",
            "_CITATION_MARKER_PATTERN",
            "_build_bundle",
            "_knowledge_retrieval_hit",
            "_memory_retrieval_hit",
            "_ACTIVE_PLANNING_STATUSES",
            "_lock_run",
            "_planning_run_from_row",
            "_update_run",
        ):
            self.assertFalse(hasattr(chat_backend, name))

        planning_public = {
            "ActivePlanningRunError",
            "ConversationMode",
            "CorruptPlanningRunError",
            "InvalidPlanningTransition",
            "PlanStore",
            "PlanningConflictError",
            "PlanningRun",
            "PlanningRunNotFoundError",
            "PlanningStatus",
            "PlanningVersion",
        }
        for name in planning_public:
            with self.subTest(name=name):
                source = conversation if name == "ConversationMode" else planning_store
                self.assertIs(getattr(chat_backend, name), getattr(source, name))
        self.assertTrue(planning_public.isdisjoint(set(agents.__all__)))

    def test_settings_error_is_a_value_error(self) -> None:
        self.assertIsInstance(SettingsError("invalid"), ValueError)

    def test_health_status_accepts_the_three_states(self) -> None:
        for value in ("ready", "unavailable", "disabled"):
            with self.subTest(value=value):
                status = HealthStatus(service="postgres", status=value)
                self.assertEqual(status.status, value)
                self.assertIsNone(status.detail)

    def test_health_status_rejects_invalid_values(self) -> None:
        for service in ("", "   "):
            with self.subTest(service=service):
                with self.assertRaisesRegex(ValueError, "non-empty"):
                    HealthStatus(service=service, status="ready")

        with self.assertRaisesRegex(ValueError, "ready, unavailable, or disabled"):
            HealthStatus(
                service="postgres",
                status="unknown",  # type: ignore[arg-type]
            )

    def test_health_status_is_frozen(self) -> None:
        status = HealthStatus(service="postgres", status="ready")

        with self.assertRaises(FrozenInstanceError):
            status.detail = "changed"  # type: ignore[misc]

    def test_app_settings_uses_complete_defaults(self) -> None:
        settings = app_settings(
            {"MYAGENT_POSTGRES_DSN": "  postgresql://localhost/myagent  "}
        )

        self.assertEqual(settings.postgres_dsn, "postgresql://localhost/myagent")
        self.assertEqual(settings.workspace_root, Path(WORKSPACE_ROOT))
        self.assertEqual(settings.workspace_test_command, "python -m pytest")
        self.assertEqual(settings.embedding_model, "text-embedding-3-small")
        self.assertEqual(settings.embedding_dimensions, 1024)
        self.assertEqual(settings.http_host, "127.0.0.1")
        self.assertEqual(settings.http_port, 8000)
        self.assertEqual(settings.cors_origins, ())
        self.assertEqual(settings.retrieval_top_k, 8)
        self.assertEqual(settings.max_context_chunks, 4)
        self.assertEqual(settings.max_context_chars, 12_000)
        self.assertFalse(settings.memory_retrieval_enabled)
        self.assertFalse(settings.allow_real_model_smoke)
        with self.assertRaises(FrozenInstanceError):
            settings.http_port = 9000  # type: ignore[misc]

    def test_app_settings_accepts_explicit_values(self) -> None:
        settings = app_settings(
            {
                "MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent",
                "MYAGENT_EMBEDDING_MODEL": "custom-embedding-model",
                "MYAGENT_EMBEDDING_DIMENSIONS": "1024",
                "MYAGENT_HTTP_HOST": "127.0.0.1",
                "MYAGENT_HTTP_PORT": "9000",
                "MYAGENT_WORKSPACE_TEST_COMMAND": (
                    "python -m pytest tests/test_chat_web.py"
                ),
                "MYAGENT_CORS_ORIGINS": (
                    "http://localhost:3000, https://127.0.0.1:8443,"
                    "http://[::1]:5173"
                ),
                "MYAGENT_RETRIEVAL_TOP_K": "10",
                "MYAGENT_MAX_CONTEXT_CHUNKS": "5",
                "MYAGENT_MAX_CONTEXT_CHARS": "9000",
                "MYAGENT_MEMORY_RETRIEVAL_ENABLED": "true",
                "MYAGENT_ALLOW_REAL_MODEL_SMOKE": "YES",
            }
        )

        self.assertEqual(settings.embedding_model, "custom-embedding-model")
        self.assertEqual(settings.http_host, "127.0.0.1")
        self.assertEqual(settings.http_port, 9000)
        self.assertEqual(
            settings.workspace_test_command,
            "python -m pytest tests/test_chat_web.py",
        )
        self.assertEqual(
            settings.cors_origins,
            (
                "http://localhost:3000",
                "https://127.0.0.1:8443",
                "http://[::1]:5173",
            ),
        )
        self.assertEqual(settings.retrieval_top_k, 10)
        self.assertEqual(settings.max_context_chunks, 5)
        self.assertEqual(settings.max_context_chars, 9000)
        self.assertTrue(settings.memory_retrieval_enabled)
        self.assertTrue(settings.allow_real_model_smoke)

    def test_app_settings_reads_os_environ_only_when_mapping_is_omitted(self) -> None:
        dsn = "postgresql://localhost/from-process"
        with patch.dict(
            os.environ,
            {
                "MYAGENT_POSTGRES_DSN": dsn,
                "MYAGENT_WORKSPACE_ROOT": WORKSPACE_ROOT,
            },
            clear=True,
        ):
            self.assertEqual(AppSettings.from_env().postgres_dsn, dsn)
            with self.assertRaises(SettingsError):
                AppSettings.from_env({})

    def test_app_settings_requires_non_empty_text(self) -> None:
        with self.assertRaisesRegex(SettingsError, "MYAGENT_POSTGRES_DSN"):
            AppSettings.from_env({})

        for name in (
            "MYAGENT_EMBEDDING_MODEL",
            "MYAGENT_HTTP_HOST",
            "MYAGENT_WORKSPACE_TEST_COMMAND",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(SettingsError, name):
                    app_settings(
                        {
                            "MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent",
                            name: "   ",
                        }
                    )

        with self.assertRaisesRegex(SettingsError, "127.0.0.1"):
            app_settings(
                {
                    "MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent",
                    "MYAGENT_HTTP_HOST": "0.0.0.0",
                }
            )

        with self.assertRaisesRegex(SettingsError, "MYAGENT_WORKSPACE_ROOT"):
            AppSettings.from_env(
                {"MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent"}
            )
        with self.assertRaisesRegex(SettingsError, "existing directory"):
            AppSettings.from_env(
                {
                    "MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent",
                    "MYAGENT_WORKSPACE_ROOT": str(
                        Path(WORKSPACE_ROOT) / "missing-workspace"
                    ),
                }
            )

    def test_app_settings_errors_do_not_leak_postgres_dsn(self) -> None:
        secret_dsn = "postgresql://user:secret-password@localhost/myagent"
        with self.assertRaises(SettingsError) as caught:
            app_settings(
                {
                    "MYAGENT_POSTGRES_DSN": secret_dsn,
                    "MYAGENT_EMBEDDING_DIMENSIONS": "1536",
                }
            )
        self.assertNotIn(secret_dsn, str(caught.exception))
        self.assertNotIn("secret-password", str(caught.exception))

    def test_app_settings_validates_numbers_and_field_relationships(self) -> None:
        dsn = {"MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent"}
        for name in (
            "MYAGENT_EMBEDDING_DIMENSIONS",
            "MYAGENT_HTTP_PORT",
            "MYAGENT_RETRIEVAL_TOP_K",
            "MYAGENT_MAX_CONTEXT_CHUNKS",
            "MYAGENT_MAX_CONTEXT_CHARS",
        ):
            with self.subTest(name=name):
                with self.assertRaisesRegex(SettingsError, name):
                    app_settings({**dsn, name: "0"})

        invalid_values = (
            ("MYAGENT_HTTP_PORT", "65536"),
            ("MYAGENT_EMBEDDING_DIMENSIONS", "1536"),
            ("MYAGENT_RETRIEVAL_TOP_K", "not-an-integer"),
        )
        for name, value in invalid_values:
            with self.subTest(name=name, value=value):
                with self.assertRaisesRegex(SettingsError, name):
                    app_settings({**dsn, name: value})

        with self.assertRaisesRegex(SettingsError, "MYAGENT_MAX_CONTEXT_CHUNKS"):
            app_settings(
                {
                    **dsn,
                    "MYAGENT_RETRIEVAL_TOP_K": "3",
                    "MYAGENT_MAX_CONTEXT_CHUNKS": "4",
                }
            )

    def test_app_settings_validates_local_cors_origins(self) -> None:
        dsn = {"MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent"}
        invalid_origins = (
            "*",
            "https://example.com",
            "http://user@localhost",
            "http://localhost/path",
            "http://localhost?query=1",
            "http://localhost#fragment",
            "ftp://localhost",
            "http://localhost:not-a-port",
            "http://localhost:70000",
            "http://local host",
        )
        for origin in invalid_origins:
            with self.subTest(origin=origin):
                with self.assertRaisesRegex(SettingsError, "MYAGENT_CORS_ORIGINS"):
                    app_settings(
                        {**dsn, "MYAGENT_CORS_ORIGINS": origin}
                    )

    def test_app_settings_parses_explicit_boolean_values(self) -> None:
        dsn = {"MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent"}
        for name, field in (
            ("MYAGENT_MEMORY_RETRIEVAL_ENABLED", "memory_retrieval_enabled"),
            ("MYAGENT_ALLOW_REAL_MODEL_SMOKE", "allow_real_model_smoke"),
        ):
            for value in ("1", "true", "TRUE", "yes", "YES"):
                with self.subTest(name=name, value=value):
                    settings = app_settings({**dsn, name: value})
                    self.assertTrue(getattr(settings, field))

            for value in ("0", "false", "FALSE", "no", "NO"):
                with self.subTest(name=name, value=value):
                    settings = app_settings({**dsn, name: value})
                    self.assertFalse(getattr(settings, field))

            with self.subTest(name=name, value="sometimes"):
                with self.assertRaisesRegex(SettingsError, name):
                    app_settings({**dsn, name: "sometimes"})


if __name__ == "__main__":
    unittest.main()
