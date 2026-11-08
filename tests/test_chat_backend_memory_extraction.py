from __future__ import annotations

import asyncio
import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from psycopg import OperationalError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from agents.chat_backend.memory_extraction import (  # noqa: E402
    MEMORY_EXTRACTION_SCHEMA,
    CandidateSaveOutcome,
    ExtractionReport,
    MemoryCandidate,
    MemoryCandidateError,
    MemoryExtractionError,
    MemoryExtractionService,
    _build_extraction_input,
    create_memory_extraction_agent,
    parse_memory_candidates,
)
from agents.chat_backend.embedding import EMBEDDING_DIMENSIONS  # noqa: E402
from agents.chat_backend.memory import (  # noqa: E402
    MemorySearchHit,
    MemorySource,
    MemorySupersedeError,
    MemoryValidationError,
)
from agents.contracts import ModelResponse  # noqa: E402
from agents.output import output_schema_from_output_type  # noqa: E402


def candidate(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "kind": "fact",
        "scope": "global",
        "content": "Uses Python",
        "tags": ["python"],
        "confidence": 0.9,
    }
    value.update(overrides)
    return value


def embedding(value: float = 0.0) -> tuple[float, ...]:
    return (value,) * EMBEDDING_DIMENSIONS


class StructuredResponseModel:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text
        self.last_messages = []
        self.last_tool_specs = []
        self.response_schema = None
        self.response_schema_name = None
        self.response_schema_strict = False

    def get_response(self, messages, tool_specs) -> ModelResponse:
        self.last_messages = list(messages)
        self.last_tool_specs = list(tool_specs)
        return ModelResponse(
            response_id="memory-extraction",
            output=[],
            output_text=self.output_text,
            tool_calls=[],
        )


class MemoryCandidateContractTests(unittest.TestCase):
    def test_schema_is_accepted_and_candidate_is_frozen(self) -> None:
        self.assertIs(
            output_schema_from_output_type(MEMORY_EXTRACTION_SCHEMA),
            MEMORY_EXTRACTION_SCHEMA,
        )
        parsed = parse_memory_candidates({"candidates": [candidate()]})

        self.assertEqual(
            parsed,
            (MemoryCandidate("fact", "global", "Uses Python", ("python",), 0.9),),
        )
        with self.assertRaises(FrozenInstanceError):
            parsed[0].content = "changed"  # type: ignore[misc]

    def test_empty_and_eight_candidates_are_valid(self) -> None:
        self.assertEqual(parse_memory_candidates({"candidates": []}), ())
        parsed = parse_memory_candidates(
            {"candidates": [candidate(content=f"Fact {index}") for index in range(8)]}
        )
        self.assertEqual(len(parsed), 8)

    def test_values_reuse_memory_normalization(self) -> None:
        parsed = parse_memory_candidates(
            {
                "candidates": [
                    candidate(
                        content="  Cafe\u0301\nuses   Python  ",
                        tags=[" Python ", "python", "STYLE"],
                        confidence=1,
                    )
                ]
            }
        )[0]

        self.assertEqual(parsed.content, "Café uses Python")
        self.assertEqual(parsed.tags, ("python", "style"))
        self.assertEqual(parsed.confidence, 1.0)

    def test_optional_supersedes_id_is_parsed_as_uuid(self) -> None:
        old_id = uuid4()

        parsed = parse_memory_candidates(
            {"candidates": [candidate(supersedes_id=str(old_id))]}
        )

        self.assertEqual(parsed[0].supersedes_id, old_id)


class MemoryCandidateValidationTests(unittest.TestCase):
    def assert_candidate_error(
        self,
        value: object,
        reason: str,
        index: int | None,
    ) -> MemoryCandidateError:
        with self.assertRaises(MemoryCandidateError) as context:
            parse_memory_candidates(value)
        self.assertEqual(context.exception.reason, reason)
        self.assertEqual(context.exception.index, index)
        return context.exception

    def test_top_level_shape_and_count_are_exact(self) -> None:
        invalid_values = (
            None,
            [],
            {},
            {"candidates": [], "extra": True},
            {"candidates": ()},
            {"candidates": [candidate()] * 9},
        )
        for value in invalid_values:
            with self.subTest(value=type(value).__name__):
                self.assert_candidate_error(value, "invalid_shape", None)

    def test_candidate_fields_are_exact(self) -> None:
        values = (
            "candidate",
            {"kind": "fact"},
            candidate(extra=True),
            candidate(kind="memory"),
            candidate(kind=True),
            candidate(scope="account"),
            candidate(scope=1),
        )
        reasons = (
            "invalid_shape",
            "invalid_shape",
            "invalid_shape",
            "invalid_kind",
            "invalid_kind",
            "invalid_scope",
            "invalid_scope",
        )
        for value, reason in zip(values, reasons, strict=True):
            with self.subTest(reason=reason):
                self.assert_candidate_error({"candidates": [value]}, reason, 0)

    def test_content_tags_and_confidence_are_checked_locally(self) -> None:
        cases = (
            (candidate(content="   "), "invalid_content"),
            (candidate(content="x" * 2001), "invalid_content"),
            (candidate(tags="python"), "invalid_tags"),
            (candidate(tags=["tag"] * 17), "invalid_tags"),
            (candidate(tags=[""]), "invalid_tags"),
            (candidate(confidence=True), "invalid_confidence"),
            (candidate(confidence=float("nan")), "invalid_confidence"),
            (candidate(confidence=float("inf")), "invalid_confidence"),
            (candidate(confidence=-0.1), "invalid_confidence"),
            (candidate(confidence=1.1), "invalid_confidence"),
        )
        for value, reason in cases:
            with self.subTest(reason=reason):
                self.assert_candidate_error({"candidates": [value]}, reason, 0)

    def test_supersedes_id_must_be_an_optional_uuid_string(self) -> None:
        for value in (None, 1, True, "not-a-uuid"):
            with self.subTest(value=value):
                self.assert_candidate_error(
                    {"candidates": [candidate(supersedes_id=value)]},
                    "invalid_supersedes_id",
                    0,
                )

    def test_obvious_credentials_are_rejected_without_echoing_them(self) -> None:
        secrets = (
            "-----BEGIN PRIVATE KEY-----",
            "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
            "ghp_abcdefghijklmnopqrstuvwxyz012345",
            "github_pat_abcdefghijklmnopqrstuvwxyz012345",
            "AKIAIOSFODNN7EXAMPLE",
            "password = super-secret-value",
            "api_key: super-secret-value",
        )
        for secret in secrets:
            with self.subTest(secret=secret[:8]):
                error = self.assert_candidate_error(
                    {"candidates": [candidate(content=f"Credential {secret}")]},
                    "sensitive_content",
                    0,
                )
                self.assertNotIn(secret, str(error))

        tag_secret = "token=super-secret-value"
        error = self.assert_candidate_error(
            {"candidates": [candidate(tags=[tag_secret])]},
            "sensitive_content",
            0,
        )
        self.assertNotIn(tag_secret, str(error))

    def test_plain_token_words_are_not_treated_as_credentials(self) -> None:
        parsed = parse_memory_candidates(
            {
                "candidates": [
                    candidate(
                        content="The tokenizer counts text tokens",
                        tags=["token budget"],
                    )
                ]
            }
        )
        self.assertEqual(parsed[0].content, "The tokenizer counts text tokens")


class MemoryExtractionAgentTests(unittest.TestCase):
    def test_agent_is_single_step_structured_and_has_no_actions(self) -> None:
        model = StructuredResponseModel('{"candidates": []}')
        agent = create_memory_extraction_agent(model)

        self.assertEqual(agent.name, "MemoryExtraction")
        self.assertEqual(agent.max_steps, 1)
        self.assertIs(agent.output_type, MEMORY_EXTRACTION_SCHEMA)
        self.assertFalse(agent.capabilities.final_answer_tool)
        self.assertEqual(agent.handoffs, [])
        self.assertEqual(agent.tool_registry.list_specs(), [])
        self.assertIs(model.response_schema, MEMORY_EXTRACTION_SCHEMA)
        self.assertTrue(model.response_schema_strict)

    def test_input_contains_only_bounded_tagged_text(self) -> None:
        built = _build_extraction_input("  user fact  ", "  assistant reply  ")
        self.assertEqual(
            built,
            (
                "<user_message>\nuser fact\n</user_message>\n"
                "<assistant_message>\nassistant reply\n</assistant_message>"
            ),
        )
        self.assertIn(
            "&lt;/user_message&gt;",
            _build_extraction_input("</user_message>", "answer"),
        )
        self.assertEqual(
            len(_build_extraction_input("u" * 20_000, "a" * 20_000)),
            40_073,
        )

        for user_text, assistant_text in (
            (None, "answer"),
            ("question", object()),
            (" ", "answer"),
            ("question", ""),
            ("u" * 20_001, "answer"),
            ("question", "a" * 20_001),
        ):
            with self.subTest(
                user_type=type(user_text).__name__,
                assistant_type=type(assistant_text).__name__,
            ):
                with self.assertRaises(ValueError) as context:
                    _build_extraction_input(  # type: ignore[arg-type]
                        user_text,
                        assistant_text,
                    )
                self.assertNotIn(repr(user_text), str(context.exception))
                self.assertNotIn(repr(assistant_text), str(context.exception))

    def test_service_runs_real_runner_without_session_and_parses_candidates(
        self,
    ) -> None:
        model = StructuredResponseModel(
            '{"candidates":[{"kind":"preference","scope":"global",'
            '"content":"Prefers concise answers","tags":["style"],'
            '"confidence":0.95}]}'
        )
        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            return create_memory_extraction_agent(model)

        service = MemoryExtractionService(factory, MagicMock())
        result = asyncio.run(service.extract("Be concise", "Understood"))

        self.assertEqual(
            result,
            (
                MemoryCandidate(
                    "preference",
                    "global",
                    "Prefers concise answers",
                    ("style",),
                    0.95,
                ),
            ),
        )
        self.assertEqual(factory_calls, 1)
        self.assertEqual(model.last_tool_specs, [])
        self.assertEqual(
            [message.role for message in model.last_messages],
            ["system", "user"],
        )
        self.assertIn("<user_message>\nBe concise", model.last_messages[-1].content)
        self.assertNotIn("Conversation summary", model.last_messages[-1].content)

    def test_service_accepts_empty_candidates(self) -> None:
        model = StructuredResponseModel('{"candidates": []}')
        service = MemoryExtractionService(
            lambda: create_memory_extraction_agent(model),
            MagicMock(),
        )
        self.assertEqual(asyncio.run(service.extract("Hello", "Hi")), ())


class MemoryCandidateSaveTests(unittest.TestCase):
    def test_reports_created_duplicate_superseded_and_rejected_in_order(self) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        created_id = uuid4()
        duplicate_id = uuid4()
        superseded_id = uuid4()
        old_ids = (uuid4(), uuid4())
        store = MagicMock()
        store.create.side_effect = (
            SimpleNamespace(
                created=True,
                record=SimpleNamespace(id=created_id),
            ),
            SimpleNamespace(
                created=False,
                record=SimpleNamespace(id=duplicate_id),
            ),
            MemoryValidationError("content_length"),
        )
        store.supersede.side_effect = (
            SimpleNamespace(id=superseded_id),
            MemorySupersedeError("old_disabled"),
        )
        service = MemoryExtractionService(lambda: MagicMock(), store)
        candidates = (
            MemoryCandidate("fact", "global", "one", (), 1.0),
            MemoryCandidate("fact", "global", "two", (), 1.0),
            MemoryCandidate("fact", "global", "three", (), 1.0, old_ids[0]),
            MemoryCandidate("fact", "global", "four", (), 1.0, old_ids[1]),
            MemoryCandidate("fact", "global", " ", (), 1.0),
        )

        report = service.save_candidates(source, candidates)

        self.assertEqual(
            report,
            ExtractionReport(
                (
                    CandidateSaveOutcome(0, "created", created_id),
                    CandidateSaveOutcome(1, "duplicate", duplicate_id),
                    CandidateSaveOutcome(2, "superseded", superseded_id),
                    CandidateSaveOutcome(3, "rejected", None, "old_disabled"),
                    CandidateSaveOutcome(4, "rejected", None, "content_length"),
                )
            ),
        )
        self.assertIs(store.create.call_args_list[0].args[0].source, source)
        with self.assertRaises(FrozenInstanceError):
            report.outcomes = ()  # type: ignore[misc]

    def test_database_failures_are_not_reported_as_rejected(self) -> None:
        error = OperationalError("database unavailable")
        store = MagicMock()
        store.create.side_effect = error
        service = MemoryExtractionService(lambda: MagicMock(), store)

        with self.assertRaises(OperationalError) as caught:
            service.save_candidates(
                MemorySource(uuid4(), uuid4(), uuid4()),
                (MemoryCandidate("fact", "global", "one", (), 1.0),),
            )

        self.assertIs(caught.exception, error)

    def test_semantic_duplicate_boundary_and_vector_write(self) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        duplicate_id = uuid4()
        created_id = uuid4()
        store = MagicMock()
        store.find_active_exact.side_effect = (None, None)
        store.find_nearest_active.side_effect = (
            MemorySearchHit(
                SimpleNamespace(id=duplicate_id),  # type: ignore[arg-type]
                0.05,
            ),
            MemorySearchHit(
                SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
                0.0501,
            ),
        )
        store.create.return_value = SimpleNamespace(
            created=True,
            record=SimpleNamespace(id=created_id),
        )
        client = MagicMock()
        vectors = (embedding(1.0), embedding(2.0))
        client.embed.return_value = vectors
        service = MemoryExtractionService(
            lambda: MagicMock(),
            store,
            embedding_client=client,
            embedding_model=" model-a ",
        )

        report = service.save_candidates(
            source,
            (
                MemoryCandidate("fact", "global", "same meaning", (), 1.0),
                MemoryCandidate(
                    "preference",
                    "conversation",
                    "slightly different",
                    (),
                    1.0,
                ),
            ),
        )

        self.assertEqual(
            report.outcomes,
            (
                CandidateSaveOutcome(0, "duplicate", duplicate_id),
                CandidateSaveOutcome(1, "created", created_id),
            ),
        )
        client.embed.assert_called_once_with(("same meaning", "slightly different"))
        nearest_calls = store.find_nearest_active.call_args_list
        self.assertEqual(nearest_calls[0].args[1:4], ("model-a", "fact", "global"))
        self.assertEqual(
            nearest_calls[1].args[1:4],
            ("model-a", "preference", "conversation"),
        )
        store.create.assert_called_once_with(
            store.create.call_args.args[0],
            embedding=vectors[1],
            embedding_model="model-a",
        )

    def test_exact_duplicate_is_removed_before_batch_embedding(self) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        exact_id = uuid4()
        created_id = uuid4()
        store = MagicMock()
        store.find_active_exact.side_effect = (
            SimpleNamespace(id=exact_id),
            None,
        )
        store.find_nearest_active.return_value = None
        store.create.return_value = SimpleNamespace(
            created=True,
            record=SimpleNamespace(id=created_id),
        )
        client = MagicMock()
        client.embed.return_value = (embedding(),)
        service = MemoryExtractionService(
            lambda: MagicMock(),
            store,
            embedding_client=client,
            embedding_model="model-a",
        )

        report = service.save_candidates(
            source,
            (
                MemoryCandidate("fact", "global", "exact", (), 1.0),
                MemoryCandidate("fact", "global", "new", (), 1.0),
            ),
        )

        self.assertEqual(report.outcomes[0].memory_id, exact_id)
        client.embed.assert_called_once_with(("new",))
        self.assertEqual(store.create.call_count, 1)

    def test_embedding_and_vector_failures_disable_semantics_for_whole_batch(
        self,
    ) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        candidates = (
            MemoryCandidate("fact", "global", "one", (), 1.0),
            MemoryCandidate("fact", "global", "two", (), 1.0),
        )
        for failure_at in ("embedding", "vector"):
            store = MagicMock()
            store.find_active_exact.side_effect = (None, None)
            ids = (uuid4(), uuid4())
            store.create.side_effect = tuple(
                SimpleNamespace(
                    created=True,
                    record=SimpleNamespace(id=memory_id),
                )
                for memory_id in ids
            )
            client = MagicMock()
            if failure_at == "embedding":
                client.embed.side_effect = RuntimeError("network")
            else:
                client.embed.return_value = (embedding(), embedding())
                store.find_nearest_active.side_effect = (
                    MemorySearchHit(
                        SimpleNamespace(id=uuid4()),  # type: ignore[arg-type]
                        0.01,
                    ),
                    OperationalError("database"),
                )
            service = MemoryExtractionService(
                lambda: MagicMock(),
                store,
                embedding_client=client,
                embedding_model="model-a",
            )

            with self.subTest(failure_at=failure_at):
                report = service.save_candidates(source, candidates)
                self.assertEqual(
                    tuple(outcome.status for outcome in report.outcomes),
                    ("created", "created"),
                )
                self.assertEqual(store.create.call_count, 2)
                self.assertTrue(
                    all(not call.kwargs for call in store.create.call_args_list)
                )

    def test_explicit_supersede_bypasses_semantic_duplicate(self) -> None:
        source = MemorySource(uuid4(), uuid4(), uuid4())
        old_id = uuid4()
        new_id = uuid4()
        store = MagicMock()
        store.supersede.return_value = SimpleNamespace(id=new_id)
        client = MagicMock()
        service = MemoryExtractionService(
            lambda: MagicMock(),
            store,
            embedding_client=client,
            embedding_model="model-a",
        )

        report = service.save_candidates(
            source,
            (MemoryCandidate("fact", "global", "new fact", (), 1.0, old_id),),
        )

        self.assertEqual(
            report.outcomes,
            (CandidateSaveOutcome(0, "superseded", new_id),),
        )
        store.supersede.assert_called_once()
        store.find_active_exact.assert_not_called()
        store.find_nearest_active.assert_not_called()
        client.embed.assert_not_called()

    def test_semantic_constructor_rejects_partial_or_invalid_configuration(
        self,
    ) -> None:
        invalid_arguments = (
            {"embedding_client": MagicMock()},
            {"embedding_model": "model-a"},
            {"embedding_client": MagicMock(), "embedding_model": " "},
            {"semantic_duplicate_threshold": 0.0},
            {"semantic_duplicate_threshold": 1.1},
            {"semantic_duplicate_threshold": float("nan")},
            {"semantic_duplicate_threshold": True},
        )
        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                MemoryExtractionService(
                    lambda: MagicMock(),
                    MagicMock(),
                    **arguments,
                )


class MemoryExtractionFailureTests(unittest.TestCase):
    def service(self) -> MemoryExtractionService:
        return MemoryExtractionService(
            lambda: create_memory_extraction_agent(object()),
            MagicMock(),
        )

    def assert_safe_failure(self, result: object) -> MemoryExtractionError:
        runner = AsyncMock(return_value=result)
        with patch(
            "agents.chat_backend.memory_extraction.Runner.run_async",
            runner,
        ):
            with self.assertRaises(MemoryExtractionError) as context:
                asyncio.run(self.service().extract("secret user text", "answer"))
        self.assertEqual(str(context.exception), "memory extraction failed")
        self.assertNotIn("secret user text", str(context.exception))
        runner.assert_awaited_once()
        self.assertEqual(len(runner.await_args.args), 2)
        self.assertEqual(runner.await_args.kwargs, {})
        return context.exception

    def test_missing_wrong_and_invalid_final_outputs_are_safe(self) -> None:
        cases = (
            SimpleNamespace(reached_final_answer=False, final_output=None),
            SimpleNamespace(reached_final_answer=True, final_output="bad"),
            SimpleNamespace(
                reached_final_answer=True,
                final_output={"candidates": [candidate(kind="bad")]},
            ),
        )
        for result in cases:
            with self.subTest(final_output=result.final_output):
                self.assert_safe_failure(result)

    def test_factory_and_runner_errors_are_safe_and_not_retried(self) -> None:
        def failing_factory():
            raise RuntimeError("secret factory detail")

        with self.assertRaises(MemoryExtractionError) as context:
            asyncio.run(
                MemoryExtractionService(failing_factory, MagicMock()).extract(
                    "user", "assistant"
                )
            )
        self.assertEqual(str(context.exception), "memory extraction failed")

        runner = AsyncMock(side_effect=RuntimeError("secret runner detail"))
        with patch(
            "agents.chat_backend.memory_extraction.Runner.run_async",
            runner,
        ):
            with self.assertRaises(MemoryExtractionError) as context:
                asyncio.run(self.service().extract("user", "assistant"))
        self.assertEqual(str(context.exception), "memory extraction failed")
        runner.assert_awaited_once()

    def test_cancelled_error_propagates(self) -> None:
        runner = AsyncMock(side_effect=asyncio.CancelledError)
        with patch(
            "agents.chat_backend.memory_extraction.Runner.run_async",
            runner,
        ):
            with self.assertRaises(asyncio.CancelledError):
                asyncio.run(self.service().extract("user", "assistant"))
        runner.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
