from typing import Literal

from pydantic import Field, JsonValue

from .common.model import Model


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
