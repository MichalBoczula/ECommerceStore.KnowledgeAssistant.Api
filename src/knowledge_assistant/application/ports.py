from typing import Literal, Protocol

from knowledge_assistant.domain.models import Document

from .contracts import Query
from .planning import AnswerPlan


class KnowledgeRetriever(Protocol):
    mode: Literal["local_keyword", "azure_hybrid"]

    def search(self, query: Query, limit: int = 12) -> list[Document]: ...


class AnswerPlanner(Protocol):
    mode: Literal["evidence", "model"]

    def plan(self, question: str, documents: list[Document]) -> AnswerPlan: ...
