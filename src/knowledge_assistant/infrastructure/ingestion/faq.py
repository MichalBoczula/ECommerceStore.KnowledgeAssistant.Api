"""Attach reviewed questions to existing, same-service source documents."""

from collections import defaultdict
from typing import Literal

from pydantic import Field, model_validator

from knowledge_assistant.domain.common.model import Model
from knowledge_assistant.domain.faq import question_key
from knowledge_assistant.domain.models import Corpus, Document

Service = Literal["products", "users", "invoices", "payments"]


class FaqTarget(Model):
    kind: Literal["api_contract", "validation", "scenario"]
    reference: str = Field(min_length=1)


class FaqEntry(Model):
    id: str = Field(pattern=r"^[A-Z]+-FAQ-[0-9]+$")
    service: Service
    question: str = Field(min_length=3, max_length=4000)
    target: FaqTarget


class FaqCatalog(Model):
    schema_version: Literal[1]
    language: Literal["en"]
    entries: list[FaqEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_entries(self):
        if len({entry.id for entry in self.entries}) != len(self.entries):
            raise ValueError("FAQ IDs must be unique")
        keys = {(entry.service, question_key(entry.question)) for entry in self.entries}
        if len(keys) != len(self.entries):
            raise ValueError("FAQ questions must be unique per service")
        return self


def _references(document: Document) -> set[str]:
    if document.kind == "api_contract":
        return {document.operation_id} if document.operation_id else set()
    if document.kind == "validation":
        return {document.title, *document.payload.get("aliases", [])}
    if document.kind == "scenario":
        return set(document.payload.get("scenarioIds", []))
    return set()


def attach_faq(corpus: Corpus, catalog: FaqCatalog) -> None:
    by_target = defaultdict(list)
    for document in corpus.documents:
        if document.source.snapshot_id == corpus.snapshot_id:
            for reference in _references(document):
                by_target[document.source.service, document.kind, reference].append(document)
    attached = defaultdict(dict)
    services = {document.source.service for document in corpus.documents}
    for entry in catalog.entries:
        if entry.service not in services:
            continue
        candidates = by_target[entry.service, entry.target.kind, entry.target.reference]
        if len(candidates) != 1:
            raise ValueError(f"FAQ {entry.id} needs exactly one matching LiveDocs source")
        attached[candidates[0].id][entry.id] = entry.question
    for document in corpus.documents:
        if document.id in attached:
            document.payload["faqAliases"] = dict(sorted(attached[document.id].items()))
