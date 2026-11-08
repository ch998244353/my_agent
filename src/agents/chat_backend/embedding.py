from __future__ import annotations

import json
from collections.abc import Sequence
from math import isfinite
from typing import Protocol

from openai import OpenAI, OpenAIError


EMBEDDING_DIMENSIONS = 1024
Embedding = tuple[float, ...]
EmbeddingBatch = tuple[Embedding, ...]


class EmbeddingContractError(ValueError):
    pass


class EmbeddingRequestError(RuntimeError):
    pass


class EmbeddingClient(Protocol):
    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        ...


class OpenAIEmbeddingClient:
    def __init__(
        self,
        client: OpenAI,
        model: str,
        dimensions: int = EMBEDDING_DIMENSIONS,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise EmbeddingContractError("embedding model must be a non-empty string")
        if type(dimensions) is not int or dimensions != EMBEDDING_DIMENSIONS:
            raise EmbeddingContractError(
                f"embedding dimensions must be {EMBEDDING_DIMENSIONS}"
            )
        self._client = client
        self._model = model.strip()
        self._dimensions = dimensions

    # 将文本转化为向量
    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        batch = _validate_texts(texts)
        if not batch:
            return ()

        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dimensions,
            )
        except OpenAIError as exc:
            raise EmbeddingRequestError("embedding request failed") from exc

        return _ordered_embeddings(
            getattr(response, "data", None),
            expected_count=len(batch),
            dimensions=self._dimensions,
        )


def _validate_texts(texts: object) -> tuple[str, ...]:
    if isinstance(texts, (str, bytes)) or not isinstance(texts, Sequence):
        raise EmbeddingContractError("embedding input must be a sequence of strings")

    batch = tuple(texts)
    for index, text in enumerate(batch):
        if not isinstance(text, str) or not text.strip():
            raise EmbeddingContractError(
                f"embedding input at index {index} must be a non-empty string"
            )
    return batch



# 检查每个向量内容是否合法并且维度正好为1024
def _ordered_embeddings(
    data: object,
    *,
    expected_count: int,
    dimensions: int,
) -> EmbeddingBatch:
    if isinstance(data, (str, bytes)) or not isinstance(data, Sequence):
        raise EmbeddingContractError("embedding response data must be a sequence")

    ordered: list[Embedding | None] = [None] * expected_count
    for item in data:
        index = getattr(item, "index", None)
        if type(index) is not int or not 0 <= index < expected_count:
            raise EmbeddingContractError("embedding response index is out of range")
        if ordered[index] is not None:
            raise EmbeddingContractError("embedding response index is duplicated")

        ordered[index] = _validate_embedding(
            getattr(item, "embedding", None),
            dimensions=dimensions,
        )

    if any(vector is None for vector in ordered):
        raise EmbeddingContractError("embedding response is missing an input index")
    return tuple(vector for vector in ordered if vector is not None)


def _validate_embedding(values: object, *, dimensions: int) -> Embedding:
    if (
        isinstance(values, (str, bytes))
        or not isinstance(values, Sequence)
        or len(values) != dimensions
    ):
        raise EmbeddingContractError(
            f"embedding vector must contain {dimensions} values"
        )

    vector: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise EmbeddingContractError("embedding vector must contain only numbers")
        number = float(value)
        if not isfinite(number):
            raise EmbeddingContractError(
                "embedding vector must contain only finite numbers"
            )
        vector.append(number)
    return tuple(vector)


# 转化向量为文本
def embedding_vector_literal(vector: object) -> str:
    values = _validate_embedding(vector, dimensions=EMBEDDING_DIMENSIONS)
    return json.dumps(values, separators=(",", ":"))
