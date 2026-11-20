"""Bounded one-hop expansion through explicit, same-snapshot operation relationships."""

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.services.evidence_catalog import intent, lookup_contracts
from knowledge_assistant.domain.faq import (
    exact_faq_examples,
    faq_aliases,
    operation_example,
    question_key,
)
from knowledge_assistant.domain.models import Corpus, Document


def expand_context(
    hits: list[Document], corpus: Corpus, query: Query, limit: int
) -> list[Document]:
    scoped = [
        doc
        for doc in corpus.documents
        if doc.source.service == query.service and doc.source.snapshot_id == query.snapshot_id
    ]
    question = question_key(query.question)
    matched_faq = [
        doc
        for doc in scoped
        if any(question_key(alias) == question for alias in faq_aliases(doc).values())
    ]
    contracts = lookup_contracts(query.question, scoped)
    validation_requested = intent(query.question).validation
    references = [example for doc in matched_faq for example in exact_faq_examples(doc, question)]
    if not references and len(contracts) == 1 and not validation_requested:
        references = operation_example(contracts[0])
    example_ids = {reference["executionDocumentId"] for reference in references}
    examples = [doc for doc in scoped if doc.id in example_ids and doc.kind == "execution"]
    if contracts:
        if not validation_requested:
            return list({d.id: d for d in [*contracts, *matched_faq, *examples]}.values())[:limit]
        operations = {doc.operation_id for doc in contracts if doc.operation_id}
        policies = [
            doc
            for doc in scoped
            if doc.kind == "validation"
            and operations.intersection(doc.payload.get("operationIds", []))
        ]
        links = [
            doc
            for doc in scoped
            if doc.kind == "operation_links" and doc.operation_id in operations
        ]
        return list(
            {d.id: d for d in [*contracts, *policies, *matched_faq, *examples, *links]}.values()
        )[:limit]
    if not hits and not matched_faq:
        return []
    seeds = list({doc.id: doc for doc in [*matched_faq, *hits]}.values())[: max(1, limit // 2)]
    operations = {op for doc in seeds for op in doc.payload.get("operationIds", [])}
    operations.update(d.operation_id for d in seeds if d.operation_id)
    related_ids = {identity for d in seeds for identity in d.payload.get("relatedDocumentIds", [])}
    related_ids.update(
        identity for doc in matched_faq for identity in doc.payload.get("executionDocumentIds", [])
    )
    candidates = [
        d
        for d in scoped
        if (
            d.id in related_ids
            or operations & set(d.payload.get("operationIds", []))
            or d.operation_id in operations
        )
    ]
    priority = {
        "api_contract": 0,
        "validation": 1,
        "flow": 2,
        "operation_links": 3,
        "scenario": 4,
        "execution": 5,
    }
    candidates.sort(key=lambda d: (priority[d.kind], d.id))
    result, seen = [], set()
    for doc in [*matched_faq, *examples, *seeds, *candidates, *hits]:
        if doc.id not in seen:
            seen.add(doc.id)
            result.append(doc)
        if len(result) == limit:
            break
    return result
