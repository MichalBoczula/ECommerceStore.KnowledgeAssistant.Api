"""Validate provider vectors and cache immutable embedding inputs, without storing raw text."""

import json
import math
import os
import tempfile
from pathlib import Path

from ..configuration.settings import Settings
from ..ingestion.livedocs import digest
from ..retrieval.index_plan import INPUT_BYTES
from .openai_client import openai_client


class InvalidEmbeddingError(ValueError):
    pass


def validate_vector(vector: object, dimensions: int) -> list[float]:
    if (
        not isinstance(vector, list)
        or len(vector) != dimensions
        or any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or abs(v) > 3.4028234663852886e38
            or not math.isfinite(v)
            for v in vector
        )
        or not any(vector)
    ):
        raise InvalidEmbeddingError("Embedding response contains an invalid vector")
    return [float(v) for v in vector]


class EmbeddingClient:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.client = client or openai_client(settings)
        self.requests = 0
        self.input_tokens = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts or any(not t.strip() or len(t.encode()) > INPUT_BYTES for t in texts):
            raise InvalidEmbeddingError("Embedding input exceeds the bounded text budget")
        response = self.client.embeddings.create(
            model=self.settings.embedding_deployment,
            input=texts,
            dimensions=self.settings.embedding_dimensions,
            encoding_format="float",
        )
        self.requests += 1
        self.input_tokens += getattr(getattr(response, "usage", None), "prompt_tokens", 0) or 0
        if (
            not isinstance(getattr(response, "data", None), list)
            or any(type(item.index) is not int for item in response.data)
            or sorted(item.index for item in response.data) != list(range(len(texts)))
        ):
            raise InvalidEmbeddingError("Embedding response has missing or duplicate input indices")
        return [
            validate_vector(item.embedding, self.settings.embedding_dimensions)
            for item in sorted(response.data, key=lambda item: item.index)
        ]

    def close(self):
        self.client.close()


class EmbeddingCache:
    def __init__(self, root: Path, profile: str, dimensions: int):
        self.root, self.profile, self.dimensions = root, profile, dimensions
        self.hits, self.misses = 0, 0

    def path(self, text: str) -> Path:
        return self.root / self.profile / (digest(text.encode()) + ".json")

    def read(self, text: str) -> list[float] | None:
        path = self.path(text)
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text())
            if value["textSha256"] != path.stem or value["profile"] != self.profile:
                raise ValueError()
            return validate_vector(value["vector"], self.dimensions)
        except (ValueError, KeyError, TypeError):
            raise InvalidEmbeddingError(
                "Corrupt embedding cache entry; remove it before retrying"
            ) from None

    def vectors(self, texts: list[str], provider: EmbeddingClient) -> list[list[float]]:
        values, missing = {}, []
        for text in dict.fromkeys(texts):
            vector = self.read(text)
            if vector is None:
                missing.append(text)
            else:
                values[text] = vector
                self.hits += 1
        if missing:
            vectors = provider.embed(missing)
            if len(vectors) != len(missing):
                raise InvalidEmbeddingError("Embedding response omitted inputs")
            for text, vector in zip(missing, vectors, strict=True):
                vector = validate_vector(vector, self.dimensions)
                target = self.path(text)
                target.parent.mkdir(parents=True, exist_ok=True)
                temporary_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", dir=target.parent, delete=False
                    ) as tmp:
                        temporary_path = Path(tmp.name)
                        json.dump(
                            {"profile": self.profile, "textSha256": target.stem, "vector": vector},
                            tmp,
                            allow_nan=False,
                        )
                    os.replace(temporary_path, target)
                finally:
                    if temporary_path:
                        temporary_path.unlink(missing_ok=True)
                values[text] = vector
                self.misses += 1
        return [values[text] for text in texts]
