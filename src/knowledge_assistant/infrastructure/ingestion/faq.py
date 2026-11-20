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


class AllureExample(Model):
    scenario_id: str = Field(min_length=1)
    coverage: Literal["direct", "related"] = "direct"


class FaqEntry(Model):
    id: str = Field(pattern=r"^[A-Z]+-FAQ-[0-9]+$")
    service: Service
    question: str = Field(min_length=3, max_length=4000)
    target: FaqTarget
    allure_examples: list[AllureExample] = Field(default_factory=list)


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
    if document.kind in {"scenario", "execution"}:
        return set(document.payload.get("scenarioIds", []))
    return set()


def attach_faq(corpus: Corpus, catalog: FaqCatalog) -> None:
    by_target = defaultdict(list)
    by_id = {}
    for document in corpus.documents:
        if document.source.snapshot_id == corpus.snapshot_id:
            by_id[document.id] = document
            for reference in _references(document):
                by_target[document.source.service, document.kind, reference].append(document)
    attached = defaultdict(dict)
    examples = defaultdict(dict)
    services = {document.source.service for document in by_id.values()}
    for entry in catalog.entries:
        if entry.service not in services:
            continue
        candidates = by_target[entry.service, entry.target.kind, entry.target.reference]
        if len(candidates) != 1:
            raise ValueError(f"FAQ {entry.id} needs exactly one matching LiveDocs source")
        target_id = candidates[0].id
        attached[target_id][entry.id] = entry.question
        if entry.allure_examples:
            examples[target_id][entry.id] = [
                _match_example(entry, example, by_target, by_id)
                for example in entry.allure_examples
            ]
    for document in corpus.documents:
        if document.id in attached:
            document.payload["faqAliases"] = dict(sorted(attached[document.id].items()))
        if document.id in examples:
            document.payload["faqAllureExamples"] = dict(sorted(examples[document.id].items()))


def _match_example(entry: FaqEntry, example: AllureExample, by_target, by_id) -> dict:
    scenarios = by_target[entry.service, "scenario", example.scenario_id]
    if not scenarios:
        raise ValueError(f"FAQ {entry.id} has an unknown Allure scenario ID: {example.scenario_id}")
    executed = {d.id: d for d in by_target[entry.service, "execution", example.scenario_id]}
    for scenario in scenarios:
        for identity in scenario.payload.get("executionDocumentIds", []):
            result = by_id.get(identity)
            if result and result.source.service == entry.service and result.kind == "execution":
                executed[identity] = result
    passed = sorted(
        (d for d in executed.values() if d.payload["status"] == "passed"),
        key=lambda d: d.source.path,
    )
    return {
        "scenarioId": example.scenario_id,
        "coverage": example.coverage,
        "executionDocumentId": passed[0].id if passed else None,
    }
