from typing import Literal

from knowledge_assistant.domain.common.model import Model

from .contracts import Rule


class PlanSection(Model):
    """A fixed model schema. Arbitrary API schemas are hydrated by the server."""

    type: Literal["explanation", "validation", "api_contract", "scenario"]
    title: str
    text: str
    rules: list[Rule]
    document_id: str | None
    generated_example_json: str | None
    source_ids: list[str]


class AnswerPlan(Model):
    status: Literal["answered", "partial", "not_found"]
    sections: list[PlanSection]
    limitations: list[str]
