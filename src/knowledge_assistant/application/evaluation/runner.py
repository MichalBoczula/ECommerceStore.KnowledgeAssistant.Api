"""Evaluate factual sentinels against original evidence, without a model as judge."""

import hashlib
import json
import math
from datetime import UTC, datetime
from time import perf_counter

from knowledge_assistant.domain.models import Corpus, Document

from ..contracts import Answer, Query
from ..ports import AnswerPlanner, KnowledgeRetriever
from ..services.knowledge_service import KnowledgeService
from ..services.request_examples import validate_example
from .models import EvaluationCase, EvaluationSuite


def validate_inputs(suite: EvaluationSuite, corpus: Corpus) -> None:
    if suite.is_demo != corpus.is_demo:
        raise ValueError("Evaluation suite and corpus disagree on demo status")
    if len({d.id for d in corpus.documents}) != len(corpus.documents):
        raise ValueError("Evaluation corpus has duplicate document IDs")
    for pin in suite.source_pins:
        sources = [d.source for d in corpus.documents if d.source.service == pin.service]
        if not sources or any(
            s.commit_sha != pin.commit_sha
            or s.bundle_sha256 != pin.bundle_sha256
            or s.snapshot_id != corpus.snapshot_id
            for s in sources
        ):
            raise ValueError("Evaluation corpus does not match its reviewed source pins")
    paths = {(d.source.service, d.source.path) for d in corpus.documents}
    for case in suite.cases:
        expected = case.expect
        if any((case.service, p) not in paths for p in expected.required_source_paths):
            raise ValueError("Evaluation expects a source absent from the pinned corpus")


class RecordedRetriever:
    def __init__(self, inner: KnowledgeRetriever):
        self.inner = inner
        self.mode = inner.mode
        self.documents: list[Document] = []

    def search(self, query: Query, limit: int = 12) -> list[Document]:
        found = self.inner.search(query, limit)
        self.documents = [d.model_copy(deep=True) for d in found]
        return found


def checks_for(case: EvaluationCase, answer: Answer, documents: list[Document], corpus: Corpus):
    expected = case.expect
    by_source = {d.source.id: d for d in documents}
    citations = {s.id: s for s in answer.sources}
    paths = {s.path for s in answer.sources}
    required = set(expected.required_source_paths)
    retrieved = {d.source.path for d in documents}
    sections = answer.sections
    types = [s.type for s in sections]
    rules = [r for s in sections if s.type == "validation" for r in s.rules]
    contracts = [s for s in sections if s.type == "api_contract"]
    descriptions = {r.description for r in rules}
    # Compare original field/text/source triples, independently of planner rule identities.
    known_rules = set()
    for d in documents:
        if d.kind != "validation":
            continue
        value = d.payload.get("value", {})
        entries = d.payload.get("normalizedRules", value.get("rules", []))
        for r in entries:
            if isinstance(r, dict):
                known_rules.add(
                    (d.source.id, str(r.get("field", "Documented rule")), r.get("description"))
                )
    checks = {
        "scope": answer.snapshot_id == corpus.snapshot_id
        and answer.service == case.service
        and answer.is_demo == corpus.is_demo
        and all(
            d.source.snapshot_id == corpus.snapshot_id and d.source.service == case.service
            for d in documents
        ),
        "citations": all(s.id in by_source and s == by_source[s.id].source for s in answer.sources)
        and all(s.source_ids and all(i in citations for i in s.source_ids) for s in sections),
        "grounded_rules": all(
            r.source_ids
            and all(
                (i, r.field, r.description) in known_rules and i in citations for i in r.source_ids
            )
            for r in rules
        ),
        "status": answer.status in expected.statuses,
        "section_types": all(t in types for t in expected.section_types),
        "section_order": not ("validation" in types and "api_contract" in types)
        or types.index("validation") < types.index("api_contract"),
        "not_found": answer.status != "not_found" or not sections and not answer.sources,
        "rules": set(expected.rule_texts) <= descriptions
        and (not expected.rules_exact or descriptions == set(expected.rule_texts)),
        "sources": required <= paths
        and (not expected.allowed_source_paths or paths <= set(expected.allowed_source_paths)),
        "retrieval": required <= retrieved,
        "contracts": not expected.contracts
        or {(c.operation_id, c.method, c.path) for c in contracts}
        == {(c.operation_id, c.method, c.path) for c in expected.contracts},
        "text": all(
            fragment.casefold()
            in "\n".join(
                s.content for s in sections if s.type in {"explanation", "scenario"}
            ).casefold()
            for fragment in expected.text_fragments
        ),
        "limitations": (answer.status not in {"partial", "not_found"} or bool(answer.limitations))
        and all(
            fragment.casefold() in "\n".join(answer.limitations).casefold()
            for fragment in expected.limitation_fragments
        ),
        "original_contracts": True,
        "examples": True,
    }
    for contract in contracts:
        originals = [
            by_source[i]
            for i in contract.source_ids
            if i in by_source and by_source[i].kind == "api_contract"
        ]
        if len(originals) != 1:
            checks["original_contracts"] = False
            continue
        payload = originals[0].payload
        documented = bool(
            payload.get("hasDocumentedExample", payload.get("exampleRequest") is not None)
        )
        checks["original_contracts"] &= all(
            [
                contract.operation_id == payload["operationId"],
                contract.method == payload["method"],
                contract.path == payload["path"],
                contract.media_type == payload["mediaType"],
                contract.request_schema == payload.get("requestSchema", {}),
                contract.reference_schemas == payload.get("referenceSchemas", {}),
            ]
        )
        if contract.example_origin == "unavailable":
            checks["examples"] &= (
                contract.example_request is None
                and contract.example_schema_valid is None
                and not documented
            )
        else:
            valid = validate_example(payload, contract.example_request)
            checks["examples"] &= valid is contract.example_schema_valid
            if contract.example_origin == "generated":
                checks["examples"] &= valid is True and not documented
            else:
                checks["examples"] &= bool(
                    payload.get("hasDocumentedExample", payload.get("exampleRequest") is not None)
                ) and contract.example_request == payload.get("exampleRequest")
        if any(
            c.operation_id == contract.operation_id
            and (c.require_example or c.model_example_required and answer.answer_mode == "model")
            for c in expected.contracts
        ):
            checks["examples"] &= (
                contract.example_origin != "unavailable" and contract.example_schema_valid is True
            )
    return checks


def evaluate_suite(
    suite: EvaluationSuite,
    corpus: Corpus,
    retriever: KnowledgeRetriever,
    planner: AnswerPlanner,
    max_candidates: int = 12,
) -> dict:
    validate_inputs(suite, corpus)
    recorded = RecordedRetriever(retriever)
    service = KnowledgeService(corpus, recorded, planner, max_candidates)
    results = []
    for case in suite.cases:
        recorded.documents = []
        started = perf_counter()
        result = {"id": case.id}
        try:
            answer = service.query(
                Query(question=case.question, service=case.service, snapshot_id=corpus.snapshot_id)
            )
            checks = checks_for(case, answer, recorded.documents, corpus)
            result.update(
                status=answer.status,
                checks=checks,
                failures=[k for k, v in checks.items() if not v],
                citedSourcePaths=sorted(s.path for s in answer.sources),
                operationIds=[s.operation_id for s in answer.sections if s.type == "api_contract"],
            )
        except Exception:
            # Provider messages, question text, answers and secrets never enter reports.
            result.update(status="error", checks={}, failures=["query_failed"])
        required = set(case.expect.required_source_paths)
        retrieved = {d.source.path for d in recorded.documents}
        result.update(
            passed=not result["failures"],
            retrievedSourcePaths=sorted(retrieved),
            expectedSources=len(required),
            retrievedExpectedSources=len(required & retrieved),
            latencyMs=round((perf_counter() - started) * 1000, 2),
        )
        results.append(result)
    passed = sum(r["passed"] for r in results)
    latencies = sorted(r["latencyMs"] for r in results)
    expected_sources = sum(r["expectedSources"] for r in results)
    return {
        "schemaVersion": 1,
        "suiteId": suite.id,
        "suiteSha256": hashlib.sha256(
            json.dumps(
                suite.model_dump(by_alias=True), sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest(),
        "createdAt": datetime.now(UTC).isoformat(),
        "snapshotId": corpus.snapshot_id,
        "isDemo": corpus.is_demo,
        "sourcePins": [p.model_dump(by_alias=True) for p in suite.source_pins],
        "corpusDocuments": len(corpus.documents),
        "answerMode": planner.mode,
        "retrievalMode": retriever.mode,
        "maxCandidates": max_candidates,
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "passRate": passed / len(results),
        "expectedSourceRecall": (
            sum(r["retrievedExpectedSources"] for r in results) / expected_sources
            if expected_sources
            else None
        ),
        "latencyP95Ms": latencies[math.ceil(len(latencies) * 0.95) - 1],
        "cases": results,
    }
