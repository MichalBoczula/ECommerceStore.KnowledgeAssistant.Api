from types import SimpleNamespace

import pytest

from knowledge_assistant.config import Settings
from knowledge_assistant.retrieval import AzureRetriever


def indexer():
    result = object.__new__(AzureRetriever)
    result.settings = Settings()
    result.profile = "embedding-profile"
    result.index_client = SimpleNamespace(
        create_or_update_index=lambda index: index,
        get_index_statistics=lambda name: SimpleNamespace(storage_size=100, vector_index_size=0),
    )
    result.embed = lambda texts: [[0.1] * 1536 for _ in texts]
    return result


def test_all_text_chunks_are_uploaded_including_the_tail(demo):
    azure = indexer()
    rows = []

    def upload(batch):
        rows.extend(batch)
        return [SimpleNamespace(succeeded=True) for _ in batch]

    azure.client = SimpleNamespace(merge_or_upload_documents=upload)
    corpus = demo.corpus.model_copy(deep=True)
    corpus.documents = corpus.documents[:1]
    corpus.documents[0].content = "a" * 25000 + "unique tail rule"
    stats = azure.upload(corpus)
    assert stats["indexedChunks"] == 3
    assert stats["sourceDocuments"] == 1
    assert rows[-1]["content"].endswith("unique tail rule")
    assert len({row["id"] for row in rows}) == 3
    assert all(row["snapshotId"] == "demo-v1" for row in rows)


def test_partial_search_upload_stops_snapshot_publication(demo):
    azure = indexer()
    azure.client = SimpleNamespace(
        merge_or_upload_documents=lambda batch: [SimpleNamespace(succeeded=False) for _ in batch]
    )
    with pytest.raises(ValueError, match="incomplete"):
        azure.upload(demo.corpus)
