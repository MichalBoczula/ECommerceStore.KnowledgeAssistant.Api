import json

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import OpenAI

from knowledge_assistant.api.app import create_app
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.infrastructure.clients import openai_client
from knowledge_assistant.infrastructure.configuration.settings import Settings

PROJECT_ENDPOINT = "https://foundry-dev.services.ai.azure.com/api/projects/store-dev"
AGENT_NAME = "ecommerce-knowledge-assistant"
QUESTION = "Explain mobile phone validation and give me the JSON contract to create a mobile phone"


def test_api_uses_the_saved_agent_and_validates_its_structured_answer(demo, monkeypatch):
    plan = EvidencePlanner().plan(QUESTION, demo.corpus.documents)
    calls = []
    identity = object()

    def token_provider(credential, scope):
        assert credential is identity
        assert scope == "https://ai.azure.com/.default"
        return lambda: "managed-identity-token"

    def respond(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert str(request.url) == (
            f"{PROJECT_ENDPOINT}/agents/{AGENT_NAME}/endpoint/protocols/openai/"
            "responses?api-version=v1"
        )
        assert request.headers["Authorization"] == "Bearer managed-identity-token"
        body = json.loads(request.content)
        calls.append(body)
        assert "model" not in body and "instructions" not in body
        assert body["tool_choice"] == "none"
        assert body["max_output_tokens"] == 4096
        assert body["store"] is False
        assert body["text"]["format"]["type"] == "json_schema"
        assert body["input"][0]["role"] == "developer"
        assert body["input"][1]["role"] == "user"
        evidence = json.loads(body["input"][1]["content"])["evidence"]
        assert any(row["kind"] == "api_contract" for row in evidence)
        return httpx.Response(
            200,
            json={
                "id": "resp_saved_agent",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-6-luna",
                "output": [
                    {
                        "type": "message",
                        "id": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": plan.model_dump_json(by_alias=True),
                                "annotations": [],
                            }
                        ],
                    }
                ],
            },
        )

    monkeypatch.setattr(openai_client, "DefaultAzureCredential", lambda: identity)
    monkeypatch.setattr(openai_client, "get_bearer_token_provider", token_provider)
    monkeypatch.setattr(
        openai_client,
        "OpenAI",
        lambda **kwargs: OpenAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(respond))
        ),
    )
    settings = Settings(
        generation_mode="foundry",
        foundry_project_endpoint=PROJECT_ENDPOINT,
        foundry_agent_name=AGENT_NAME,
        openai_key="model-key-from-existing-configuration",
        max_output_tokens=4096,
    )
    with TestClient(create_app(settings, corpus=demo.corpus)) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": QUESTION, "service": "products", "snapshotId": "demo-v1"},
        )
    assert response.status_code == 200
    answer = response.json()
    contract = next(s for s in answer["sections"] if s["type"] == "api_contract")
    assert (contract["method"], contract["path"]) == ("POST", "/mobile-phones")
    expected_sources = {doc.source.id for doc in demo.corpus.documents}
    assert set(contract["sourceIds"]) <= expected_sources
    assert len(calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/api/projects/store-dev",
        "https://foundry-dev.openai.azure.com/openai/v1",
        f"{PROJECT_ENDPOINT}/../another-project",
    ],
)
def test_foundry_credentials_require_a_valid_project_endpoint(url):
    settings = Settings(foundry_project_endpoint=url, foundry_agent_name=AGENT_NAME)
    with pytest.raises(ValueError, match="KA_FOUNDRY_PROJECT_ENDPOINT"):
        settings.foundry_agent_base_url()
