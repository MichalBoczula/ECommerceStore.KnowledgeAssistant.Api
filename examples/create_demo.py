"""Generate synthetic documentation for local UI development; never production evidence."""

import json
from pathlib import Path

from knowledge_assistant.ingestion import documents_from_bundle
from knowledge_assistant.models import Corpus

schema = {
    "type": "object",
    "required": ["name", "price", "specifications"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "price": {"type": "number", "exclusiveMinimum": 0},
        "specifications": {
            "type": "object",
            "required": ["storageGb", "color"],
            "properties": {
                "storageGb": {"type": "integer", "minimum": 1},
                "color": {"type": "string"},
            },
        },
        "photos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "main": {"type": "boolean"}},
            },
        },
    },
}
example = {
    "name": "Example phone",
    "price": 1299.99,
    "specifications": {"storageGb": 256, "color": "Black"},
    "photos": [{"url": "https://example.com/phone.webp", "main": True}],
}
spec = {
    "openapi": "3.1.0",
    "info": {"title": "Synthetic Products API", "version": "demo"},
    "paths": {
        "/mobile-phones": {
            "post": {
                "operationId": "createMobilePhone",
                "summary": "Create a mobile phone",
                "requestBody": {
                    "content": {"application/json": {"schema": schema, "example": example}}
                },
                "responses": {"201": {"description": "Created"}},
            }
        }
    },
}
rules = {
    "name": "Mobile phone validation",
    "rules": [
        {"field": "name", "description": "Required, nonempty text."},
        {"field": "price", "description": "A number greater than zero."},
        {"field": "specifications", "description": "Storage size and color are required."},
    ],
}
files = {
    "sources/openapi.json": json.dumps(spec).encode(),
    "sources/validation-policies.json": json.dumps([rules]).encode(),
    "sources/flows.json": json.dumps(
        [
            {
                "name": "Create mobile phone",
                "steps": ["Validate request", "Persist mobile phone", "Return created resource"],
            }
        ]
    ).encode(),
    "sources/operation-links.json": json.dumps({"createMobilePhone": ["DEMO-MOBILE-001"]}).encode(),
    "bdd/mobile.feature": b"""@DEMO-MOBILE-001
Feature: Mobile phone validation
  Scenario Outline: Reject invalid mobile phone price
    Given a mobile phone with price <price>
    When I submit the create request
    Then validation fails
    Examples:
      | price |
      | 0 |
      | -1 |
""",
    "allure-results/demo-result.json": json.dumps(
        {
            "uuid": "demo-result",
            "name": "Reject invalid mobile phone price",
            "status": "passed",
            "steps": [{"name": "Then validation fails", "status": "passed"}],
        }
    ).encode(),
}
metadata = {"project": "products", "commitSha": "0" * 40}
corpus = Corpus(
    snapshot_id="demo-v1",
    version="demo",
    manifest_sha256="0" * 64,
    is_demo=True,
    documents=documents_from_bundle(metadata, files, "demo-v1", "0" * 64, "demo"),
)
if __name__ == "__main__":
    Path(__file__).with_name("demo-corpus.json").write_text(
        corpus.model_dump_json(by_alias=True, indent=2) + "\n"
    )
