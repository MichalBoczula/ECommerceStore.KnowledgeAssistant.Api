"""Bounded one-hop expansion through explicit, same-snapshot operation relationships."""

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.domain.models import Corpus, Document


def expand_context(
    hits: list[Document], corpus: Corpus, query: Query, limit: int
) -> list[Document]:
    if not hits:
        return []
    seeds = hits[: max(1, limit // 2)]
    operations = {op for doc in seeds for op in doc.payload.get("operationIds", [])}
    operations.update(d.operation_id for d in seeds if d.operation_id)
    related_ids = {identity for d in seeds for identity in d.payload.get("relatedDocumentIds", [])}
    candidates = [
        d
        for d in corpus.documents
        if d.source.service == query.service
        and d.source.snapshot_id == query.snapshot_id
        and (
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
    for doc in [*seeds, *candidates, *hits]:
        if doc.id not in seen:
            seen.add(doc.id)
            result.append(doc)
        if len(result) == limit:
            break
    return result
