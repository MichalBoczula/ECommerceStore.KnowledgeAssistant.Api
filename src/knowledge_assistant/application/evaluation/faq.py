"""Measure whether unseen wording retrieves the intended LiveDocs document."""

from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from knowledge_assistant.domain.common.model import Model
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.faq import faq_aliases, question_key
from knowledge_assistant.domain.models import Corpus

from ..contracts import Query
from ..errors import DependencyUnavailableError
from ..ports import KnowledgeRetriever


class FaqCase(Model):
    faq_id: str = Field(pattern=r"^[A-Z]+-FAQ-[0-9]+$")
    question: str = Field(min_length=3, max_length=4000)


class FaqEvaluation(Model):
    schema_version: Literal[1]
    language: Literal["en"]
    cases: list[FaqCase] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self):
        if len({case.faq_id for case in self.cases}) != len(self.cases):
            raise ValueError("FAQ evaluation IDs must be unique")
        return self


def _counts(results: list[dict]) -> dict:
    ranks = [row["rank"] for row in results if row["rank"] is not None]
    return {
        "total": len(results),
        "first": ranks.count(1),
        "topFive": sum(rank <= 5 for rank in ranks),
        "retrieved": len(ranks),
        "errors": sum(row["error"] is not None for row in results),
    }


def evaluate_faq(
    corpus: Corpus, retriever: KnowledgeRetriever, suite: FaqEvaluation, limit: int = 12
) -> dict:
    sources = {}
    indexed_questions = defaultdict(set)
    for document in corpus.documents:
        for identity, question in faq_aliases(document).items():
            if identity in sources or document.source.snapshot_id != corpus.snapshot_id:
                raise ValueError("FAQ corpus contains duplicate or out-of-snapshot references")
            sources[identity] = document
            indexed_questions[document.source.service].add(question_key(question))
    prepared = []
    for case in suite.cases:
        document = sources.get(case.faq_id)
        if document is None:
            raise ValueError(f"Missing indexed source for FAQ evaluation case {case.faq_id}")
        service = document.source.service
        if question_key(case.question) in indexed_questions[service]:
            raise ValueError(f"FAQ evaluation case {case.faq_id} reuses indexed wording")
        prepared.append(
            (
                case,
                document,
                Query(question=case.question, service=service, snapshot_id=corpus.snapshot_id),
            )
        )

    results = []
    for case, source, query in prepared:
        rank, failure = None, None
        try:
            hits = retriever.search(query, limit)
            rank = next(
                (
                    i
                    for i, doc in enumerate(hits, 1)
                    if doc.id == source.id and doc.source == source.source
                ),
                None,
            )
        except DependencyUnavailableError:
            failure = "provider_unavailable"
        except EvidenceVerificationError:
            failure = "evidence_verification_failed"
        results.append(
            {
                "faqId": case.faq_id,
                "service": query.service,
                "sourcePath": source.source.path,
                "rank": rank,
                "error": failure,
            }
        )
    by_service = defaultdict(list)
    for result in results:
        by_service[result["service"]].append(result)
    return {
        "schemaVersion": 1,
        "snapshotId": corpus.snapshot_id,
        "retrievalMode": retriever.mode,
        "candidateLimit": limit,
        **_counts(results),
        "byService": {name: _counts(rows) for name, rows in sorted(by_service.items())},
        "cases": results,
    }
