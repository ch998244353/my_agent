from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from openai import OpenAIError

from agents.chat_backend.embedding import (
    EMBEDDING_DIMENSIONS,
    EmbeddingContractError,
    EmbeddingRequestError,
    OpenAIEmbeddingClient,
    embedding_vector_literal,
)
from agents.chat_backend.settings import AppSettings


def _vector(value: float = 0.0, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    return [value] * dimensions


def _item(index: object, embedding: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        embedding=_vector() if embedding is None else embedding,
    )


class ChatBackendEmbeddingTests(unittest.TestCase):
    def _client(self, response: object) -> tuple[OpenAIEmbeddingClient, Mock]:
        create = Mock(return_value=response)
        sdk = SimpleNamespace(embeddings=SimpleNamespace(create=create))
        client = OpenAIEmbeddingClient(
            sdk,  # type: ignore[arg-type]
            " text-embedding-3-small ",
        )
        return client, create

    def test_empty_batch_returns_without_request(self) -> None:
        client, create = self._client(SimpleNamespace(data=[]))

        self.assertEqual(client.embed(()), ())
        create.assert_not_called()

    def test_batch_request_is_single_ordered_and_immutable(self) -> None:
        client, create = self._client(
            SimpleNamespace(
                data=[
                    _item(1, _vector(2.0)),
                    _item(0, _vector(1.0)),
                ]
            )
        )

        result = client.embed(["first", "second"])

        self.assertIsInstance(result, tuple)
        self.assertTrue(all(isinstance(vector, tuple) for vector in result))
        self.assertEqual(result[0][0], 1.0)
        self.assertEqual(result[1][0], 2.0)
        create.assert_called_once_with(
            model="text-embedding-3-small",
            input=("first", "second"),
            dimensions=EMBEDDING_DIMENSIONS,
        )

    def test_constructor_and_input_reject_invalid_values(self) -> None:
        sdk = SimpleNamespace(embeddings=SimpleNamespace(create=Mock()))
        for model in ("", "   ", 1):
            with self.subTest(model=model):
                with self.assertRaises(EmbeddingContractError):
                    OpenAIEmbeddingClient(sdk, model)  # type: ignore[arg-type]
        for dimensions in (0, 512, True):
            with self.subTest(dimensions=dimensions):
                with self.assertRaises(EmbeddingContractError):
                    OpenAIEmbeddingClient(
                        sdk,
                        "text-embedding-3-small",
                        dimensions,  # type: ignore[arg-type]
                    )

        client, create = self._client(SimpleNamespace(data=[]))
        invalid_batches = (
            "one string",
            b"bytes",
            ["valid", ""],
            ["valid", "   "],
            ["valid", 1],
        )
        for batch in invalid_batches:
            with self.subTest(batch=batch):
                with self.assertRaises(EmbeddingContractError):
                    client.embed(batch)  # type: ignore[arg-type]
        create.assert_not_called()

    def test_response_requires_complete_unique_in_range_indices(self) -> None:
        cases = (
            ("missing", [_item(0)]),
            ("duplicate", [_item(0), _item(0)]),
            ("negative", [_item(-1), _item(1)]),
            ("out_of_range", [_item(0), _item(2)]),
            ("boolean", [_item(False), _item(1)]),
        )
        for name, data in cases:
            with self.subTest(name=name):
                client, _ = self._client(SimpleNamespace(data=data))
                with self.assertRaises(EmbeddingContractError):
                    client.embed(("first", "second"))

    def test_response_rejects_bad_vectors_and_data(self) -> None:
        cases = (
            ("missing_data", SimpleNamespace()),
            ("wrong_dimensions", SimpleNamespace(data=[_item(0, _vector(0.0, 1023))])),
            ("nan", SimpleNamespace(data=[_item(0, _vector(float("nan")))])),
            ("infinity", SimpleNamespace(data=[_item(0, _vector(float("inf")))])),
            (
                "boolean",
                SimpleNamespace(data=[_item(0, [False] * EMBEDDING_DIMENSIONS)]),
            ),
            ("text", SimpleNamespace(data=[_item(0, ["0"] * EMBEDDING_DIMENSIONS)])),
        )
        for name, response in cases:
            with self.subTest(name=name):
                client, _ = self._client(response)
                with self.assertRaises(EmbeddingContractError):
                    client.embed(("first",))

    def test_openai_error_is_wrapped_once_with_cause(self) -> None:
        error = OpenAIError("network failed")
        create = Mock(side_effect=error)
        sdk = SimpleNamespace(embeddings=SimpleNamespace(create=create))
        client = OpenAIEmbeddingClient(
            sdk,  # type: ignore[arg-type]
            "text-embedding-3-small",
        )

        with self.assertRaises(EmbeddingRequestError) as caught:
            client.embed(("first",))

        self.assertIs(caught.exception.__cause__, error)
        create.assert_called_once()

    def test_settings_reuses_embedding_dimension_constant(self) -> None:
        settings = AppSettings.from_env(
            {
                "MYAGENT_POSTGRES_DSN": "postgresql://localhost/myagent",
                "MYAGENT_WORKSPACE_ROOT": str(Path(__file__).resolve().parents[1]),
            }
        )

        self.assertEqual(settings.embedding_dimensions, EMBEDDING_DIMENSIONS)

    def test_vector_literal_is_compact_and_reuses_contract_validation(self) -> None:
        self.assertEqual(
            embedding_vector_literal([0] * EMBEDDING_DIMENSIONS),
            "[" + ",".join(["0.0"] * EMBEDDING_DIMENSIONS) + "]",
        )

        for vector in (
            [0.0] * (EMBEDDING_DIMENSIONS - 1),
            [False] * EMBEDDING_DIMENSIONS,
            [float("nan")] * EMBEDDING_DIMENSIONS,
            [float("inf")] * EMBEDDING_DIMENSIONS,
            ["0"] * EMBEDDING_DIMENSIONS,
        ):
            with self.subTest(value=vector[0]), self.assertRaises(
                EmbeddingContractError
            ):
                embedding_vector_literal(vector)


if __name__ == "__main__":
    unittest.main()
