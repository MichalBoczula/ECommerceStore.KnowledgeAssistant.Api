import copy
import json

import pytest
from pydantic import ValidationError

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.planning import AnswerPlan, PlanSection
from knowledge_assistant.application.services.answer_assembler import assemble
from knowledge_assistant.application.services.evidence_catalog import policy_rules
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.application.services.request_examples import validate_example
from knowledge_assistant.domain.errors import EvidenceVerificationError

QUESTION = "Explain mobile phone validation and give me the JSON contract to create a mobile phone"


def prepared(demo, question=QUESTION):
    docs = copy.deepcopy(demo.corpus.documents)
    return docs, EvidencePlanner().plan(question, docs)


def answer(docs, plan, question=QUESTION, is_demo=True):
    query = Query(question=question, service="products", snapshot_id="demo-v1")
    return assemble(query, docs, plan, "model", "local_keyword", is_demo)


def test_model_selects_rule_ids_and_server_hydrates_original_rule_text(demo):
    docs, plan = prepared(demo)
    validation = plan.sections[0]
    assert validation.rule_ids and not hasattr(validation, "rules")
    validation.text = "Invented rule: price may be zero"
    validation.title = "All rules were executed successfully"
    result = answer(docs, plan)
    policy = next(d for d in docs if d.kind == "validation")
    assert result.sections[0].rules == list(policy_rules(policy).values())
    assert result.sections[0].title == "Validation"
    invalid = validation.model_dump()
    invalid["rules"] = [{"description": "Invented"}]
    with pytest.raises(ValidationError):
        PlanSection.model_validate(invalid)


def test_known_citation_cannot_authorize_an_unknown_validation_rule(demo):
    docs, plan = prepared(demo)
    plan.sections[0].rule_ids = ["invented-rule"]
    with pytest.raises(EvidenceVerificationError, match="unknown documented rule"):
        answer(docs, plan)


def test_server_orders_merges_and_deduplicates_validation_sections(demo):
    docs, plan = prepared(demo)
    validation, contract = plan.sections
    left = validation.model_copy(update={"rule_ids": validation.rule_ids[:1]})
    right = validation.model_copy(update={"rule_ids": validation.rule_ids[1:]})
    plan.sections = [contract, right, left, left]
    result = answer(docs, plan)
    assert [s.type for s in result.sections] == ["validation", "api_contract"]
    assert len(result.sections[0].rules) == 3 and result.status == "answered"


def test_validation_needs_a_policy_document_even_with_known_citations(demo):
    docs, plan = prepared(demo)
    scenario = next(d for d in docs if d.kind == "scenario")
    plan.sections[0].document_id = scenario.id
    plan.sections[0].source_ids = [scenario.source.id]
    with pytest.raises(EvidenceVerificationError, match="Wrong source type"):
        answer(docs, plan)


@pytest.mark.parametrize(
    "role,operations", [("published_only", []), ("referenced", ["otherOperation"])]
)
def test_unestablished_policy_enforcement_is_explicitly_partial(demo, role, operations):
    docs, plan = prepared(demo)
    policy = next(d for d in docs if d.kind == "validation")
    policy.payload.update(policyRole=role, operationIds=operations)
    result = answer(docs, plan, is_demo=False)
    assert result.status == "partial" and result.sections[0].rules
    assert any(
        "operation link" in note or "not explicitly linked" in note for note in result.limitations
    )


def test_referenced_policy_is_documentation_not_proof_that_every_rule_ran(demo):
    docs, plan = prepared(demo)
    policy = next(d for d in docs if d.kind == "validation")
    policy.payload.update(policyRole="referenced", operationIds=["createMobilePhone"])
    result = answer(docs, plan, is_demo=False)
    assert result.status == "answered"
    assert any("do not prove" in note for note in result.limitations)


@pytest.mark.parametrize("missing", ["validation", "api_contract"])
def test_missing_requested_part_keeps_supported_sections(demo, missing):
    docs, _ = prepared(demo)
    docs = [d for d in docs if d.kind != missing]
    result = answer(docs, EvidencePlanner().plan(QUESTION, docs))
    assert result.status == "partial" and result.sections
    assert any("not established" in note for note in result.limitations)


def test_artifact_citations_do_not_include_model_added_unrelated_sources(demo):
    docs, plan = prepared(demo)
    foreign = next(d for d in docs if d.kind == "scenario")
    for section in plan.sections:
        section.source_ids.append(foreign.source.id)
    result = answer(docs, plan)
    assert foreign.source.id not in {s.id for s in result.sources}


@pytest.mark.parametrize("status", ["passed", "failed", "broken", "skipped", "unknown"])
def test_observed_status_is_hydrated_and_cannot_be_rewritten(demo, status):
    docs, _ = prepared(demo)
    execution = next(d for d in docs if d.kind == "execution")
    execution.payload["status"] = status
    plan = AnswerPlan(
        status="answered",
        limitations=[],
        sections=[
            PlanSection(
                type="scenario",
                title="Everything passed",
                text="Invented outcome",
                rule_ids=[],
                document_id=execution.id,
                generated_example_json=None,
                source_ids=[execution.source.id],
            )
        ],
    )
    result = answer(docs, plan, question="Explain this scenario")
    assert result.sections[0].execution_status == status
    assert result.sections[0].content == execution.content
    assert result.status == ("answered" if status == "passed" else "partial")


def test_expected_scenario_is_not_presented_as_an_observed_execution(demo):
    docs, _ = prepared(demo)
    scenario = next(d for d in docs if d.kind == "scenario")
    scenario.payload["status"] = "passed"
    plan = AnswerPlan(
        status="answered",
        limitations=[],
        sections=[
            PlanSection(
                type="scenario",
                title="Passed",
                text="",
                rule_ids=[],
                document_id=scenario.id,
                generated_example_json=None,
                source_ids=[scenario.source.id],
            )
        ],
    )
    assert answer(docs, plan, question="Explain this scenario").sections[0].execution_status is None


@pytest.mark.parametrize(
    "generated",
    ["not JSON", '{"price":1,"price":2}', '{"price":NaN}', '{"price":1e999}', " " * 24001],
)
def test_invalid_generated_json_is_omitted_without_losing_validation(demo, generated):
    docs, plan = prepared(demo)
    contract = next(d for d in docs if d.kind == "api_contract")
    contract.payload.update(exampleRequest=None, hasDocumentedExample=False)
    plan.sections[-1].generated_example_json = generated
    result = answer(docs, plan)
    assert result.status == "partial" and result.sections[0].rules
    assert result.sections[1].example_origin == "unavailable"


def test_valid_generated_example_is_labelled_and_business_compliance_is_not_assumed(demo):
    docs, plan = prepared(demo)
    next(d for d in docs if d.kind == "api_contract").payload.update(
        exampleRequest=None, hasDocumentedExample=False
    )
    plan.sections[-1].generated_example_json = json.dumps(demo.example)
    result = answer(docs, plan)
    contract = result.sections[1]
    assert contract.example_origin == "generated" and contract.example_schema_valid is True
    assert result.status == "answered"
    assert any("business-rule compliance" in note for note in result.limitations)


@pytest.mark.parametrize("documented", [True, False])
def test_json_null_can_be_an_available_valid_example(demo, documented):
    docs, plan = prepared(demo)
    contract = next(d for d in docs if d.kind == "api_contract")
    contract.payload.update(
        requestSchema={"type": ["object", "null"]},
        exampleRequest=None,
        hasDocumentedExample=documented,
    )
    plan.sections[-1].generated_example_json = None if documented else "null"
    result = answer(docs, plan)
    assert result.sections[1].example_request is None
    assert result.sections[1].example_origin == ("openapi" if documented else "generated")
    assert result.sections[1].example_schema_valid is True


def test_unresolvable_schema_is_not_fetched_or_treated_as_verified(demo, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Example verification must not fetch remote schema references")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    docs, plan = prepared(demo)
    contract = next(d for d in docs if d.kind == "api_contract")
    contract.payload.update(
        requestSchema={"$ref": "https://example.invalid/schema"},
        exampleRequest=None,
        hasDocumentedExample=False,
    )
    plan.sections[-1].generated_example_json = "{}"
    result = answer(docs, plan)
    assert result.status == "partial" and result.sections[1].example_request is None
    assert result.sections[1].example_schema_valid is None


@pytest.mark.parametrize("version", ["3.0.3", "3.1.0"])
def test_local_recursive_reference_schemas_are_verified(version):
    payload = {
        "openapiVersion": version,
        "requestSchema": {"$ref": "#/components/schemas/Node"},
        "referenceSchemas": {
            "Node": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "children": {"type": "array", "items": {"$ref": "#/components/schemas/Node"}},
                },
            }
        },
    }
    assert validate_example(payload, {"name": "root", "children": [{"name": "child"}]}) is True
    assert validate_example(payload, {"children": [{}]}) is False


def test_ambiguous_contract_is_not_chosen_by_retrieval_order(demo):
    docs, _ = prepared(demo)
    original = next(d for d in docs if d.kind == "api_contract")
    duplicate = original.model_copy(deep=True)
    duplicate.id, duplicate.source.id = "other-contract", "other-source"
    docs.append(duplicate)
    result = answer(docs, EvidencePlanner().plan(QUESTION, docs))
    assert result.status == "partial"
    assert not any(s.type == "api_contract" for s in result.sections)


def test_read_contract_without_request_body_does_not_require_an_example(demo):
    docs, _ = prepared(demo)
    contract = next(d for d in docs if d.kind == "api_contract")
    contract.payload.update(
        method="GET",
        mediaType="",
        requestSchema={},
        exampleRequest=None,
        hasDocumentedExample=False,
    )
    question = "Give me the GET contract for reading a mobile phone"
    result = answer(docs, EvidencePlanner().plan(question, docs), question=question)
    assert result.status == "answered" and result.sections[0].method == "GET"


def test_invalid_documented_example_is_retained_and_labelled(demo):
    docs, plan = prepared(demo)
    contract = next(d for d in docs if d.kind == "api_contract")
    contract.payload["exampleRequest"] = {"price": -1}
    plan.sections[-1].generated_example_json = json.dumps(demo.example)
    result = answer(docs, plan)
    assert result.status == "partial"
    assert result.sections[1].example_origin == "openapi"
    assert result.sections[1].example_request == {"price": -1}
    assert result.sections[1].example_schema_valid is False


def test_unresolved_links_for_the_selected_operation_are_reported(demo):
    docs, plan = prepared(demo)
    link = next(d for d in docs if d.kind == "operation_links")
    link.payload["ingestionWarnings"] = [{"code": "unresolved_flow", "target": "MissingFlow"}]
    result = answer(docs, plan)
    assert result.status == "partial"
    assert link.source.id in {s.id for s in result.sources}
    assert any("unresolved evidence" in note for note in result.limitations)


def test_not_found_cannot_silently_publish_answer_sections(demo):
    docs, plan = prepared(demo)
    plan.status = "not_found"
    with pytest.raises(EvidenceVerificationError, match="not-found"):
        answer(docs, plan)
