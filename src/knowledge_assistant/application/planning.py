from typing import Literal

from pydantic import Field

from knowledge_assistant.domain.common.model import Model


class PlanSection(Model):
    """A fixed model schema. Arbitrary API schemas are hydrated by the server."""

    type: Literal["explanation", "validation", "api_contract", "scenario"]
    title: str
    text: str
    rule_ids: list[str] = Field(max_length=100)
    document_id: str | None
    generated_example_json: str | None
    source_ids: list[str]


class AnswerPlan(Model):
    status: Literal["answered", "partial", "not_found"]
    sections: list[PlanSection] = Field(max_length=12)
    limitations: list[str]
