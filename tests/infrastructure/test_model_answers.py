import json
from types import SimpleNamespace

import pytest
from openai import OpenAIError

from knowledge_assistant.application.errors import DependencyUnavailableError
from knowledge_assistant.application.planning import AnswerPlan, PlanSection
from knowledge_assistant.application.services.evidence_catalog import policy_rules
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.planning.evidence_context import (
    MAX_INPUT_BYTES,
    build_context,
    encoded,
    schema_context,
)
from knowledge_assistant.infrastructure.planning.model_planner import ModelPlanner

QUESTION = "Explain mobile phone validation and give me the JSON contract to create a mobile phone"


def planner(callback):
    client = SimpleNamespace(responses=SimpleNamespace(parse=callback), close=lambda: None)
    return ModelPlanner(
        Settings(
            openai_endpoint="https://example.openai.azure.com/openai/v1/",
            embedding_deployment="test-embedding",
            chat_deployment="configured-chat",
        ),
        client=client,
    )


def response(plan, status="completed", refusal=False):
    return SimpleNamespace(
        status=status,
        output_parsed=plan,
        output=[SimpleNamespace(type="message", content=[SimpleNamespace(type="refusal")])]
        if refusal
        else [],
    )


def test_model_gets_a_compact_rule_catalog_and_preserves_the_configured_deployment(demo):
    docs = demo.corpus.documents
    for doc in docs:
        doc.payload["privateUnusedPayload"] = "must-not-be-sent"
    calls = []

    def parse(**kwargs):
        calls.append(kwargs)
        assert "must-not-be-sent" not in kwargs["input"]
        context = json.loads(kwargs["input"])
        policy = next(e for e in context["evidence"] if e["kind"] == "validation")
        assert policy["rules"][0]["ruleId"].startswith("rule-")
        assert "Do not fill gaps" in kwargs["instructions"]
        return response(EvidencePlanner().plan(QUESTION, docs))

    result = planner(parse).plan(QUESTION, docs)
    assert result.sections[0].rule_ids
    assert len(calls) == 1 and calls[0]["store"] is False
    assert calls[0]["model"] == "configured-chat" and calls[0]["text_format"] is AnswerPlan


@pytest.mark.parametrize(
    "status,refusal", [("incomplete", False), ("failed", False), ("completed", True)]
)
def test_incomplete_or_refused_responses_never_become_answers(demo, status, refusal):
    plan = EvidencePlanner().plan(QUESTION, demo.corpus.documents)
    with pytest.raises(EvidenceVerificationError):
        planner(lambda **_: response(plan, status, refusal)).plan(QUESTION, demo.corpus.documents)


@pytest.mark.parametrize(
    "failure", [ValueError("private model output"), OpenAIError("private provider")]
)
def test_parse_and_provider_failures_are_controlled_without_retrying_with_demo(demo, failure):
    calls = []

    def fail(**kwargs):
        calls.append(True)
        raise failure

    error = (
        EvidenceVerificationError if isinstance(failure, ValueError) else DependencyUnavailableError
    )
    with pytest.raises(error):
        planner(fail).plan(QUESTION, demo.corpus.documents)
    assert calls == [True]


def test_no_parsed_output_is_rejected(demo):
    with pytest.raises(EvidenceVerificationError):
        planner(lambda **_: response(None)).plan(QUESTION, demo.corpus.documents)


def test_rule_from_retrieved_but_omitted_content_is_not_authorized(demo):
    docs = demo.corpus.documents
    policy = next(d for d in docs if d.kind == "validation")
    policy.payload.pop("normalizedRules", None)
    policy.payload["value"]["rules"] = [{"field": "name", "description": "x" * 20000}]
    identity = next(iter(policy_rules(policy)))
    plan = AnswerPlan(
        status="answered",
        limitations=[],
        sections=[
            PlanSection(
                type="validation",
                title="Validation",
                text="",
                document_id=policy.id,
                rule_ids=[identity],
                generated_example_json=None,
                source_ids=[policy.source.id],
            )
        ],
    )
    context = build_context(QUESTION, docs)
    assert identity not in context.rules[policy.id]
    with pytest.raises(EvidenceVerificationError):
        planner(lambda **_: response(plan)).plan(QUESTION, docs)


def test_context_is_bounded_and_omitted_content_is_reported(demo):
    source = next(d for d in demo.corpus.documents if d.kind == "scenario")
    docs = []
    for index in range(40):
        doc = source.model_copy(deep=True)
        doc.id, doc.source.id = f"doc-{index}", f"source-{index}"
        doc.content = "📱" * 5000
        docs.append(doc)
    context = build_context("Explain the documented scenarios", docs)
    assert len(encoded(context.body)) <= MAX_INPUT_BYTES
    assert context.limited and len(context.document_ids) < len(docs)
    selected = context.body["evidence"][0]
    plan = AnswerPlan(
        status="answered",
        limitations=[],
        sections=[
            PlanSection(
                type="scenario",
                title="Scenario",
                text="",
                document_id=selected["documentId"],
                rule_ids=[],
                generated_example_json=None,
                source_ids=[selected["sourceId"]],
            )
        ],
    )
    checked = context.check(plan)
    assert checked.status == "partial" and checked.limitations


def test_only_reachable_local_component_schemas_are_sent():
    payload = {
        "requestSchema": {"$ref": "#/components/schemas/Node"},
        "referenceSchemas": {
            "Node": {
                "type": "object",
                "properties": {"child": {"$ref": "#/components/schemas/Node"}},
            },
            "Unused": {"description": "x" * 100000},
        },
    }
    result = schema_context(payload)
    assert set(result["referenceSchemas"]) == {"Node"}
    assert len(encoded(result)) < 1000


def test_oversized_contract_stays_hydratable_but_cannot_authorize_generation(demo):
    docs = demo.corpus.documents
    contract = next(d for d in docs if d.kind == "api_contract")
    contract.payload.update(
        requestSchema={"description": "x" * 30000}, exampleRequest=None, hasDocumentedExample=False
    )
    plan = EvidencePlanner().plan(QUESTION, docs)
    plan.sections[-1].generated_example_json = "{}"
    checked = planner(lambda **_: response(plan)).plan(QUESTION, docs)
    assert checked.sections[-1].document_id == contract.id
    assert checked.sections[-1].generated_example_json is None
    assert checked.status == "partial"


def test_unrelated_question_can_return_not_found_without_an_invented_answer(demo):
    empty = AnswerPlan(status="not_found", sections=[], limitations=["No relevant evidence."])
    result = planner(lambda **_: response(empty)).plan("Explain astronomy", demo.corpus.documents)
    assert result.status == "not_found" and result.sections == []
