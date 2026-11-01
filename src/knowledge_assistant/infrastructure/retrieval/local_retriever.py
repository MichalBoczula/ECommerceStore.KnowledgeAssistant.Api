from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.services.evidence_catalog import intent
from knowledge_assistant.domain.models import Corpus, Document
from knowledge_assistant.domain.text import FORMAT_TERMS, tokens

from .context import expand_context


class LocalRetriever:
    mode = "local_keyword"

    def __init__(self, corpus: Corpus):
        self.corpus = corpus

    def search(self, query: Query, limit: int = 12) -> list[Document]:
        terms = tokens(query.question) - FORMAT_TERMS
        requested = intent(query.question)
        scored = []
        for doc in self.corpus.documents:
            if doc.source.service != query.service or doc.source.snapshot_id != query.snapshot_id:
                continue
            matched = terms & tokens(doc.title + " " + doc.content)
            if not matched:
                continue
            score = len(matched) + len(terms & tokens(doc.title)) * 3
            # Direct typed evidence must compete with verbose, repeated execution records.
            if doc.kind == "validation" and requested.validation:
                score += 12
            if doc.kind == "api_contract" and requested.contract:
                score += 12
            if doc.operation_id and doc.operation_id.casefold() in query.question.casefold():
                score += 12
            if doc.kind == "api_contract" and doc.payload.get("method") == "POST":
                score += 2 if terms & {"add", "create", "new", "post"} else 0
            scored.append((score, doc))
        hits = [doc for _, doc in sorted(scored, key=lambda x: (-x[0], x[1].id))[:limit]]
        return expand_context(hits, self.corpus, query, limit)
