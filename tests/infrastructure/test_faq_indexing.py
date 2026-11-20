import json
from collections import Counter
from pathlib import Path

import pytest

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.evaluation.faq import FaqEvaluation
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.application.services.knowledge_service import KnowledgeService
from knowledge_assistant.domain.faq import question_key
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.ingestion.faq import FaqCatalog, attach_faq
from knowledge_assistant.infrastructure.ingestion.json_source import read
from knowledge_assistant.infrastructure.ingestion.livedocs import documents_from_bundle, load_bundle
from knowledge_assistant.infrastructure.ingestion.reporting import ingestion_report
from knowledge_assistant.infrastructure.retrieval.context import expand_context
from knowledge_assistant.infrastructure.retrieval.index_plan import plan_index
from knowledge_assistant.infrastructure.retrieval.local_retriever import LocalRetriever


def test_reviewed_faq_and_evaluation_inputs_are_complete_and_disjoint():
    catalog = FaqCatalog.model_validate(read(Path("knowledge/faq-en.json")))
    evaluation = FaqEvaluation.model_validate(read(Path("evaluation/faq-en.json")))
    assert Counter(entry.service for entry in catalog.entries) == {
        "products": 20,
        "users": 20,
        "invoices": 20,
        "payments": 20,
    }
    assert {entry.id for entry in catalog.entries} == {case.faq_id for case in evaluation.cases}
    assert all(entry.allure_examples for entry in catalog.entries)
    assert {question_key(entry.question) for entry in catalog.entries}.isdisjoint(
        question_key(case.question) for case in evaluation.cases
    )


def catalog_with_target(
    reference, allure_examples=(), question="Where do I register a handset in stock?"
):
    return FaqCatalog.model_validate(
        {
            "schemaVersion": 1,
            "language": "en",
            "entries": [
                {
                    "id": "PROD-FAQ-01",
                    "service": "products",
                    "question": question,
                    "target": {"kind": "api_contract", "reference": reference},
                    "allureExamples": list(allure_examples),
                }
            ],
        }
    )


def test_faq_chunk_and_exact_match_use_the_original_source_identity(demo):
    catalog = catalog_with_target("createMobilePhone")
    attach_faq(demo.corpus, catalog)
    entry = catalog.entries[0]
    plan = plan_index(demo.corpus, Settings())
    (chunk,) = [row for row in plan.rows if entry.question in row["content"]]
    source = next(doc for doc in demo.corpus.documents if doc.id == chunk["documentId"])
    assert source.kind == chunk["kind"] == "api_contract"
    assert source.source.path == "sources/openapi.json#/paths/~1mobile-phones/post"
    assert entry.question not in source.content
    query = Query(question=entry.question, service="products", snapshot_id="demo-v1")
    assert expand_context([], demo.corpus, query, limit=12)[0] == source
    wrong_service = query.model_copy(update={"service": "users"})
    assert expand_context([], demo.corpus, wrong_service, limit=12) == []


def test_faq_catalog_rejects_an_unresolved_reference(demo):
    with pytest.raises(ValueError, match="PROD-FAQ-01"):
        attach_faq(demo.corpus, catalog_with_target("UnknownOperation"))


def test_faq_report_and_answer_include_the_observed_allure_case(bundle_factory, demo):
    scenario_id = "DEMO-MOBILE-001"
    path_to_result = "allure-results/demo-result.json"
    raw_result = json.loads(demo.files[path_to_result])
    raw_result["labels"] = [{"name": "scenarioId", "value": scenario_id}]
    raw_result["parameters"] = [{"name": "price", "value": "0"}]
    path, reference, _ = bundle_factory({path_to_result: json.dumps(raw_result).encode()})
    metadata, files = load_bundle(path, reference)
    corpus = demo.corpus.model_copy(
        update={
            "is_demo": False,
            "documents": documents_from_bundle(
                metadata,
                files,
                demo.corpus.snapshot_id,
                reference["artifact"]["sha256"],
                "v1",
                "https://docs.example.test",
            ),
        }
    )
    execution = next(doc for doc in corpus.documents if doc.kind == "execution")
    catalog = catalog_with_target(
        "createMobilePhone",
        [{"scenarioId": scenario_id}],
        "Which endpoint creates a mobile phone?",
    )
    attach_faq(corpus, catalog)
    url = "https://docs.example.test/livedoc/v1/products/allure/#behaviors"
    report = ingestion_report(corpus, [], "corpus-checksum", 1)
    assert report["faqAllureExamples"] == [
        {
            "faqId": "PROD-FAQ-01",
            "question": catalog.entries[0].question,
            "service": "products",
            "scenarioId": scenario_id,
            "coverage": "direct",
            "testScenario": execution.title,
            "url": url,
            "executionStatus": "passed",
        }
    ]

    service = KnowledgeService(corpus, LocalRetriever(corpus), EvidencePlanner())
    for question in (catalog.entries[0].question, "How do I create a mobile phone?"):
        body = Query(question=question, service="products", snapshot_id=corpus.snapshot_id)
        result = service.query(body)
        (contract,) = [section for section in result.sections if section.type == "api_contract"]
        assert (contract.method, contract.path) == ("POST", "/mobile-phones")
        (scenario,) = [section for section in result.sections if section.type == "scenario"]
        assert scenario.title == execution.title
        assert scenario.execution_status == "passed"
        assert any(
            source.url == url and source.id in scenario.source_ids for source in result.sources
        )


def test_catalog_rejects_a_missing_allure_case(demo):
    catalog = catalog_with_target("createMobilePhone", [{"scenarioId": "DEMO-missing"}])
    with pytest.raises(ValueError, match="unknown Allure scenario ID"):
        attach_faq(demo.corpus, catalog)
