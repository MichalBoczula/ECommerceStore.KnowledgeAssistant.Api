import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from knowledge_assistant.api.app import create_app
from knowledge_assistant.application.planning import AnswerPlan, PlanSection
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.planning.model_planner import ModelPlanner

QUESTION = "Explain mobile phone validation and give me the JSON contract to create a mobile phone"


def model(invent=False):
    def parse(**kwargs):
        evidence = json.loads(kwargs["input"])["evidence"]
        sections = []
        for doc in evidence:
            if doc["kind"] not in {"validation", "api_contract"}:
                continue
            sections.append(
                PlanSection(
                    type=doc["kind"],
                    title="Model label",
                    text="",
                    document_id=doc["documentId"],
                    rule_ids=(
                        ["made-up"] if invent else [r["ruleId"] for r in doc.get("rules", [])]
                    ),
                    generated_example_json=None,
                    source_ids=[doc["sourceId"]],
                ),
            )
        return SimpleNamespace(
            status="completed",
            output=[],
            output_parsed=AnswerPlan(status="answered", sections=sections, limitations=[]),
        )

    return ModelPlanner(
        Settings(
            openai_endpoint="https://example.openai.azure.com/openai/v1/",
            embedding_deployment="test",
            chat_deployment="test",
        ),
        client=SimpleNamespace(responses=SimpleNamespace(parse=parse), close=lambda: None),
    )


def test_model_plan_becomes_ordered_original_rule_and_contract_sections(demo):
    with TestClient(create_app(corpus=demo.corpus, planner=model())) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": QUESTION, "service": "products", "snapshotId": "demo-v1"},
        )
    assert response.status_code == 200
    answer = response.json()
    assert answer["answerMode"] == "model" and answer["isDemo"] is True
    assert [s["type"] for s in answer["sections"]] == ["validation", "api_contract"]
    assert answer["sections"][1]["requestSchema"] == demo.schema
    assert answer["sections"][1]["exampleRequest"] == demo.example
    assert all(rule["sourceIds"] for rule in answer["sections"][0]["rules"])


def test_invented_rule_ids_return_safe_problem_details(demo, caplog):
    with TestClient(
        create_app(corpus=demo.corpus, planner=model(invent=True)), raise_server_exceptions=False
    ) as client:
        response = client.post(
            "/api/v1/query",
            json={"question": QUESTION, "service": "products", "snapshotId": "demo-v1"},
        )
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["code"] == "answer_verification_failed"
    assert "made-up" not in response.text and "made-up" not in caplog.text
