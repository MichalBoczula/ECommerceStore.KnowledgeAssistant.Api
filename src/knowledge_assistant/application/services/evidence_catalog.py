"""Stable references to documented rules; models select rules instead of writing them."""

import hashlib
import json
import re
from dataclasses import dataclass

from knowledge_assistant.domain.models import Document
from knowledge_assistant.domain.text import FORMAT_TERMS, tokens

from ..contracts import Rule


@dataclass(frozen=True)
class Intent:
    validation: bool
    contract: bool


def intent(question: str) -> Intent:
    words = tokens(question)
    return Intent(
        bool(words & {"validation", "validate", "validations", "rules", "constraints"}),
        bool(words & {"contract", "contracts", "json", "request", "schema", "payload"}),
    )


def policy_rules(document: Document) -> dict[str, Rule]:
    if document.kind != "validation":
        return {}
    value = document.payload.get("value", {})
    entries = document.payload.get("normalizedRules")
    if entries is None:
        entries = value.get("rules", []) if isinstance(value, dict) else []
    result = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict) or not isinstance(entry.get("description"), str):
            continue
        field, description = str(entry.get("field", "Documented rule")), entry["description"]
        if not description.strip():
            continue
        identity = json.dumps([document.id, index, field, description], ensure_ascii=False)
        identity = "rule-" + hashlib.sha256(identity.encode()).hexdigest()
        result[identity] = Rule(
            field=field, description=description, source_ids=[document.source.id]
        )
    return result


def documented_example(payload: dict) -> bool:
    return bool(payload.get("hasDocumentedExample", payload.get("exampleRequest") is not None))


def select_policies(question: str, documents: list[Document], contract: Document | None):
    policies = [d for d in documents if d.kind == "validation" and policy_rules(d)]
    named = [d for d in policies if d.title.casefold() in question.casefold()]
    if named:
        return named
    if contract:
        linked = [d for d in policies if contract.operation_id in d.payload.get("operationIds", [])]
        if linked:
            return linked
    terms = tokens(question) - FORMAT_TERMS

    def score(doc):
        return len(
            terms
            & tokens(
                doc.title + " " + " ".join(rule.description for rule in policy_rules(doc).values())
            )
        )

    best = max((score(d) for d in policies), default=0)
    return [d for d in policies if best > 0 and score(d) == best]


def select_contract(question: str, documents: list[Document]) -> Document | None:
    """Extracted evidence view: select only an unambiguous documented operation."""
    candidates = [d for d in documents if d.kind == "api_contract"]
    words = tokens(question)
    exact = [
        d
        for d in candidates
        if d.operation_id
        and re.search(
            r"(?<![a-z0-9])" + re.escape(d.operation_id.casefold()) + r"(?![a-z0-9])",
            question.casefold(),
        )
    ]
    if exact:
        return exact[0] if len(exact) == 1 else None
    methods = set()
    for hints, verbs in (
        ({"create", "add", "new", "register", "post", "filter"}, {"POST"}),
        ({"update", "edit", "change", "put", "patch"}, {"PUT", "PATCH"}),
        ({"delete", "remove"}, {"DELETE"}),
        ({"get", "read", "list", "fetch"}, {"GET"}),
    ):
        if words & hints:
            methods.update(verbs)
    if methods:
        candidates = [d for d in candidates if d.payload.get("method") in methods]
    topical = (
        words
        - FORMAT_TERMS
        - {
            "create",
            "add",
            "new",
            "register",
            "post",
            "update",
            "edit",
            "change",
            "put",
            "patch",
            "delete",
            "remove",
            "get",
            "read",
            "list",
            "fetch",
        }
    )
    candidates = [
        d
        for d in candidates
        if topical & tokens(f"{d.title} {d.operation_id or ''} {d.payload.get('path', '')}")
    ]
    if len(candidates) <= 1:
        return next(iter(candidates), None)

    def score(document):
        text = f"{document.title} {document.operation_id or ''} {document.payload.get('path', '')}"
        split = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
        return len(tokens(split) & words)

    ranked = sorted(candidates, key=score, reverse=True)
    return ranked[0] if score(ranked[0]) > score(ranked[1]) else None
