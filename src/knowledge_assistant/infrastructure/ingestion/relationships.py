"""Resolve explicit source links; ambiguous/unmatched evidence is retained and reported."""

import json
from collections import defaultdict

from knowledge_assistant.domain.models import Document

from .structured import feature_candidates


def _value(value):
    if not isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    try:
        decoded = json.loads(value)
        return decoded if isinstance(decoded, str) else value
    except (ValueError, TypeError):
        return value


def relate(documents: list[Document]) -> None:
    scenarios = [d for d in documents if d.kind == "scenario"]
    executions = [d for d in documents if d.kind == "execution"]
    feature_paths = sorted({d.payload["featurePath"] for d in scenarios})
    contracts = {d.operation_id: d for d in documents if d.kind == "api_contract"}
    names = defaultdict(list)
    ids = defaultdict(list)
    for doc in scenarios:
        for name in {doc.title, doc.payload["scenarioName"]}:
            names[name].append(doc)
        for identity in doc.payload.get("scenarioIds", []):
            ids[identity].append(doc)
    for execution in executions:
        identities = execution.payload.get("scenarioIds", [])
        candidates = {d.id: d for identity in identities for d in ids.get(identity, [])}
        if not identities:
            candidates = {d.id: d for d in names.get(execution.title, [])}
        feature = next(
            (
                x.get("value")
                for x in execution.payload.get("labels", []) or []
                if x.get("name") == "feature"
            ),
            None,
        )
        if feature:
            candidates = {
                k: d for k, d in candidates.items() if d.payload["featureName"] == feature
            }
        ambiguous = len(candidates) > 1
        if candidates:
            parameters = {
                p["name"].casefold(): _value(p.get("value"))
                for p in execution.payload.get("parameters", []) or []
            }
            matching = {}
            for key, doc in candidates.items():
                values = {k.casefold(): v for k, v in doc.payload.get("exampleValues", {}).items()}
                shared = values.keys() & parameters.keys()
                if (shared and all(values[k] == parameters[k] for k in shared)) or (
                    not shared and not ambiguous
                ):
                    matching[key] = doc
            candidates = matching
        if len(candidates) == 1:
            scenario = next(iter(candidates.values()))
            execution.payload["scenarioDocumentIds"] = [scenario.id]
            scenario.payload.setdefault("executionDocumentIds", []).append(execution.id)
        else:
            execution.payload["scenarioDocumentIds"] = []
            execution.payload.setdefault("ingestionWarnings", []).append(
                {
                    "code": "ambiguous_execution" if ambiguous else "unmatched_execution",
                    "path": execution.source.path,
                }
            )
    aliases = {kind: defaultdict(list) for kind in ("validation", "flow")}
    for doc in documents:
        if doc.kind in aliases:
            for alias in doc.payload.get("aliases", []):
                aliases[doc.kind][alias].append(doc)
    for link in [d for d in documents if d.kind == "operation_links" and d.operation_id]:
        value = link.payload["value"]
        related = {}

        def warn(code, target, link=link):
            link.payload.setdefault("ingestionWarnings", []).append(
                {
                    "code": code,
                    "path": link.source.path,
                    "target": str(target),
                }
            )

        contract = contracts.get(link.operation_id)
        if contract:
            related[contract.id] = contract
        else:
            warn("unresolved_operation", link.operation_id)
        for kind, references in (
            ("validation", value.get("policies", [])),
            ("flow", [value["flow"]] if value.get("flow") else []),
        ):
            for reference in references:
                if isinstance(reference, dict):
                    reference = (
                        reference.get("id") or reference.get("source") or reference.get("name")
                    )
                targets = aliases[kind].get(reference, []) if isinstance(reference, str) else []
                if len(targets) == 1:
                    related.update({d.id: d for d in targets})
                else:
                    warn("unresolved_" + kind, reference)
        for reference in value.get("scenarios", []):
            targets = []
            if isinstance(reference, str):
                targets = list(ids.get(reference, []))
                granularity = "scenario"
                if not targets:
                    paths = feature_candidates(reference, feature_paths)
                    targets = [d for d in scenarios if d.payload["featurePath"] in paths]
                    if len(paths) > 1:
                        targets = []
                    granularity = "feature"
            elif isinstance(reference, dict):
                identity = (
                    reference.get("scenarioId") or reference.get("caseId") or reference.get("id")
                )
                targets = list(ids.get(identity, [])) if identity else []
                paths = feature_candidates(str(reference.get("feature", "")), feature_paths)
                if not identity:
                    targets = [
                        d
                        for d in scenarios
                        if d.payload["featurePath"] in paths
                        and (
                            d.payload["scenarioName"] == reference.get("scenario")
                            or d.title == reference.get("scenario")
                        )
                    ]
                if reference.get("feature"):
                    targets = [d for d in targets if d.payload["featurePath"] in paths]
                granularity = "scenario"
            else:
                granularity = "unknown"
            if targets:
                related.update({d.id: d for d in targets})
            else:
                warn(
                    "unresolved_scenario",
                    reference
                    if isinstance(reference, str)
                    else reference.get("scenarioId", reference.get("feature", "unknown"))
                    if isinstance(reference, dict)
                    else "invalid reference",
                )
            link.payload.setdefault("scenarioLinkGranularity", []).append(granularity)
        execution_ids = {
            x
            for d in related.values()
            if d.kind == "scenario"
            for x in d.payload.get("executionDocumentIds", [])
        }
        related.update({d.id: d for d in executions if d.id in execution_ids})
        link.payload["relatedDocumentIds"] = sorted(related)
        for doc in related.values():
            operation_ids = set(doc.payload.get("operationIds", [])) | {link.operation_id}
            doc.payload["operationIds"] = sorted(operation_ids)
            if doc.kind != "api_contract":
                doc.operation_id = next(iter(operation_ids)) if len(operation_ids) == 1 else None
    for doc in documents:
        if doc.kind == "validation":
            doc.payload["policyRole"] = (
                "referenced" if doc.payload.get("operationIds") else "published_only"
            )
        for name in ("executionDocumentIds", "scenarioDocumentIds"):
            if name in doc.payload:
                doc.payload[name] = sorted(doc.payload[name])
