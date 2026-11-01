import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from knowledge_assistant.api.app import create_app
from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.errors import DependencyUnavailableError
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.infrastructure.retrieval.azure_indexer import AzureIndexer
from knowledge_assistant.infrastructure.retrieval.azure_retriever import AzureRetriever
from knowledge_assistant.infrastructure.retrieval.index_plan import plan_index

QUERY = Query(
    question="mobile phone validation and JSON contract", service="products", snapshot_id="demo-v1"
)


def prepared(demo, cloud, tmp_path):
    plan = plan_index(demo.corpus, cloud.settings)
    AzureIndexer(
        cloud.settings, client=cloud.search, index_client=cloud.index, embeddings=cloud.embeddings
    ).upload(plan, tmp_path)
    retriever = AzureRetriever(
        cloud.settings, demo.corpus, client=cloud.search, embeddings=cloud.embeddings
    )
    return retriever, plan


def test_hybrid_query_is_prefiltered_and_hydrates_original_documents(demo, cloud, tmp_path):
    retriever, plan = prepared(demo, cloud, tmp_path)
    cloud.search.search_rows = [
        dict(plan.rows[0], content="injected content", payloadJson="{}")
    ] * 2
    documents = retriever.search(QUERY)
    assert len(documents) <= 12
    assert documents[0] is demo.corpus.documents[0]
    assert documents[0].content != "injected content"
    request = cloud.search.queries[-1]
    assert request["search_text"] == QUERY.question
    assert request["vector_filter_mode"] == "preFilter"
    assert request["vector_queries"][0].fields == "embedding"
    assert all(
        value in request["filter"]
        for value in [
            "service eq 'products'",
            "snapshotId eq 'demo-v1'",
            plan.corpus_sha,
            plan.profile,
        ]
    )
    assert "payloadJson" not in request["select"]
    assert not hasattr(retriever, "index_client")


@pytest.mark.parametrize(
    "change",
    [
        {"service": "users"},
        {"snapshotId": "old"},
        {"corpusHash": "old"},
        {"embeddingProfile": "old"},
        {"documentId": "unknown"},
        {"documentHash": "changed"},
        {"kind": "validation"},
    ],
)
def test_unknown_or_stale_search_evidence_is_rejected(demo, cloud, tmp_path, change):
    retriever, plan = prepared(demo, cloud, tmp_path)
    # Validation is intentionally different from the first execution document.
    cloud.search.search_rows = [{**plan.rows[0], **change}]
    with pytest.raises(EvidenceVerificationError):
        retriever.search(QUERY)


def test_unpublished_corpus_fails_before_embedding_the_question(demo, cloud):
    retriever = AzureRetriever(
        cloud.settings, demo.corpus, client=cloud.search, embeddings=cloud.embeddings
    )
    with pytest.raises(DependencyUnavailableError):
        retriever.search(QUERY)
    assert cloud.embeddings.requests == 0


def test_provider_outage_returns_a_controlled_dependency_failure(demo, cloud, tmp_path):
    retriever, _ = prepared(demo, cloud, tmp_path)

    def fail(**kwargs):
        raise AzureError("private provider detail")

    cloud.search.search = fail
    with pytest.raises(DependencyUnavailableError):
        retriever.search(QUERY)


def test_expansion_supplies_explicit_contract_and_policy_without_crossing_service(
    demo, cloud, tmp_path
):
    corpus = demo.corpus
    seed = next(d for d in corpus.documents if d.kind == "scenario")
    policy = next(d for d in corpus.documents if d.kind == "validation")
    contract = next(d for d in corpus.documents if d.kind == "api_contract")
    for doc in (seed, policy, contract):
        doc.payload["operationIds"] = ["createMobilePhone"]
    unrelated = policy.model_copy(deep=True)
    unrelated.id, unrelated.source.service = "foreign-policy", "users"
    corpus.documents.append(unrelated)
    retriever, plan = prepared(demo, cloud, tmp_path)
    cloud.search.search_rows = [next(r for r in plan.rows if r["documentId"] == seed.id)]
    result = retriever.search(QUERY, limit=4)
    assert seed in result and contract in result and policy in result
    assert all(d.source.service == "products" for d in result)
    assert len(result) <= 4


def test_long_unicode_questions_are_embedded_without_losing_the_tail(demo, cloud, tmp_path):
    retriever, _ = prepared(demo, cloud, tmp_path)
    before = len(cloud.embeddings.inputs)
    retriever.search(QUERY.model_copy(update={"question": "📱" * 3990 + " tail"}))
    inputs = cloud.embeddings.inputs[before:]
    assert len(inputs) > 1 and inputs[-1].endswith("tail")
    assert all(len(t.encode()) <= 6000 for t in inputs)


def test_http_query_returns_validation_then_the_exact_contract_from_hybrid_evidence(
    demo, cloud, tmp_path
):
    corpus = demo.corpus
    for doc in corpus.documents:
        if doc.kind in {"scenario", "api_contract", "validation"}:
            doc.payload["operationIds"] = ["createMobilePhone"]
    retriever, plan = prepared(demo, cloud, tmp_path)
    cloud.search.search_rows = [next(r for r in plan.rows if r["kind"] == "scenario")]
    with TestClient(create_app(corpus=corpus, retriever=retriever)) as client:
        response = client.post("/api/v1/query", json=QUERY.model_dump(by_alias=True))
    assert response.status_code == 200
    answer = response.json()
    assert answer["retrievalMode"] == "azure_hybrid"
    assert [s["type"] for s in answer["sections"]] == ["validation", "api_contract"]
    contract = next(d for d in corpus.documents if d.kind == "api_contract")
    assert answer["sections"][1]["requestSchema"] == contract.payload["requestSchema"]
    assert answer["sections"][1]["exampleRequest"] == contract.payload["exampleRequest"]
