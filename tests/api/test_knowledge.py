import json

from fastapi.testclient import TestClient
from pydantic import SecretStr

from knowledge_assistant.application.contracts import Answer, Query
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.main import create_app

QUESTION = "Explain mobile phone validation and give me the JSON contract to create a mobile phone"


def query():
    return Query(question=QUESTION, service="products", snapshot_id="demo-v1")


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
