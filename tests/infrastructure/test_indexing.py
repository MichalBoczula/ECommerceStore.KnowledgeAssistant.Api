import pytest
from azure.core.exceptions import AzureError

from knowledge_assistant.infrastructure.retrieval.azure_indexer import AzureIndexer
from knowledge_assistant.infrastructure.retrieval.index_plan import (
    INPUT_BYTES,
    MANIFEST_KIND,
    manifest_key,
    plan_index,
)
from knowledge_assistant.infrastructure.retrieval.index_schema import compatible, index_definition


def indexer(cloud):
    return AzureIndexer(
        cloud.settings, client=cloud.search, index_client=cloud.index, embeddings=cloud.embeddings
    )


def test_all_unicode_chunks_and_text_tails_are_uploaded_without_payload_duplication(
    demo, cloud, tmp_path
):
    corpus = demo.corpus.model_copy(deep=True)
    corpus.documents = corpus.documents[:1]
    corpus.documents[0].content = "📱ę" * 9000 + " unique tail rule"
    plan = plan_index(corpus, cloud.settings)
    result = indexer(cloud).upload(plan, tmp_path)
    chunks = [r for r in cloud.search.rows.values() if r["kind"] != MANIFEST_KIND]
    assert chunks[-1]["content"].endswith("unique tail rule")
    assert all(len(r["content"].encode()) <= INPUT_BYTES for r in chunks)
    assert all("payloadJson" not in r and "sourceJson" not in r for r in chunks)
    assert len({r["id"] for r in chunks}) == len(chunks)
    assert result["indexedChunks"] == len(chunks) and result["visibleChunksVerified"]
    assert cloud.search.rows[manifest_key(plan.corpus_sha, plan.profile)]["chunkCount"] == len(
        chunks
    )


def test_unchanged_text_reuses_embeddings_across_retries_and_snapshots(demo, cloud, tmp_path):
    service = indexer(cloud)
    plan = plan_index(demo.corpus, cloud.settings)
    first = service.upload(plan, tmp_path)
    calls = cloud.embeddings.requests
    repeated = service.upload(plan, tmp_path)
    changed = demo.corpus.model_copy(deep=True)
    changed.snapshot_id = "next"
    for doc in changed.documents:
        doc.source.snapshot_id = "next"
    other = service.upload(plan_index(changed, cloud.settings), tmp_path)
    assert first["newEmbeddingInputs"] > 0
    assert repeated["newEmbeddingInputs"] == other["newEmbeddingInputs"] == 0
    assert repeated["embeddingRequests"] == other["embeddingRequests"] == 0
    assert cloud.embeddings.requests == calls
    assert plan.corpus_sha != other["corpusHash"]


def test_failed_batch_never_publishes_a_completion_marker(demo, cloud, tmp_path):
    corpus = demo.corpus.model_copy(deep=True)
    corpus.documents[0].content = "x" * 150000
    plan = plan_index(corpus, cloud.settings)
    cloud.search.fail_upload = 2
    with pytest.raises(ValueError, match="incomplete"):
        indexer(cloud).upload(plan, tmp_path)
    assert cloud.embeddings.requests == 2
    assert manifest_key(plan.corpus_sha, plan.profile) not in cloud.search.rows


def test_unqueryable_upload_is_not_published(demo, cloud, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.retrieval.azure_indexer.time.sleep", lambda _: None
    )
    cloud.search.count_override = 0
    plan = plan_index(demo.corpus, cloud.settings)
    with pytest.raises(ValueError, match="not all visible"):
        indexer(cloud).upload(plan, tmp_path)
    assert manifest_key(plan.corpus_sha, plan.profile) not in cloud.search.rows


def test_existing_schema_is_checked_before_embedding_or_mutation(demo, cloud, tmp_path):
    cloud.index.index = index_definition("knowledge-v1", 8)
    with pytest.raises(ValueError, match="incompatible"):
        indexer(cloud).upload(plan_index(demo.corpus, cloud.settings), tmp_path)
    assert cloud.index.created == cloud.search.uploads == []
    assert cloud.embeddings.requests == 0


def test_definition_round_trip_matches_pinned_sdk_schema(cloud):
    expected = index_definition("knowledge-v1", 4)
    actual = type(expected).deserialize(expected.serialize())
    assert compatible(actual, expected)
    actual.vector_search.algorithms[0].parameters.metric = "euclidean"
    assert not compatible(actual, expected)


def test_existing_index_with_a_stored_vector_copy_requires_a_new_index(demo, cloud, tmp_path):
    cloud.index.index = index_definition("knowledge-v1", cloud.settings.embedding_dimensions)
    embedding = next(field for field in cloud.index.index.fields if field.name == "embedding")
    embedding.stored = True
    with pytest.raises(ValueError, match="select a new KA_SEARCH_INDEX"):
        indexer(cloud).upload(plan_index(demo.corpus, cloud.settings), tmp_path)
    assert cloud.index.created == cloud.search.uploads == []
    assert cloud.embeddings.requests == 0


def test_statistics_outage_does_not_hide_a_completed_upload(demo, cloud, tmp_path):
    def fail(name):
        raise AzureError("not public")

    cloud.index.get_index_statistics = fail
    report = indexer(cloud).upload(plan_index(demo.corpus, cloud.settings), tmp_path)
    assert report["uploadComplete"] and report["storageBytes"] is None


def test_keys_are_bound_to_profile_and_exact_corpus(demo, cloud):
    first = plan_index(demo.corpus, cloud.settings)
    changed = demo.corpus.model_copy(deep=True)
    changed.documents[0].payload["audit"] = "changed source evidence"
    other = plan_index(changed, cloud.settings)
    assert first.corpus_sha != other.corpus_sha
    assert {r["id"] for r in first.rows}.isdisjoint({r["id"] for r in other.rows})
    cloud.settings.embedding_revision = "2"
    revised = plan_index(demo.corpus, cloud.settings)
    assert first.profile != revised.profile


def test_plan_and_uploader_must_use_the_same_embedding_profile(demo, cloud, tmp_path):
    plan = plan_index(demo.corpus, cloud.settings)
    cloud.settings.embedding_revision = "changed"
    with pytest.raises(ValueError, match="plan differs"):
        indexer(cloud).upload(plan, tmp_path)
    assert cloud.embeddings.requests == 0 and cloud.index.created == []


def test_completed_snapshot_reuses_index_without_embedding_cache(demo, cloud, tmp_path):
    plan = plan_index(demo.corpus, cloud.settings)
    service = indexer(cloud)
    service.upload(plan, tmp_path / "first-cache")
    calls, uploads = cloud.embeddings.requests, len(cloud.search.uploads)
    result = service.upload(plan, tmp_path / "empty-cache")
    assert result["reusedCompletedSnapshot"] and result["embeddingRequests"] == 0
    assert cloud.embeddings.requests == calls and len(cloud.search.uploads) == uploads


def test_corrupt_completion_marker_does_not_authorize_reuse(demo, cloud, tmp_path):
    plan = plan_index(demo.corpus, cloud.settings)
    service = indexer(cloud)
    service.upload(plan, tmp_path)
    cloud.search.rows[manifest_key(plan.corpus_sha, plan.profile)]["chunkCount"] += 1
    uploads = len(cloud.search.uploads)
    with pytest.raises(ValueError, match="marker differs"):
        service.upload(plan, tmp_path)
    assert len(cloud.search.uploads) == uploads
