"""Include the observed Allure examples reviewed for an FAQ or its API operation."""

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.planning import AnswerPlan, PlanSection
from knowledge_assistant.domain.faq import (
    exact_faq_examples,
    operation_example,
    question_key,
)
from knowledge_assistant.domain.models import Document

from .evidence_catalog import intent


def _references(
    query: Query, documents: list[Document], plan: AnswerPlan, operation_lookup: bool
) -> list[dict]:
    question = question_key(query.question)
    for document in documents:
        if references := exact_faq_examples(document, question):
            return references
    if not operation_lookup or intent(query.question).validation:
        return []
    contracts = {section.document_id for section in plan.sections if section.type == "api_contract"}
    if len(contracts) != 1:
        return []
    contract = next(
        (doc for doc in documents if doc.id in contracts and doc.kind == "api_contract"),
        None,
    )
    return operation_example(contract) if contract is not None else []


def add_allure_examples(
    query: Query, documents: list[Document], plan: AnswerPlan, *, operation_lookup: bool
) -> AnswerPlan:
    if plan.status == "not_found":
        return plan
    references = _references(query, documents, plan, operation_lookup)
    if not references:
        return plan
    executions = {
        doc.id: doc
        for doc in documents
        if doc.kind == "execution" and doc.payload.get("status") == "passed"
    }
    shown = {section.document_id for section in plan.sections if section.type == "scenario"}
    plan = plan.model_copy(deep=True)
    for reference in references:
        doc = executions.get(reference["executionDocumentId"])
        if doc is None:
            continue
        if doc.id not in shown and len(plan.sections) < 12:
            shown.add(doc.id)
            plan.sections.append(
                PlanSection(
                    type="scenario",
                    title=doc.title,
                    text="",
                    rule_ids=[],
                    document_id=doc.id,
                    generated_example_json=None,
                    source_ids=[doc.source.id],
                )
            )
        if doc.id in shown and reference["coverage"] == "related":
            plan.limitations.append(
                f"Related Allure example: {doc.title}. Consult the cited documentation "
                "for the specific rule or API contract."
            )
    return plan
