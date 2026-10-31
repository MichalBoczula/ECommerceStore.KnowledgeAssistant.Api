import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

from knowledge_assistant.api.app import create_app
from knowledge_assistant.application.errors import DependencyUnavailableError
from knowledge_assistant.domain.errors import EvidenceVerificationError

REQUEST = {"question": "mobile phone validation", "service": "products", "snapshotId": "demo-v1"}
FIELDS = {
    "type",
    "title",
    "status",
    "detail",
    "instance",
    "code",
    "traceId",
    "errors",
    "missingProperties",
}


def assert_problem(response, status, code, instance="/api/v1/query"):
    body = response.json()
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert set(body) == FIELDS
    assert body["status"] == status and body["code"] == code
    assert body["instance"] == instance
    assert body["traceId"] == response.headers["x-request-id"]
    assert isinstance(body["errors"], list) and isinstance(body["missingProperties"], list)
    schema = json.loads(Path("contracts/openapi.json").read_text())
    problem_schema = schema["components"]["schemas"]["ApiProblemDetails"]
    Draft202012Validator({**problem_schema, "components": schema["components"]}).validate(body)
    return body


@pytest.mark.parametrize(
    ("change", "status", "code"),
    [
        ({"snapshotId": "old"}, 409, "snapshot_not_active"),
        ({"service": "payments"}, 404, "resource_not_found"),
        ({"question": "x"}, 400, "validation_failed"),
        ({"question": {"secret": "not-public"}}, 400, "invalid_json"),
    ],
)
def test_query_failures_share_the_documented_contract(demo, change, status, code):
    with TestClient(create_app(corpus=demo.corpus)) as client:
        response = client.post("/api/v1/query?secret=not-public", json={**REQUEST, **change})
        assert_problem(response, status, code)
        assert "not-public" not in response.text


def test_binding_route_method_and_content_type_errors(demo):
    with TestClient(create_app(corpus=demo.corpus)) as client:
        missing = client.post("/api/v1/query", json={"question": "mobile phone"})
        assert assert_problem(missing, 400, "invalid_json")["missingProperties"] == [
            "service",
            "snapshotId",
        ]
        assert_problem(
            client.post(
                "/api/v1/query", content="{secret", headers={"Content-Type": "application/json"}
            ),
            400,
            "invalid_json",
        )
        assert_problem(client.get("/unknown?secret=not-public"), 404, "route_not_found", "/unknown")
        assert_problem(client.get("/api/v1/query"), 405, "method_not_allowed")
        assert_problem(
            client.post(
                "/api/v1/query", content="plain text", headers={"Content-Type": "text/plain"}
            ),
            415,
            "unsupported_media_type",
        )


@pytest.mark.parametrize(
    ("exception", "status", "code"),
    [
        (DependencyUnavailableError("secret provider details"), 503, "service_unavailable"),
        (EvidenceVerificationError("secret model output"), 503, "answer_verification_failed"),
        (RuntimeError("secret internal details"), 500, "internal_error"),
    ],
)
def test_provider_and_unexpected_errors_are_safe(demo, exception, status, code):
    def fail(*args):
        raise exception

    retriever = SimpleNamespace(mode="local_keyword", search=fail)
    with TestClient(
        create_app(corpus=demo.corpus, retriever=retriever), raise_server_exceptions=False
    ) as client:
        response = client.post("/api/v1/query", json=REQUEST)
        assert_problem(response, status, code)
        assert "secret" not in response.text


def test_openapi_has_stable_operations_and_problem_media_types():
    actual = create_app().openapi()
    assert actual == json.loads(Path("contracts/openapi.json").read_text())
    query = actual["paths"]["/api/v1/query"]["post"]
    assert query["operationId"] == "queryKnowledge"
    assert "422" not in query["responses"]
    for status in ["400", "401", "404", "409", "415", "500", "503"]:
        content = query["responses"][status]["content"]
        assert set(content) == {"application/problem+json"}
        assert content["application/problem+json"]["schema"]["$ref"].endswith("/ApiProblemDetails")
