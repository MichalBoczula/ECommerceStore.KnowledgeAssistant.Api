"""Adapters for the actual .NET descriptor and Python source-catalog formats."""

from dataclasses import dataclass
from typing import Any


@dataclass
class Entry:
    pointer: str
    identity: str
    title: str
    value: dict
    aliases: set[str]


def pointer_part(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def entries(value: Any, kind: str) -> list[Entry]:
    wrappers = {
        "validation": ("validations", "policies"),
        "flow": ("flows",),
        "operation_links": ("operations",),
    }
    prefix = ""
    if isinstance(value, dict):
        for wrapper in wrappers[kind]:
            if wrapper in value:
                value, prefix = value[wrapper], "/" + wrapper
                break
    if isinstance(value, list):
        items = [(f"{prefix}/{i}", None, v) for i, v in enumerate(value)]
    elif isinstance(value, dict):
        if single_record(value, kind):
            items = [(prefix, None, value)]
        else:
            items = [(f"{prefix}/{pointer_part(k)}", k, v) for k, v in value.items()]
    else:
        raise ValueError(f"Unsupported {kind} source collection")
    result = []
    expanded = []
    for pointer, outer_name, item in items:
        if isinstance(item, dict) and item and not single_record(item, kind):
            if not all(isinstance(child, dict) for child in item.values()):
                raise ValueError(f"Unsupported {kind} named descriptor map")
            expanded.extend(
                (pointer + "/" + pointer_part(key), key, child) for key, child in item.items()
            )
        else:
            expanded.append((pointer, outer_name, item))
    for pointer, outer_name, item in expanded:
        if kind == "operation_links" and outer_name and isinstance(item, list):
            item = {"operationId": outer_name, "scenarios": item}
        if not isinstance(item, dict):
            raise ValueError(f"Unsupported {kind} source entry")
        keys = (
            "id",
            "policyId",
            "flowId",
            "operationId",
            "policyName",
            "flowName",
            "actionName",
            "name",
            "title",
            "source",
        )
        aliases = {str(item[k]) for k in keys if isinstance(item.get(k), str) and item[k]}
        if outer_name:
            aliases.add(outer_name)
        preferred = next((item[k] for k in keys if isinstance(item.get(k), str) and item[k]), None)
        identity = str(preferred or outer_name or pointer or kind)
        title = str(
            item.get("policyName")
            or item.get("flowName")
            or item.get("actionName")
            or item.get("name")
            or item.get("title")
            or outer_name
            or preferred
            or kind
        )
        result.append(Entry(pointer, identity, title, item, aliases | {identity}))
    return result


def single_record(value: dict, kind: str) -> bool:
    return bool(
        value.keys()
        & {
            "validation": {"policyName", "policyId", "rules", "source", "description", "name"},
            "flow": {"flowName", "flowId", "actionName", "steps", "source", "name"},
            "operation_links": {"operationId", "scenarios", "method", "path"},
        }[kind]
    )


def validation_rules(policy: dict) -> list[dict]:
    """Flatten documented messages/conditions; never infer rules from field names."""
    result = []

    def visit(node: Any, group: dict | None = None):
        if isinstance(node, list):
            for child in node:
                visit(child, group)
            return
        if not isinstance(node, dict):
            raise ValueError("Unsupported validation rule entry")
        if isinstance(node.get("rules"), list):
            for child in node["rules"]:
                visit(child, node)
            return
        message = node.get("message") or node.get("description")
        conditions = node.get("conditions", [])
        if message is None and not conditions:
            return
        parent = group or {}
        rule_name = node.get("ruleName") or node.get("name") or parent.get("ruleName")
        entity = node.get("entity") or parent.get("entity")
        result.append(
            {
                "field": str(node.get("field") or entity or rule_name or "Documented rule"),
                "description": str(message or "; ".join(map(str, conditions))),
                "ruleName": rule_name,
                "ruleId": node.get("id") or node.get("ruleId") or parent.get("id"),
                "entity": entity,
                "conditions": conditions,
                "source": node.get("source") or policy.get("source"),
            }
        )

    visit(policy)
    return result


def feature_candidates(reference: str, paths: list[str]) -> list[str]:
    normalized = reference.replace("\\", "/").removeprefix("./")
    for marker in ("/Features/", "/features/"):
        if marker in normalized:
            normalized = normalized.split(marker, 1)[1]
    for prefix in ("Features/", "features/", "bdd/"):
        normalized = normalized.removeprefix(prefix)
    return [p for p in paths if p.removeprefix("bdd/") == normalized]
