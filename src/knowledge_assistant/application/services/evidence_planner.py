from knowledge_assistant.domain.models import Document
from knowledge_assistant.domain.text import FORMAT_TERMS, tokens

from ..planning import AnswerPlan, PlanSection
from .evidence_catalog import (
    intent,
    lookup_contracts,
    policy_rules,
    requests_endpoint_matrix,
    select_contract,
    select_policies,
)


def _validation_section(doc: Document) -> PlanSection | None:
    if not (rules := policy_rules(doc)):
        return None
    return PlanSection(
        type="validation",
        title="Validation",
        text="",
        rule_ids=list(rules)[:100],
        document_id=doc.id,
        generated_example_json=None,
        source_ids=[doc.source.id],
    )


def _contract_section(doc: Document) -> PlanSection:
    return PlanSection(
        type="api_contract",
        title=doc.title,
        text="",
        rule_ids=[],
        document_id=doc.id,
        generated_example_json=None,
        source_ids=[doc.source.id],
    )


def plan_api_lookup(question: str, documents: list[Document], limit: int) -> AnswerPlan | None:
    contracts = lookup_contracts(question, documents)
    if not contracts:
        return None
    if requests_endpoint_matrix(question):
        rows = [
            f"{doc.payload['method']} {doc.payload['path']} — "
            + doc.payload.get("responsibility", doc.title)
            for doc in contracts
        ]
        sections = [
            PlanSection(
                type="explanation",
                title="Endpoint matrix",
                text="\n".join(rows),
                rule_ids=[],
                document_id=None,
                generated_example_json=None,
                source_ids=[doc.source.id for doc in contracts],
            )
        ]
    else:
        sections = [_contract_section(doc) for doc in contracts]
    if intent(question).validation:
        operations = {doc.operation_id for doc in contracts if doc.operation_id}
        for doc in documents:
            if operations.intersection(doc.payload.get("operationIds", [])):
                if section := _validation_section(doc):
                    sections.append(section)
    limited = len(sections) > 12 or len(documents) == limit
    return AnswerPlan(
        status="partial" if limited else "answered",
        sections=sections[:12],
        limitations=["The documentation lookup reached its evidence limit."] if limited else [],
    )


class EvidencePlanner:
    mode = "evidence"

    def plan(self, question: str, documents: list[Document]) -> AnswerPlan:
        sections = []
        requested = intent(question)
        contract = select_contract(question, documents) if requested.contract else None
        if requested.validation:
            for doc in select_policies(question, documents, contract):
                if section := _validation_section(doc):
                    sections.append(section)
        if contract is not None:
            sections.append(_contract_section(contract))
        flows = [
            d for d in documents if d.kind == "flow" and d.title.casefold() in question.casefold()
        ]
        for doc in flows:
            sections.append(
                PlanSection(
                    type="explanation",
                    title=doc.title,
                    text=doc.content,
                    rule_ids=[],
                    document_id=doc.id,
                    generated_example_json=None,
                    source_ids=[doc.source.id],
                )
            )
        if not sections and not requested.contract:
            terms = tokens(question) - FORMAT_TERMS
            doc = next(
                (d for d in documents if terms & tokens(f"{d.title} {d.content}")),
                None,
            )
            if doc is not None:
                sections.append(
                    PlanSection(
                        type="scenario" if doc.kind in {"scenario", "execution"} else "explanation",
                        title=doc.title,
                        text=doc.content,
                        rule_ids=[],
                        document_id=doc.id,
                        generated_example_json=None,
                        source_ids=[doc.source.id],
                    )
                )
        return AnswerPlan(
            status="answered" if sections else "not_found",
            sections=sections[:12],
            limitations=["Evidence view: extracted documentation; model synthesis is not enabled."],
        )
