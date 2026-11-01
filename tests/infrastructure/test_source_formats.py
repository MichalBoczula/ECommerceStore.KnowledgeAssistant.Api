"""Synthetic fixtures reflecting the pinned producer formats, without business payloads."""

import json

import pytest

from knowledge_assistant.infrastructure.ingestion.livedocs import (
    documents_from_bundle,
    load_bundle,
)
from knowledge_assistant.infrastructure.ingestion.reporting import bundle_report
from knowledge_assistant.infrastructure.ingestion.structured import entries, validation_rules


@pytest.mark.parametrize("wrapper", [None, "validations"])
def test_dotnet_nested_rules_keep_each_message_and_descriptor_name(wrapper):
    policy = {
        "policyName": "PhonePolicy",
        "rules": [
            {
                "ruleName": "NameRule",
                "rules": [
                    {"message": "Name is required.", "name": "NameRule", "entity": "Phone"},
                    {"message": "Name is too long.", "name": "NameRule", "entity": "Phone"},
                ],
            }
        ],
    }
    source = [policy] if wrapper is None else {wrapper: [{"PhonePolicyDescriptor": policy}]}
    (record,) = entries(source, "validation")
    rules = validation_rules(record.value)
    assert [r["description"] for r in rules] == ["Name is required.", "Name is too long."]
    assert all(r["ruleName"] == "NameRule" and r["entity"] == "Phone" for r in rules)
    if wrapper:
        assert "PhonePolicyDescriptor" in record.aliases
        assert record.pointer == "/validations/0/PhonePolicyDescriptor"


def test_users_flow_map_inside_array_yields_each_descriptor():
    source = {
        "flows": [
            {
                "GetCreateDescriptor": {
                    "flowName": "Create",
                    "steps": [{"order": 1, "stepName": "Save"}],
                },
                "GetReadDescriptor": {"flowName": "Read", "steps": []},
            }
        ]
    }
    create, read = entries(source, "flow")
    assert create.title == "Create" and read.title == "Read"
    assert "GetCreateDescriptor" in create.aliases
    assert create.value["steps"] == [{"order": 1, "stepName": "Save"}]
    assert create.pointer != read.pointer


def test_python_policy_catalog_preserves_conditions_and_source():
    policy = {
        "source": "sample.Policy.require_name",
        "description": "Name is required.",
        "conditions": ["not value", "not value.strip()"],
    }
    (record,) = entries({"policies": [policy]}, "validation")
    (rule,) = validation_rules(record.value)
    assert record.identity == policy["source"]
    assert rule["conditions"] == policy["conditions"]
    assert rule["source"] == policy["source"]


def imported(bundle_factory, overrides):
    path, ref, _ = bundle_factory(
        {p: json.dumps(v).encode() if not isinstance(v, bytes) else v for p, v in overrides.items()}
    )
    metadata, files = load_bundle(path, ref)
    return (
        documents_from_bundle(
            metadata, files, "pinned", ref["artifact"]["sha256"], "v1", "https://docs.example.test"
        ),
        metadata,
        ref,
    )


def test_template_names_and_quoted_numeric_parameters_match_exact_outline_row(bundle_factory):
    feature = b"""Feature: Pricing
  Scenario Outline: Reject price <price>
    Given price <price>
    Then reject it
    Examples:
      | price | scenarioId |
      | 0     | CASE-1     |
      | -1    | CASE-1     |
"""

    def result(number):
        return {
            "uuid": f"r{number}",
            "name": "Reject price <price>",
            "status": "passed",
            "labels": [
                {"name": "feature", "value": "Pricing"},
                {"name": "scenarioId", "value": "CASE-1"},
            ],
            "parameters": [{"name": "price", "value": json.dumps(str(number))}],
        }

    docs, metadata, ref = imported(
        bundle_factory,
        {
            "bdd/mobile.feature": feature,
            "allure-results/demo-result.json": result(0),
            "allure-results/second-result.json": result(-1),
            "sources/operation-links.json": {
                "operations": [
                    {
                        "operationId": "createMobilePhone",
                        "scenarios": [{"scenarioId": "CASE-1"}],
                    }
                ]
            },
        },
    )
    by_id = {d.id: d for d in docs}
    for execution in [d for d in docs if d.kind == "execution"]:
        (scenario,) = [by_id[i] for i in execution.payload["scenarioDocumentIds"]]
        assert scenario.payload["exampleValues"]["price"] == execution.payload["uuid"][1:]
        assert execution.payload["operationIds"] == ["createMobilePhone"]
    report = bundle_report(metadata, ref, docs)
    assert report["scenariosWithExecution"] == 2
    assert report["unmatchedExecutions"] == 0
    assert report["warnings"] == []


def test_ambiguous_results_are_preserved_without_guessing_an_outline_row(bundle_factory):
    docs, metadata, ref = imported(bundle_factory, {})
    result = next(d for d in docs if d.kind == "execution")
    assert result.payload["status"] == "passed"
    assert result.payload["scenarioDocumentIds"] == []
    assert bundle_report(metadata, ref, docs)["warnings"][0]["code"] == "ambiguous_execution"


def test_unknown_explicit_ids_cannot_fall_back_to_a_matching_name(bundle_factory):
    feature = b"""@KNOWN-ID
Feature: Phone
  Scenario: Create phone
    Then it succeeds
"""
    docs, _, _ = imported(
        bundle_factory,
        {
            "bdd/mobile.feature": feature,
            "allure-results/demo-result.json": {
                "uuid": "r",
                "name": "Create phone",
                "status": "passed",
                "labels": [
                    {"name": "feature", "value": "Phone"},
                    {"name": "scenarioId", "value": "WRONG-ID"},
                ],
            },
            "sources/operation-links.json": {
                "operations": [
                    {
                        "operationId": "createMobilePhone",
                        "scenarios": [
                            {
                                "scenarioId": "WRONG-ID",
                                "feature": "mobile.feature",
                                "scenario": "Create phone",
                            }
                        ],
                    }
                ]
            },
        },
    )
    execution = next(d for d in docs if d.kind == "execution")
    scenario = next(d for d in docs if d.kind == "scenario")
    link = next(d for d in docs if d.kind == "operation_links")
    assert execution.payload["scenarioDocumentIds"] == []
    assert scenario.id not in link.payload["relatedDocumentIds"]
    assert link.payload["ingestionWarnings"][0]["code"] == "unresolved_scenario"


def test_allure_gherkin_label_tags_are_explicit_scenario_ids(bundle_factory):
    docs, _, _ = imported(
        bundle_factory,
        {
            "bdd/mobile.feature": b"""Feature: Phone
  @allure.label.scenarioId:CASE-1
  Scenario: Create phone
    Then it succeeds
""",
            "allure-results/demo-result.json": {
                "uuid": "r",
                "name": "Create phone",
                "status": "passed",
                "labels": [{"name": "scenarioId", "value": "CASE-1"}],
            },
            "sources/operation-links.json": {
                "operations": [
                    {
                        "operationId": "createMobilePhone",
                        "scenarios": [{"scenarioId": "CASE-1"}],
                    }
                ]
            },
        },
    )
    scenario = next(d for d in docs if d.kind == "scenario")
    execution = next(d for d in docs if d.kind == "execution")
    assert "CASE-1" in scenario.payload["scenarioIds"]
    assert execution.payload["scenarioDocumentIds"] == [scenario.id]
    assert scenario.payload["operationIds"] == ["createMobilePhone"]


def test_payment_case_ids_inline_refs_and_unreferenced_policy_remain_distinct(bundle_factory):
    source = "sample.Policy.require_name"
    docs, _, _ = imported(
        bundle_factory,
        {
            "bdd/mobile.feature": b"""Feature: Phone
  Scenario: PAY11-01 Create a phone
    When I create a phone
    Then it succeeds
""",
            "allure-results/demo-result.json": {
                "uuid": "r1",
                "name": "PAY11-01 Create a phone",
                "status": "failed",
                "labels": [{"name": "feature", "value": "Phone"}],
                "parameters": None,
                "steps": None,
            },
            "sources/validation-policies.json": {
                "policies": [
                    {
                        "source": source,
                        "description": "Name is required.",
                        "conditions": ["not value"],
                    },
                    {"source": "sample.Money.validate", "description": "Amount is positive."},
                ]
            },
            "sources/flows.json": {
                "flows": [
                    {
                        "operationId": "createMobilePhone",
                        "source": "sample.create",
                        "steps": ["Save"],
                    }
                ]
            },
            "sources/operation-links.json": {
                "service": "sample",
                "operations": [
                    {
                        "operationId": "createMobilePhone",
                        "flow": {"source": "sample.create"},
                        "policies": [{"source": source}],
                        "scenarios": [{"id": "PAY11-01"}],
                    }
                ],
            },
        },
    )
    policies = [d for d in docs if d.kind == "validation"]
    assert [d.payload["policyRole"] for d in policies] == ["referenced", "published_only"]
    link = next(d for d in docs if d.kind == "operation_links" and d.operation_id)
    related = [d for d in docs if d.id in link.payload["relatedDocumentIds"]]
    assert {d.kind for d in related} == {
        "validation",
        "flow",
        "scenario",
        "execution",
        "api_contract",
    }
    assert next(d for d in related if d.kind == "execution").payload["status"] == "failed"
    assert not link.payload.get("ingestionWarnings")


def test_feature_only_links_keep_their_granularity_and_source_urls(bundle_factory):
    docs, _, _ = imported(
        bundle_factory,
        {
            "sources/operation-links.json": {
                "operations": [
                    {
                        "operationId": "createMobilePhone",
                        "scenarios": ["tests/Sample.Acceptance.Tests/Features/mobile.feature"],
                    }
                ]
            },
        },
    )
    link = next(d for d in docs if d.kind == "operation_links")
    assert link.payload["scenarioLinkGranularity"] == ["feature"]
    scenarios = [d for d in docs if d.kind == "scenario"]
    assert all(d.id in link.payload["relatedDocumentIds"] for d in scenarios)
    assert (
        scenarios[0].source.url
        == "https://docs.example.test/livedoc/v1/products/bdd/mobile.feature"
    )
    assert link.source.url.endswith("/products/operation-links.json")
    assert link.payload["provenance"]["sourceFileSha256"]


def test_contract_retains_raw_refs_and_resolved_schema_without_inventing_example(bundle_factory):
    schema = {"$ref": "#/components/schemas/Phone"}
    docs, _, _ = imported(
        bundle_factory,
        {
            "sources/openapi.json": {
                "openapi": "3.0.3",
                "paths": {
                    "/phones": {
                        "post": {
                            "operationId": "createMobilePhone",
                            "requestBody": {"content": {"application/json": {"schema": schema}}},
                            "responses": {"201": {"description": "Created"}},
                        }
                    }
                },
                "components": {
                    "schemas": {
                        "Phone": {
                            "type": "object",
                            "required": ["name"],
                            "properties": {"name": {"type": "string"}},
                        }
                    }
                },
            }
        },
    )
    contract = next(d for d in docs if d.kind == "api_contract")
    assert contract.payload["rawRequestSchema"] == schema
    assert contract.payload["requestSchema"]["required"] == ["name"]
    assert contract.payload["exampleRequest"] is None
    assert contract.payload["hasDocumentedExample"] is False
