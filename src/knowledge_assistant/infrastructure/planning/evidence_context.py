"""Bounded, explicit evidence catalog for model selection, without full payload duplication."""

import json
from dataclasses import dataclass

from knowledge_assistant.application.planning import AnswerPlan
from knowledge_assistant.application.services.evidence_catalog import (
    documented_example,
    intent,
    policy_rules,
)
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.models import Document

MAX_INPUT_BYTES = 64000
MAX_DOCUMENT_BYTES = 12000


def encoded(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


def snippet(value: str, size: int) -> str:
    return value.encode()[:size].decode("utf-8", errors="ignore")


def schema_context(payload: dict) -> dict | None:
    schema = payload.get("requestSchema", {})
    if not isinstance(schema, dict):
        return None
    definitions = payload.get("referenceSchemas", {})
    root = {**schema, "components": {"schemas": definitions}}
    selected, visited, pending = {}, set(), [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            for key in ("$ref", "$dynamicRef", "$recursiveRef"):
                reference = item.get(key)
                if reference is None:
                    continue
                if not isinstance(reference, str):
                    return None
                if reference in visited:
                    continue
                visited.add(reference)
                if reference == "#":
                    continue
                if not isinstance(reference, str) or not reference.startswith("#/"):
                    return None
                parts = [p.replace("~1", "/").replace("~0", "~") for p in reference[2:].split("/")]
                target = root
                try:
                    for part in parts:
                        target = target[int(part)] if isinstance(target, list) else target[part]
                    if parts[:2] == ["components", "schemas"]:
                        selected[parts[2]] = definitions[parts[2]]
                except (KeyError, IndexError, TypeError, ValueError):
                    return None
                pending.append(target)
            pending.extend(item.values())
    return {"requestSchema": schema, "referenceSchemas": selected}


@dataclass
class EvidenceContext:
    body: dict
    document_ids: set[str]
    source_ids: set[str]
    rules: dict[str, set[str]]
    example_ids: set[str]
    documented_examples: set[str]
    limited: bool

    def check(self, plan: AnswerPlan) -> AnswerPlan:
        plan = plan.model_copy(deep=True)
        for section in plan.sections:
            if any(source not in self.source_ids for source in section.source_ids):
                raise EvidenceVerificationError("Model cites evidence outside its supplied context")
            if section.document_id is not None and section.document_id not in self.document_ids:
                raise EvidenceVerificationError(
                    "Model references an artifact outside its supplied context"
                )
            if any(
                rule not in self.rules.get(section.document_id, set()) for rule in section.rule_ids
            ):
                raise EvidenceVerificationError(
                    "Model selected a rule absent from its supplied context"
                )
            if (
                section.generated_example_json is not None
                and section.document_id not in self.example_ids
            ):
                section.generated_example_json = None
                if section.document_id not in self.documented_examples:
                    plan.limitations.append(
                        "Generated example omitted because its complete schema "
                        "was not available in model context."
                    )
                    if plan.status == "answered":
                        plan.status = "partial"
        if self.limited:
            plan.limitations.append(
                "Model context omitted some retrieved content; this answer "
                "uses the supplied subset."
            )
            if plan.status == "answered":
                plan.status = "partial"
        return plan


def build_context(question: str, documents: list[Document]) -> EvidenceContext:
    requested = intent(question)
    body = {
        "question": question,
        "requestedSections": {
            "validation": requested.validation,
            "apiContract": requested.contract,
        },
        "evidence": [],
    }
    result = EvidenceContext(body, set(), set(), {}, set(), set(), False)
    priority = {
        "api_contract": 0 if requested.contract else 3,
        "validation": 1 if requested.validation else 3,
    }
    ordered = sorted(documents, key=lambda d: priority.get(d.kind, 2))
    for doc in ordered:
        entry = {
            "documentId": doc.id,
            "sourceId": doc.source.id,
            "kind": doc.kind,
            "title": snippet(doc.title, 1000),
            "service": doc.source.service,
            "snapshotId": doc.source.snapshot_id,
            "commitSha": doc.source.commit_sha,
            "sourcePath": snippet(doc.source.path, 1000),
            "operationIds": doc.payload.get(
                "operationIds", [doc.operation_id] if doc.operation_id else []
            ),
        }
        limited = len(doc.title.encode()) > 1000 or len(doc.source.path.encode()) > 1000
        warnings = doc.payload.get("ingestionWarnings", [])
        entry["relationshipWarnings"] = [
            {"code": warning.get("code"), "target": warning.get("target")}
            for warning in warnings[:8]
        ]
        limited = limited or len(warnings) > 8
        if doc.kind == "validation":
            entry.update(policyRole=doc.payload.get("policyRole", "unspecified"), rules=[])
            for identity, rule in policy_rules(doc).items():
                row = {"ruleId": identity, "field": rule.field, "description": rule.description}
                if len(encoded({**entry, "rules": [*entry["rules"], row]})) > MAX_DOCUMENT_BYTES:
                    limited = True
                    continue
                entry["rules"].append(row)
        elif doc.kind == "api_contract":
            entry.update(
                {
                    k: doc.payload.get(k)
                    for k in ("method", "path", "operationId", "mediaType", "openapiVersion")
                }
            )
            entry["hasDocumentedExample"] = documented_example(doc.payload)
            schema = schema_context(doc.payload)
            if schema is not None and len(encoded({**entry, **schema})) <= MAX_DOCUMENT_BYTES:
                entry.update(schema)
                entry["completeRequestSchemaAvailable"] = True
            else:
                entry["completeRequestSchemaAvailable"] = False
                limited = True
        else:
            entry["content"] = snippet(doc.content, 3500)
            limited = limited or len(doc.content.encode()) > 3500
            if doc.kind == "execution":
                entry["executionStatus"] = doc.payload.get("status")
                entry["evidenceRole"] = "observed_execution"
                entry["matchedExpectedScenarios"] = doc.payload.get("scenarioDocumentIds", [])
            elif doc.kind == "scenario":
                entry["evidenceRole"] = "expected_behavior"
        entry["contentLimited"] = limited
        if (
            len(encoded(entry)) > MAX_DOCUMENT_BYTES
            or len(encoded({**body, "evidence": [*body["evidence"], entry]})) > MAX_INPUT_BYTES
        ):
            result.limited = True
            continue
        body["evidence"].append(entry)
        result.limited = result.limited or limited
        result.document_ids.add(doc.id)
        result.source_ids.add(doc.source.id)
        result.rules[doc.id] = {r["ruleId"] for r in entry.get("rules", [])}
        if entry.get("hasDocumentedExample"):
            result.documented_examples.add(doc.id)
        elif entry.get("completeRequestSchemaAvailable"):
            result.example_ids.add(doc.id)
    return result
