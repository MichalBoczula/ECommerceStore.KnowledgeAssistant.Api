from uuid import uuid4

from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.models import Document

from ..contracts import (
    Answer,
    ContractSection,
    ExplanationSection,
    Query,
    ScenarioSection,
    ValidationSection,
)
from ..planning import AnswerPlan
from .evidence_catalog import intent, policy_rules
from .request_examples import resolve_example


def assemble(
    query: Query,
    documents: list[Document],
    plan: AnswerPlan,
    answer_mode: str,
    retrieval_mode: str,
    is_demo: bool,
) -> Answer:
    by_id = {d.id: d for d in documents}
    sources = {d.source.id: d.source for d in documents}
    if (
        len(by_id) != len(documents)
        or any(sources[d.source.id] != d.source for d in documents)
        or any(
            d.source.service != query.service or d.source.snapshot_id != query.snapshot_id
            for d in documents
        )
    ):
        raise EvidenceVerificationError(
            "Answer evidence has duplicate identities or escaped its scope"
        )
    if plan.status == "not_found" and plan.sections:
        raise EvidenceVerificationError("A not-found plan must not contain answer sections")
    sections, used, limitations = [], set(), list(plan.limitations)
    partial = plan.status == "partial"
    validation_rules, validation_sources, selected_rules = [], [], set()
    coverage = {}
    emitted = set()
    operations = {
        by_id[s.document_id].operation_id
        for s in plan.sections
        if s.type == "api_contract" and s.document_id in by_id
    }

    def limit(note: str, incomplete: bool = True):
        nonlocal partial
        limitations.append(note)
        partial = partial or incomplete

    def check_sources(ids: list[str]):
        if not ids or any(source not in sources for source in ids):
            raise EvidenceVerificationError("Answer cites missing or unknown evidence")

    for section in plan.sections:
        check_sources(section.source_ids)
        doc = by_id.get(section.document_id)
        if section.document_id is not None and (
            doc is None or doc.source.id not in section.source_ids
        ):
            raise EvidenceVerificationError("Artifact is not part of the cited retrieval evidence")
        if section.type != "validation" and section.rule_ids:
            raise EvidenceVerificationError("Rule references belong only to validation sections")
        if section.type != "api_contract" and section.generated_example_json is not None:
            raise EvidenceVerificationError("Generated request belongs only to an API contract")
        if section.type == "explanation":
            if not section.text.strip():
                limit("An empty explanation was omitted.")
                continue
            sections.append(
                ExplanationSection(
                    type="explanation",
                    title=section.title,
                    content=section.text,
                    source_ids=list(dict.fromkeys(section.source_ids)),
                )
            )
            used.update(section.source_ids)
            continue
        if doc is None:
            raise EvidenceVerificationError("Artifact is not part of the cited retrieval evidence")
        if section.type == "validation":
            if doc.kind != "validation":
                raise EvidenceVerificationError("Wrong source type for validation section")
            catalog = policy_rules(doc)
            if any(identity not in catalog for identity in section.rule_ids):
                raise EvidenceVerificationError("Validation references an unknown documented rule")
            if not section.rule_ids:
                limit("A validation section without documented rules was omitted.")
                continue
            for identity in section.rule_ids:
                if identity not in selected_rules:
                    selected_rules.add(identity)
                    validation_rules.append(catalog[identity])
            validation_sources.append(doc.source.id)
            used.add(doc.source.id)
            coverage.setdefault(doc.id, set()).update(section.rule_ids)
            role = doc.payload.get("policyRole")
            if not is_demo and role != "referenced":
                limit(
                    "Policy " + doc.title + " has no explicit operation link; runtime enforcement "
                    "was not established."
                )
            elif (
                not is_demo
                and operations
                and not operations.intersection(doc.payload.get("operationIds", []))
            ):
                limit(
                    "Policy "
                    + doc.title
                    + " is not explicitly linked to the selected API contract."
                )
            continue
        identity = (section.type, doc.id)
        if identity in emitted:
            continue
        emitted.add(identity)
        used.add(doc.source.id)
        common = {"title": doc.title, "source_ids": [doc.source.id]}
        if section.type == "scenario":
            if doc.kind not in {"scenario", "execution"}:
                raise EvidenceVerificationError("Wrong source type for scenario section")
            status = doc.payload.get("status") if doc.kind == "execution" else None
            sections.append(
                ScenarioSection(
                    type="scenario", content=doc.content, execution_status=status, **common
                )
            )
            if status is not None and status != "passed":
                limit(
                    "The cited execution reports "
                    + status
                    + "; expected behavior is not confirmed by that run."
                )
            continue
        if doc.kind != "api_contract":
            raise EvidenceVerificationError("Wrong source type for API contract section")
        payload = doc.payload
        if not isinstance(payload.get("requestSchema", {}), dict):
            raise EvidenceVerificationError(
                "Request schema is not supported by the answer contract"
            )
        example, origin, valid, notes, incomplete = resolve_example(
            payload, section.generated_example_json
        )
        for note in notes:
            limit(note, incomplete)
        sections.append(
            ContractSection(
                type="api_contract",
                method=payload["method"],
                path=payload["path"],
                operation_id=payload["operationId"],
                media_type=payload["mediaType"],
                request_schema=payload.get("requestSchema", {}),
                example_request=example,
                example_origin=origin,
                example_schema_valid=valid,
                reference_schemas=payload.get("referenceSchemas", {}),
                **common,
            )
        )
    for document_id, selected in coverage.items():
        if selected != set(policy_rules(by_id[document_id])):
            limit(
                "Only a subset of the documented rules was selected for " + by_id[document_id].title
            )
    if validation_rules:
        sections.append(
            ValidationSection(
                type="validation",
                title="Validation",
                rules=validation_rules,
                source_ids=list(dict.fromkeys(validation_sources)),
            )
        )
    requested = intent(query.question)
    if requested.validation and not validation_rules:
        limit("The requested validation rules were not established by retrieved evidence.")
    if requested.contract and not any(isinstance(s, ContractSection) for s in sections):
        limit("The requested API contract was not established by retrieved evidence.")
    for doc in documents:
        if (
            doc.kind == "operation_links"
            and doc.operation_id in operations
            and doc.payload.get("ingestionWarnings")
        ):
            used.add(doc.source.id)
            limit(
                "Documentation links for " + str(doc.operation_id) + " contain unresolved evidence."
            )
    if validation_rules and not is_demo:
        limit(
            "Published rules and documentation links do not prove that every rule ran at runtime.",
            False,
        )
    priority = {"validation": 0, "api_contract": 1, "explanation": 2, "scenario": 3}
    sections.sort(key=lambda s: priority[s.type])
    return Answer(
        request_id=str(uuid4()),
        snapshot_id=query.snapshot_id,
        service=query.service,
        status="not_found" if not sections else "partial" if partial else "answered",
        answer_mode=answer_mode,
        retrieval_mode=retrieval_mode,
        is_demo=is_demo,
        sections=sections,
        sources=[s for identity, s in sources.items() if identity in used],
        limitations=list(dict.fromkeys(limitations)),
    )
