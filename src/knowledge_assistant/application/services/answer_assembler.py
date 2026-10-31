import json
from uuid import uuid4

from jsonschema import Draft202012Validator
from openapi_schema_validator import OAS30Validator

from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.models import Document
from knowledge_assistant.domain.text import tokens

from ..contracts import (
    Answer,
    ContractSection,
    ExplanationSection,
    Query,
    ScenarioSection,
    ValidationSection,
)
from ..planning import AnswerPlan


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
    sections = []
    used = set()
    limitations = list(plan.limitations)

    def check_sources(ids: list[str]) -> None:
        if not ids or any(source not in sources for source in ids):
            raise EvidenceVerificationError("Answer cites missing or unknown evidence")
        used.update(ids)

    for section in plan.sections:
        check_sources(section.source_ids)
        common = {"title": section.title, "source_ids": section.source_ids}
        if section.type == "explanation":
            sections.append(ExplanationSection(type="explanation", content=section.text, **common))
        elif section.type == "validation":
            for rule in section.rules:
                check_sources(rule.source_ids)
            sections.append(ValidationSection(type="validation", rules=section.rules, **common))
        else:
            doc = by_id.get(section.document_id)
            if doc is None or doc.source.id not in section.source_ids:
                raise EvidenceVerificationError(
                    "Artifact is not part of the cited retrieval evidence"
                )
            if section.type == "scenario":
                if doc.kind not in {"scenario", "execution"}:
                    raise EvidenceVerificationError("Wrong source type for scenario section")
                sections.append(
                    ScenarioSection(
                        type="scenario",
                        content=doc.content,
                        execution_status=doc.payload.get("status"),
                        **common,
                    )
                )
                continue
            if doc.kind != "api_contract":
                raise EvidenceVerificationError("Wrong source type for API contract section")
            payload = doc.payload
            example = payload.get("exampleRequest")
            origin = "openapi" if example is not None else "unavailable"
            if example is None and section.generated_example_json is not None:
                try:
                    example = json.loads(section.generated_example_json)
                    origin = "generated"
                except json.JSONDecodeError:
                    limitations.append("A generated request was invalid JSON and was omitted.")
            valid = None
            schema = payload.get("requestSchema", {})
            if example is not None:
                validator = (
                    OAS30Validator
                    if str(payload.get("openapiVersion", "")).startswith("3.0")
                    else Draft202012Validator
                )
                validation_schema = {
                    **schema,
                    "components": {"schemas": payload.get("referenceSchemas", {})},
                }
                errors = list(validator(validation_schema).iter_errors(example))
                if errors:
                    if origin == "generated":
                        example, origin = None, "unavailable"
                        limitations.append(
                            "A generated request failed schema validation and was omitted."
                        )
                    else:
                        limitations.append(
                            "The documented example does not satisfy its request schema."
                        )
                    valid = False
                else:
                    valid = True
            if example is None:
                limitations.append(
                    "No schema-valid request example is available for " + payload["path"]
                )
            sections.append(
                ContractSection(
                    type="api_contract",
                    method=payload["method"],
                    path=payload["path"],
                    operation_id=payload["operationId"],
                    media_type=payload["mediaType"],
                    request_schema=schema,
                    example_request=example,
                    example_origin=origin,
                    example_schema_valid=valid,
                    reference_schemas=payload.get("referenceSchemas", {}),
                    **common,
                )
            )
    requested_contract = bool(
        tokens(query.question) & {"contract", "json", "request", "schema", "payload"}
    )
    if requested_contract and not any(isinstance(s, ContractSection) for s in sections):
        limitations.append("The requested API contract was not established by retrieved evidence.")
    partial = plan.status != "answered" or any(
        "not established" in s
        or "omitted" in s
        or "No schema-valid" in s
        or "does not satisfy" in s
        for s in limitations
    )
    return Answer(
        request_id=str(uuid4()),
        snapshot_id=query.snapshot_id,
        service=query.service,
        status="not_found" if not sections else "partial" if partial else "answered",
        answer_mode=answer_mode,
        retrieval_mode=retrieval_mode,
        is_demo=is_demo,
        sections=sections,
        sources=[s for key, s in sources.items() if key in used],
        limitations=limitations,
    )
