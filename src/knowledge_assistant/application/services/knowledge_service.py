from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.models import Corpus

from ..contracts import Answer, Catalog, Query, Snapshot
from ..errors import ResourceNotFoundError, SnapshotNotActiveError
from ..planning import AnswerPlan
from ..ports import AnswerPlanner, KnowledgeRetriever
from .answer_assembler import assemble


class KnowledgeService:
    def __init__(
        self,
        corpus: Corpus,
        retriever: KnowledgeRetriever,
        planner: AnswerPlanner,
        max_candidates: int = 12,
    ):
        self.corpus = corpus
        self.retriever = retriever
        self.planner = planner
        self.max_candidates = max_candidates

    def catalog(self) -> Catalog:
        return Catalog(
            snapshots=[
                Snapshot(
                    id=self.corpus.snapshot_id,
                    version=self.corpus.version,
                    is_demo=self.corpus.is_demo,
                    services=sorted({d.source.service for d in self.corpus.documents}),
                )
            ],
            answer_mode=self.planner.mode,
            retrieval_mode=self.retriever.mode,
        )

    def query(self, body: Query) -> Answer:
        if body.snapshot_id != self.corpus.snapshot_id:
            raise SnapshotNotActiveError()
        if body.service not in {d.source.service for d in self.corpus.documents}:
            raise ResourceNotFoundError()
        documents = self.retriever.search(body, self.max_candidates)
        if any(
            d.source.snapshot_id != body.snapshot_id or d.source.service != body.service
            for d in documents
        ):
            raise EvidenceVerificationError("Evidence escaped the requested scope")
        plan = (
            self.planner.plan(body.question, documents)
            if documents
            else AnswerPlan(
                status="not_found",
                sections=[],
                limitations=[
                    "No supporting evidence was found in the selected service and snapshot."
                ],
            )
        )
        return assemble(
            body, documents, plan, self.planner.mode, self.retriever.mode, self.corpus.is_demo
        )
