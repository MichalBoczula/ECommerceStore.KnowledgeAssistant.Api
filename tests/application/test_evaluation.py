import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.evaluation.models import EvaluationSuite
from knowledge_assistant.application.evaluation.runner import (
    checks_for,
    evaluate_suite,
    validate_inputs,
)
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.application.services.knowledge_service import KnowledgeService
from knowledge_assistant.domain.models import Corpus
from knowledge_assistant.infrastructure.retrieval.local_retriever import LocalRetriever


@pytest.fixture
def demo():
    return SimpleNamespace(
        corpus=Corpus.model_validate_json(Path("examples/demo-corpus.json").read_text())
    )


def suite():
    return EvaluationSuite.model_validate_json(Path("evaluation/demo-smoke.json").read_text())


def result(demo):
    s = suite()
    c = s.cases[0]
    retrieval = LocalRetriever(demo.corpus)
    docs = retrieval.search(
        Query(question=c.question, service=c.service, snapshot_id=demo.corpus.snapshot_id)
    )
    answer = KnowledgeService(demo.corpus, retrieval, EvidencePlanner()).query(
        Query(question=c.question, service=c.service, snapshot_id=demo.corpus.snapshot_id)
    )
    return s, c, docs, answer.model_copy(deep=True)


def test_reviewed_products_suite_has_25_distinct_cases():
    s = EvaluationSuite.model_validate_json(Path("evaluation/products-v1.json").read_text())
    assert len(s.cases) == 25 and not s.is_demo
    assert len({c.question for c in s.cases}) == 25
    assert s.cases[0].expect.contracts[0].operation_id == "CreateMobilePhone"
    assert s.cases[0].expect.contracts[0].model_example_required


def test_offline_smoke_reports_per_case_failures_recall_and_mode(demo):
    report = evaluate_suite(suite(), demo.corpus, LocalRetriever(demo.corpus), EvidencePlanner())
    assert report["passed"] == report["total"] == 4
    assert report["failed"] == 0 and report["expectedSourceRecall"] == 1
    assert report["isDemo"] and report["answerMode"] == "evidence"
    assert report["retrievalMode"] == "local_keyword"
    assert all(c["latencyMs"] >= 0 and c["checks"] for c in report["cases"])


@pytest.mark.parametrize(
    "mutation,check",
    [
        ("schema", "original_contracts"),
        ("method", "original_contracts"),
        ("rule", "grounded_rules"),
        ("citation", "citations"),
        ("source_commit", "citations"),
        ("scope", "scope"),
        ("order", "section_order"),
        ("example", "examples"),
        ("example_origin", "examples"),
        ("replace_documented", "examples"),
        ("status", "status"),
        ("not_found", "not_found"),
    ],
)
def test_counterfactual_answers_fail_the_expected_independent_check(demo, mutation, check):
    _, case, docs, answer = result(demo)
    contract = answer.sections[1]
    if mutation == "schema":
        contract.request_schema = {"type": "string"}
    elif mutation == "method":
        contract.method = "DELETE"
    elif mutation == "rule":
        answer.sections[0].rules[0].description = "Price may be negative"
    elif mutation == "citation":
        answer.sections[0].source_ids = ["fabricated"]
    elif mutation == "source_commit":
        answer.sources[0].commit_sha = "b" * 40
    elif mutation == "scope":
        answer.service = "payments"
    elif mutation == "order":
        answer.sections.reverse()
    elif mutation == "example":
        contract.example_request = {"price": -1}
    elif mutation == "example_origin":
        contract.example_origin = "unavailable"
    elif mutation == "replace_documented":
        contract.example_origin = "generated"
    elif mutation == "status":
        answer.status = "partial"
    elif mutation == "not_found":
        answer.status = "not_found"
    assert checks_for(case, answer, docs, demo.corpus)[check] is False


def test_unretrieved_source_is_rejected_even_when_it_exists_in_corpus(demo):
    _, case, docs, answer = result(demo)
    cited = answer.sources[0].id
    assert not checks_for(case, answer, [d for d in docs if d.source.id != cited], demo.corpus)[
        "citations"
    ]


def test_expected_evidence_recall_does_not_count_unrelated_hits(demo):
    _, case, docs, answer = result(demo)
    docs = [d for d in docs if d.kind != "validation"]
    checks = checks_for(case, answer, docs, demo.corpus)
    assert not checks["retrieval"] and not checks["grounded_rules"]


def test_generated_example_is_required_for_model_case_but_missing_is_honest_in_evidence(demo):
    _, case, docs, answer = result(demo)
    case.expect.contracts[0].require_example = False
    case.expect.contracts[0].model_example_required = True
    contract = answer.sections[1]
    # The source has no documented example in this variant.
    original = next(d for d in docs if d.kind == "api_contract")
    original.payload.update(hasDocumentedExample=False, exampleRequest=None)
    contract.example_request = None
    contract.example_schema_valid = None
    contract.example_origin = "unavailable"
    assert checks_for(case, answer, docs, demo.corpus)["examples"]
    answer.answer_mode = "model"
    assert not checks_for(case, answer, docs, demo.corpus)["examples"]


@pytest.mark.parametrize("field", ["commit_sha", "bundle_sha256", "snapshot_id"])
def test_stale_provenance_cannot_be_evaluated_as_the_reviewed_snapshot(demo, field):
    c = copy.deepcopy(demo.corpus)
    setattr(c.documents[0].source, field, "b" * (64 if field == "bundle_sha256" else 40))
    with pytest.raises(ValueError, match="source pins"):
        validate_inputs(suite(), c)


def test_duplicate_case_ids_and_unpinned_services_are_rejected():
    s = suite().model_dump()
    s["cases"].append(s["cases"][0])
    with pytest.raises(ValidationError):
        EvaluationSuite.model_validate(s)
    s = suite().model_dump()
    s["cases"][0]["service"] = "users"
    with pytest.raises(ValidationError):
        EvaluationSuite.model_validate(s)


def test_query_failure_is_failed_and_provider_message_is_not_exported(demo):
    class FailingRetriever:
        mode = "azure_hybrid"

        def search(self, query, limit=12):
            raise RuntimeError("private-provider-token")

    report = evaluate_suite(suite(), demo.corpus, FailingRetriever(), EvidencePlanner())
    assert report["failed"] == 4 and report["passed"] == 0
    assert all(c["failures"] == ["query_failed"] for c in report["cases"])
    assert "private-provider-token" not in json.dumps(report)
    assert "question" not in json.dumps(report)
