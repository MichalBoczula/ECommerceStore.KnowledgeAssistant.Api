from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue
from pydantic.alias_generators import to_camel


class Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class Source(Model):
    id: str
    service: str
    snapshot_id: str
    path: str
    commit_sha: str
    bundle_sha256: str
    title: str
    url: str | None = None


class Document(Model):
    id: str
    source: Source
    kind: Literal["scenario", "execution", "api_contract", "validation", "flow", "operation_links"]
    title: str
    content: str
    payload: dict[str, JsonValue]
    operation_id: str | None = None
    tags: list[str] = Field(default_factory=list)


class Corpus(Model):
    schema_version: Literal[1] = 1
    snapshot_id: str
    version: str
    manifest_sha256: str
    is_demo: bool = False
    documents: list[Document]


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
