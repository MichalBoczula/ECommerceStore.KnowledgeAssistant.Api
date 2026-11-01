from typing import Annotated, Literal

from pydantic import Field, JsonValue

from knowledge_assistant.domain.common.model import Model
from knowledge_assistant.domain.models import Source


class Query(Model):
    question: str = Field(min_length=3, max_length=4000)
    service: Literal["products", "users", "invoices", "payments"]
    snapshot_id: str = Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9._:-]+$")


class Rule(Model):
    field: str
    description: str
    source_ids: list[str]


class ExplanationSection(Model):
    type: Literal["explanation"]
    title: str
    content: str
    source_ids: list[str]


class ValidationSection(Model):
    type: Literal["validation"]
    title: str
    rules: list[Rule]
    source_ids: list[str]


class ContractSection(Model):
    type: Literal["api_contract"]
    title: str
    method: str
    path: str
    operation_id: str
    media_type: str
    request_schema: dict[str, JsonValue]
    example_request: JsonValue | None
    example_origin: Literal["openapi", "generated", "unavailable"]
    example_schema_valid: bool | None
    reference_schemas: dict[str, JsonValue]
    source_ids: list[str]


class ScenarioSection(Model):
    type: Literal["scenario"]
    title: str
    content: str
    execution_status: str | None = None
    source_ids: list[str]


Section = Annotated[
    ExplanationSection | ValidationSection | ContractSection | ScenarioSection,
    Field(discriminator="type"),
]


class Answer(Model):
    schema_version: Literal[1] = 1
    request_id: str
    snapshot_id: str
    service: str
    status: Literal["answered", "partial", "not_found"]
    answer_mode: Literal["evidence", "model"]
    retrieval_mode: Literal["local_keyword", "azure_hybrid"]
    is_demo: bool
    sections: list[Section]
    sources: list[Source]
    limitations: list[str]


class Snapshot(Model):
    id: str
    version: str
    is_demo: bool
    services: list[str]


class Catalog(Model):
    schema_version: Literal[1] = 1
    snapshots: list[Snapshot]
    answer_mode: Literal["evidence", "model"]
    retrieval_mode: Literal["local_keyword", "azure_hybrid"]


class Health(Model):
    status: str


class Readiness(Health):
    snapshot_id: str
