import json
from types import SimpleNamespace

import pytest
from azure.core.exceptions import AzureError
from fastapi.testclient import TestClient

from knowledge_assistant.api.app import create_app
from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.errors import DependencyUnavailableError
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.ingestion.livedocs import documents_from_bundle
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


def test_hybrid_faq_hit_hydrates_original_contract_and_source(demo, cloud, tmp_path):
    contract = next(doc for doc in demo.corpus.documents if doc.kind == "api_contract")
    contract.payload["faqAliases"] = {"PROD-FAQ-01": "Where can I register a handset in stock?"}
    retriever, plan = prepared(demo, cloud, tmp_path)
    cloud.search.search_rows = [row for row in plan.rows if "register a handset" in row["content"]]
    assert len(cloud.search.search_rows) == 1
    query = QUERY.model_copy(update={"question": "I need the API for adding a handset to stock."})
    evidence = retriever.search(query)
    assert evidence[0] == contract
    assert evidence[0].source.path == "sources/openapi.json#/paths/~1mobile-phones/post"
    assert "register a handset" not in evidence[0].content


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


@pytest.mark.parametrize(
    ("question", "expected_status"),
    [
        ("How does lunar agriculture work on Ganymede?", "not_found"),
        ("What happens to an invalid price?", "answered"),
    ],
)
def test_evidence_fallback_checks_topic_of_hybrid_candidates(
    demo, cloud, tmp_path, question, expected_status
):
    retriever, plan = prepared(demo, cloud, tmp_path)
    flow = next(row for row in plan.rows if row["kind"] == "flow")
    scenario = next(row for row in plan.rows if row["kind"] == "scenario")
    cloud.search.search_rows = [flow, scenario]
    query = QUERY.model_copy(update={"question": question})
    with TestClient(create_app(corpus=demo.corpus, retriever=retriever)) as client:
        response = client.post("/api/v1/query", json=query.model_dump(by_alias=True))

    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == expected_status
    if expected_status == "not_found":
        assert answer["sections"] == answer["sources"] == []
    else:
        assert [section["type"] for section in answer["sections"]] == ["scenario"]
        assert [source["id"] for source in answer["sources"]] == [
            retriever.documents[scenario["documentId"]].source.id
        ]


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


INVOICE_ROUTES = {
    "/invoices/{clientId}/{orderId}": ("post", "CreateInvoiceForOrder"),
    "/invoices/{invoiceId}": ("get", "GetInvoiceById"),
    "/invoices/{invoiceId}/pdf": ("get", "DownloadInvoicePdf"),
    "/invoices/by-order/{orderId}": ("get", "GetInvoiceByOrderId"),
    "/orders/{orderId}": ("get", "GetOrderById"),
    "/orders/client/{clientId}": ("get", "GetOrdersByClientId"),
    "/orders/{orderId}/status": ("patch", "UpdateOrderStatus"),
    "/client-data-versions/{clientId}": ("post", "CreateClientDataVersion"),
    "/client-data-versions/client/{clientId}": ("get", "GetClientDataVersionByClientId"),
    "/orders-documentation/flows": ("get", "GetFlowDocumentation"),
    "/orders-documentation/validations": ("get", "GetValidationDocumentation"),
    "/shopping-carts/{clientId}": ("post", "CreateShoppingCart"),
    "/shopping-carts/client/{clientId}": ("get", "GetShoppingCartByClientId"),
    "/invoice-reports/filter": ("post", "FilterInvoices"),
}


@pytest.fixture
def invoice_model_app(demo, cloud, tmp_path):
    policy_name = "InvoiceValidationPolicy"
    policy = {
        "policyName": policy_name,
        "rules": [
            {
                "ruleName": "InvoiceOrderStatusValidationRule",
                "rules": [
                    {"entity": "Order", "message": "Invoice can be created only for paid orders."}
                ],
            },
        ],
    }
    files = {
        "allure-results/invoice-result.json": {"uuid": "invoice-1", "status": "passed"},
        "sources/openapi.json": {
            "openapi": "3.0.1",
            "paths": {
                path: {method: {"operationId": operation, "summary": operation}}
                for path, (method, operation) in INVOICE_ROUTES.items()
            },
        },
        "sources/validation-policies.json": {
            "validations": [
                {policy_name: policy},
                {"UnlinkedInvoicePolicy": {**policy, "policyName": "UnlinkedInvoicePolicy"}},
            ],
        },
        "sources/operation-links.json": {
            "operations": [{"operationId": "CreateInvoiceForOrder", "policies": [policy_name]}],
        },
        "sources/flows.json": [],
    }
    demo.corpus.documents.extend(
        documents_from_bundle(
            {"project": "invoices", "commitSha": "1" * 40},
            {path: json.dumps(value).encode() for path, value in files.items()},
            "demo-v1",
            "1" * 64,
            "demo",
        )
    )
    retriever, index = prepared(demo, cloud, tmp_path)
    # Simulate a hybrid search whose first results miss the requested operation and policy.
    cloud.search.search_rows = [
        row
        for row in index.rows
        if row["service"] == "invoices"
        and retriever.documents[row["documentId"]].operation_id == "GetOrderById"
    ]

    def unexpected_model_call(*_):
        pytest.fail("Explicit documented operations should be answered from source evidence")

    def app(limit=12):
        return create_app(
            Settings(max_candidates=limit),
            corpus=demo.corpus,
            retriever=retriever,
            planner=SimpleNamespace(mode="model", plan=unexpected_model_call),
        )

    return app


@pytest.mark.parametrize("limit", [12, 2])
def test_short_invoice_endpoint_query_lists_scoped_openapi_routes(invoice_model_app, limit):
    with TestClient(invoice_model_app(limit)) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": "invoice endpoint", "service": "invoices", "snapshotId": "demo-v1"},
        )

    assert response.status_code == 200
    answer = response.json()
    assert answer["answerMode"] == "evidence" and answer["retrievalMode"] == "azure_hybrid"
    assert answer["status"] == ("answered" if limit == 12 else "partial")
    assert all(section["type"] == "api_contract" for section in answer["sections"])
    assert [section["path"] for section in answer["sections"]] == sorted(
        path for path in INVOICE_ROUTES if path.startswith("/invoices/")
    )[:limit]
    assert all(
        source["path"].startswith("sources/openapi.json#/") and source["service"] == "invoices"
        for source in answer["sources"]
    )


@pytest.mark.parametrize(
    "question",
    [
        "What is the API endpoint for CreateInvoiceForOrder, and what validation rules apply? "
        "Give the exact method, route, and documented rules.",
        "Show the API endpoint and validation rules for `POST /invoices/{clientId}/{orderId}`.",
    ],
)
def test_invoice_endpoint_with_validation_returns_original_linked_policy(
    invoice_model_app, question
):
    with TestClient(invoice_model_app()) as client:
        response = client.post(
            "/api/v1/query",
            json={
                "question": question,
                "service": "invoices",
                "snapshotId": "demo-v1",
            },
        )

    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered" and answer["answerMode"] == "evidence"
    validation, contract = answer["sections"]
    assert validation["type"] == "validation" and contract["type"] == "api_contract"
    assert (contract["method"], contract["path"], contract["operationId"]) == (
        "POST",
        "/invoices/{clientId}/{orderId}",
        "CreateInvoiceForOrder",
    )
    assert [rule["description"] for rule in validation["rules"]] == [
        "Invoice can be created only for paid orders.",
    ]
    sources = {source["id"]: source["path"] for source in answer["sources"]}
    assert [sources[identity] for identity in validation["sourceIds"]] == [
        "sources/validation-policies.json#/validations/0/InvoiceValidationPolicy",
    ]
    assert [sources[identity] for identity in contract["sourceIds"]] == [
        "sources/openapi.json#/paths/~1invoices~1{clientId}~1{orderId}/post",
    ]


@pytest.mark.parametrize(
    ("question", "operation_ids"),
    [
        ("How to create an invoice?", {"CreateInvoiceForOrder"}),
        (
            "How do I get an invoice?",
            {"GetInvoiceById", "GetInvoiceByOrderId", "DownloadInvoicePdf"},
        ),
        ("How to get an invoice by order ID?", {"GetInvoiceByOrderId"}),
        ("How to download an invoice PDF?", {"DownloadInvoicePdf"}),
        ("How to search invoices?", {"FilterInvoices"}),
    ],
)
def test_english_operation_lookup_uses_method_and_responsibility(
    invoice_model_app, question, operation_ids
):
    with TestClient(invoice_model_app()) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": question, "service": "invoices", "snapshotId": "demo-v1"},
        )
    assert response.status_code == 200
    answer = response.json()
    assert answer["answerMode"] == "evidence"
    assert {section["operationId"] for section in answer["sections"]} == operation_ids
    assert {source["service"] for source in answer["sources"]} == {"invoices"}


def test_endpoint_matrix_lists_every_scoped_operation_with_an_openapi_source(invoice_model_app):
    with TestClient(invoice_model_app()) as client:
        response = client.post(
            "/api/v1/query",
            json={
                "question": "List all endpoints",
                "service": "invoices",
                "snapshotId": "demo-v1",
            },
        )
    assert response.status_code == 200
    answer = response.json()
    assert answer["status"] == "answered"
    (matrix,) = answer["sections"]
    assert matrix["title"] == "Endpoint matrix"
    assert matrix["content"].splitlines() == [
        f"{method.upper()} {path} — {operation}"
        for path, (method, operation) in sorted(INVOICE_ROUTES.items())
    ]
    assert len(matrix["sourceIds"]) == len(INVOICE_ROUTES) == len(answer["sources"])
