import copy

import pytest

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.services.answer_assembler import assemble
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.infrastructure.retrieval.local_retriever import LocalRetriever

QUESTION = "Explain mobile phone validation and give me the JSON contract to create a mobile phone"


def query():
    return Query(question=QUESTION, service="products", snapshot_id="demo-v1")


def test_contract_and_example_are_hydrated_exactly_from_evidence(demo):
    docs = LocalRetriever(demo.corpus).search(query())
    plan = EvidencePlanner().plan(QUESTION, docs)
    result = assemble(query(), docs, plan, "evidence", "local_keyword", True)
    contract = next(s for s in result.sections if s.type == "api_contract")
    assert contract.request_schema == demo.schema
    assert contract.example_request == demo.example
    assert contract.example_schema_valid is True
    assert result.sections[0].type == "validation"
    assert result.is_demo is True
    assert all(s.snapshot_id == "demo-v1" for s in result.sources)


def test_invented_citations_are_rejected(demo):
    docs = LocalRetriever(demo.corpus).search(query())
    plan = EvidencePlanner().plan(QUESTION, docs)
    plan.sections[0].source_ids = ["invented"]
    with pytest.raises(ValueError, match="unknown evidence"):
        assemble(query(), docs, plan, "model", "local_keyword", True)


def test_invalid_generated_example_is_omitted(demo):
    docs = copy.deepcopy(LocalRetriever(demo.corpus).search(query()))
    next(d for d in docs if d.kind == "api_contract").payload.update(
        exampleRequest=None, hasDocumentedExample=False
    )
    plan = EvidencePlanner().plan(QUESTION, docs)
    plan.sections[-1].generated_example_json = '{"price":-5}'
    result = assemble(query(), docs, plan, "model", "local_keyword", True)
    contract = next(s for s in result.sections if s.type == "api_contract")
    assert contract.example_request is None
    assert result.status == "partial"


def test_service_and_snapshot_isolation(demo):
    retriever = LocalRetriever(demo.corpus)
    assert (
        retriever.search(Query(question=QUESTION, service="payments", snapshot_id="demo-v1")) == []
    )
    assert retriever.search(Query(question=QUESTION, service="products", snapshot_id="other")) == []


def test_malformed_generated_json_does_not_discard_supported_rules(demo):
    docs = copy.deepcopy(LocalRetriever(demo.corpus).search(query()))
    next(d for d in docs if d.kind == "api_contract").payload.update(
        exampleRequest=None, hasDocumentedExample=False
    )
    plan = EvidencePlanner().plan(QUESTION, docs)
    plan.sections[-1].generated_example_json = "not JSON"
    result = assemble(query(), docs, plan, "model", "local_keyword", True)
    assert result.status == "partial"
    assert result.sections[0].type == "validation"
    assert next(s for s in result.sections if s.type == "api_contract").example_request is None
