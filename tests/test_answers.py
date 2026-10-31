import copy
import json

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from knowledge_assistant.answers import EvidencePlanner, assemble
from knowledge_assistant.config import Settings
from knowledge_assistant.main import create_app
from knowledge_assistant.models import Answer, Query
from knowledge_assistant.retrieval import LocalRetriever

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
    next(d for d in docs if d.kind == "api_contract").payload["exampleRequest"] = None
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


def test_public_api_has_matching_schema_and_scope_errors(demo):
    with TestClient(create_app(corpus=demo.corpus)) as client:
        catalog = client.get("/api/v1/catalog").json()
        assert catalog["snapshots"][0]["isDemo"] is True
        response = client.post("/api/v1/query", json=query().model_dump(by_alias=True))
        assert response.status_code == 200
        Answer.model_validate(response.json())
        bad = {**query().model_dump(by_alias=True), "snapshotId": "old"}
        assert client.post("/api/v1/query", json=bad).status_code == 409
        bad = {**query().model_dump(by_alias=True), "service": "payments"}
        assert client.post("/api/v1/query", json=bad).status_code == 404
        empty = {**query().model_dump(by_alias=True), "question": "unrelated astronomy"}
        assert client.post("/api/v1/query", json=empty).json()["status"] == "not_found"


def test_api_key_is_required_when_configured(demo):
    settings = Settings(api_key=SecretStr("test-only-key"))
    with TestClient(create_app(settings, corpus=demo.corpus)) as client:
        assert client.get("/api/v1/catalog").status_code == 401
        assert (
            client.get("/api/v1/catalog", headers={"X-API-Key": "test-only-key"}).status_code == 200
        )
        assert client.get("/health/live").status_code == 200


def test_exported_schema_matches_api():
    from pathlib import Path

    expected = json.loads(Path("contracts/answer.schema.json").read_text())
    assert expected == Answer.model_json_schema(by_alias=True)


def test_malformed_generated_json_does_not_discard_supported_rules(demo):
    docs = copy.deepcopy(LocalRetriever(demo.corpus).search(query()))
    next(d for d in docs if d.kind == "api_contract").payload["exampleRequest"] = None
    plan = EvidencePlanner().plan(QUESTION, docs)
    plan.sections[-1].generated_example_json = "not JSON"
    result = assemble(query(), docs, plan, "model", "local_keyword", True)
    assert result.status == "partial"
    assert result.sections[0].type == "validation"
    assert next(s for s in result.sections if s.type == "api_contract").example_request is None
