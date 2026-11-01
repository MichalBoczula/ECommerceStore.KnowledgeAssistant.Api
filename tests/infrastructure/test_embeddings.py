import json
from types import SimpleNamespace

import pytest

from knowledge_assistant.infrastructure.clients.embeddings import (
    EmbeddingCache,
    EmbeddingClient,
    InvalidEmbeddingError,
    validate_vector,
)
from knowledge_assistant.infrastructure.retrieval.index_plan import embedding_profile


def client_for(cloud, items):
    api = SimpleNamespace(
        embeddings=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                data=[SimpleNamespace(index=i, embedding=v) for i, v in items],
                usage=SimpleNamespace(prompt_tokens=10),
            )
        )
    )
    return EmbeddingClient(cloud.settings, api)


def test_provider_indices_are_restored_to_input_order(cloud):
    client = client_for(cloud, [(1, [0.2] * 4), (0, [0.1] * 4)])
    assert client.embed(["first", "second"]) == [[0.1] * 4, [0.2] * 4]
    assert client.requests == 1 and client.input_tokens == 10


@pytest.mark.parametrize("indices", [[0, 0], [1, 2], [0]])
def test_missing_duplicate_or_out_of_range_indices_are_rejected(cloud, indices):
    with pytest.raises(InvalidEmbeddingError, match="indices"):
        client_for(cloud, [(i, [0.1] * 4) for i in indices]).embed(["a", "b"])


@pytest.mark.parametrize(
    "vector",
    [
        [],
        [1.0] * 3,
        [0.0] * 4,
        [True] * 4,
        [float("nan")] * 4,
        [float("inf")] * 4,
        [1e39] * 4,
        ["0.1"] * 4,
    ],
)
def test_invalid_vectors_cannot_enter_the_index_or_cache(vector):
    with pytest.raises(InvalidEmbeddingError):
        validate_vector(vector, 4)


def test_cache_deduplicates_text_without_storing_raw_text(cloud, tmp_path):
    profile = embedding_profile(cloud.settings)
    cache = EmbeddingCache(tmp_path, profile, 4)
    result = cache.vectors(["private source text", "private source text"], cloud.embeddings)
    assert result[0] == result[1] and cloud.embeddings.inputs == ["private source text"]
    path = cache.path("private source text")
    assert "private source text" not in path.read_text()
    cache.vectors(["private source text"], cloud.embeddings)
    assert cloud.embeddings.requests == 1
    value = json.loads(path.read_text())
    value["vector"] = [0.0] * 4
    path.write_text(json.dumps(value))
    with pytest.raises(InvalidEmbeddingError, match="Corrupt"):
        cache.vectors(["private source text"], cloud.embeddings)
    assert cloud.embeddings.requests == 1


def test_revision_invalidates_the_cache_while_changing_keys_does_not(cloud):
    profile = embedding_profile(cloud.settings)
    cloud.settings.openai_key = "new-key"
    assert embedding_profile(cloud.settings) == profile
    cloud.settings.embedding_revision = "2"
    assert embedding_profile(cloud.settings) != profile
