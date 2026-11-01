from typing import Literal

from pydantic import Field, model_validator

from knowledge_assistant.domain.common.model import Model


class SourcePin(Model):
    service: Literal["products", "users", "invoices", "payments"]
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    bundle_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ExpectedContract(Model):
    operation_id: str = Field(min_length=1)
    method: str = Field(pattern=r"^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)$")
    path: str = Field(min_length=1)
    require_example: bool = False
    model_example_required: bool = False


class Expectations(Model):
    statuses: list[Literal["answered", "partial", "not_found"]] = Field(min_length=1)
    section_types: list[Literal["validation", "api_contract", "explanation", "scenario"]] = []
    rule_texts: list[str] = []
    rules_exact: bool = False
    contracts: list[ExpectedContract] = []
    required_source_paths: list[str] = []
    allowed_source_paths: list[str] = []
    text_fragments: list[str] = []
    limitation_fragments: list[str] = []


class EvaluationCase(Model):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    question: str = Field(min_length=3, max_length=4000)
    service: Literal["products", "users", "invoices", "payments"]
    expect: Expectations


class EvaluationSuite(Model):
    schema_version: Literal[1] = 1
    id: str = Field(min_length=1, max_length=100)
    description: str
    is_demo: bool
    source_pins: list[SourcePin] = Field(min_length=1)
    cases: list[EvaluationCase] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_identities(self):
        if len({c.id for c in self.cases}) != len(self.cases):
            raise ValueError("Evaluation case IDs must be unique")
        if len({p.service for p in self.source_pins}) != len(self.source_pins):
            raise ValueError("Evaluation source pins must be unique per service")
        if not {c.service for c in self.cases} <= {p.service for p in self.source_pins}:
            raise ValueError("Every evaluated service needs a source pin")
        return self
