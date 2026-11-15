from collections import Counter
from pathlib import Path

import pytest

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.evaluation.faq import FaqEvaluation
from knowledge_assistant.domain.faq import question_key
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.ingestion.faq import FaqCatalog, attach_faq
from knowledge_assistant.infrastructure.ingestion.json_source import read
from knowledge_assistant.infrastructure.retrieval.context import expand_context
from knowledge_assistant.infrastructure.retrieval.index_plan import plan_index


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
    assert {question_key(entry.question) for entry in catalog.entries}.isdisjoint(
        question_key(case.question) for case in evaluation.cases
    )


def catalog_with_target(reference):
    return FaqCatalog.model_validate(
        {
            "schemaVersion": 1,
            "language": "en",
            "entries": [
                {
                    "id": "PROD-FAQ-01",
                    "service": "products",
                    "question": "Where do I register a handset in stock?",
                    "target": {"kind": "api_contract", "reference": reference},
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
