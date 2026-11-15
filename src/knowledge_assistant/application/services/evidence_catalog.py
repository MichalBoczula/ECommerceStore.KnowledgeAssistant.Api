"""Stable references to documented rules; models select rules instead of writing them."""

import hashlib
import json
import re
from dataclasses import dataclass

from knowledge_assistant.domain.api_operations import operation_actions
from knowledge_assistant.domain.models import Document
from knowledge_assistant.domain.text import FORMAT_TERMS, tokens

from ..contracts import Rule

INVENTORY_PREFIX = r"(?:show (?:me )?|list )?(?:the )?"
RESOURCE_ENDPOINTS = re.compile(
    INVENTORY_PREFIX + r"(?P<resource>[\w-]+(?: [\w-]+)*) endpoints?[?.!]?$",
    re.IGNORECASE,
)
ENDPOINT_MATRIX = re.compile(
    INVENTORY_PREFIX + r"(?:all (?:api )?endpoints|(?:api )?endpoint matrix)[?.!]?$",
    re.IGNORECASE,
)
ACTION_QUESTION = re.compile(
    r"(?:(?:how (?:to|do i|can i)|(?:which|what) (?:api )?endpoint (?:do i use )?to) )?"
    r"(?P<verb>[a-z]+) (?P<target>.+)",
    re.IGNORECASE,
)
MODEL_QUESTION_TERMS = {
    "example",
    "examples",
    "flow",
    "flows",
    "json",
    "sample",
    "scenario",
    "scenarios",
    "test",
    "tests",
    "why",
}


@dataclass(frozen=True)
class Intent:
    validation: bool
    contract: bool


def intent(question: str) -> Intent:
    words = tokens(question)
    return Intent(
        bool(words & {"validation", "validate", "validations", "rules", "constraints"}),
        bool(
            not requests_endpoint_matrix(question)
            and words
            & {
                "contract",
                "contracts",
                "endpoint",
                "endpoints",
                "json",
                "request",
                "schema",
                "payload",
            }
        ),
    )


def requests_endpoint_matrix(question: str) -> bool:
    return ENDPOINT_MATRIX.fullmatch(" ".join(question.split())) is not None


def _route_order(doc: Document) -> tuple[str, str]:
    return doc.payload["path"], doc.payload["method"]


def _resource_terms(terms: set[str]) -> set[str]:
    return {
        term[:-1]
        if len(term) > 3 and term.endswith("s") and not term.endswith(("ss", "us", "is"))
        else term
        for term in terms
    }


def _action_contracts(question: str, candidates: list[Document]) -> list[Document]:
    match = ACTION_QUESTION.fullmatch(question)
    if not match:
        return []
    verb = match["verb"].casefold()
    terms = _resource_terms(
        tokens(match["target"])
        - FORMAT_TERMS
        - {"all", "api", "by", "endpoint", "endpoints", "method", "operation", "route"}
    )
    if not terms:
        return []
    matches = []
    for doc in candidates:
        path, method = _route_order(doc)
        responsibility = doc.payload.get("responsibility", doc.title)
        if verb in operation_actions(method, doc.operation_id or "", responsibility) and (
            terms <= _resource_terms(tokens(f"{path} {doc.operation_id or ''} {responsibility}"))
        ):
            matches.append(doc)
    return sorted(matches, key=_route_order)


def _named_contracts(question: str, candidates: list[Document]) -> list[Document]:
    question = question.casefold()
    return [
        doc
        for doc in candidates
        if doc.operation_id
        and re.search(
            r"(?<![a-z0-9])" + re.escape(doc.operation_id.casefold()) + r"(?![a-z0-9])", question
        )
    ]


def lookup_contracts(question: str, documents: list[Document]) -> list[Document]:
    """Find scoped OpenAPI operations for a list, an action or an exact reference."""
    candidates = [doc for doc in documents if doc.kind == "api_contract"]
    question = " ".join(question.split())
    if requests_endpoint_matrix(question):
        return sorted(candidates, key=_route_order)
    if match := RESOURCE_ENDPOINTS.fullmatch(question):
        resource = match["resource"].casefold().replace(" ", "-")
        return sorted(
            (
                doc
                for doc in candidates
                if doc.payload["path"].strip("/").partition("/")[0].casefold()
                in {resource, resource + "s"}
            ),
            key=_route_order,
        )

    if tokens(question) & MODEL_QUESTION_TERMS:
        return []
    requested = intent(question)
    if not (requested.contract and requested.validation):
        return _action_contracts(question, candidates)
    matches = {doc.id: doc for doc in _named_contracts(question, candidates)}
    for doc in candidates:
        method_path = f"{doc.payload['method']} {doc.payload['path']}"
        if re.search(
            r"(?<![\w/])" + re.escape(method_path) + r"(?=$|[\s`\"',?!;)]|\.(?=\s|$))",
            question,
            flags=re.IGNORECASE,
        ):
            matches[doc.id] = doc
    if matches:
        return list(matches.values()) if len(matches) == 1 else []
    return _action_contracts(question, candidates)


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
    exact = _named_contracts(question, candidates)
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
