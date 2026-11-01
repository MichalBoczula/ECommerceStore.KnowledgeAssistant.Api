import json

from knowledge_assistant.domain.models import Document
from knowledge_assistant.domain.text import tokens

from ..contracts import Rule
from ..planning import AnswerPlan, PlanSection


class EvidencePlanner:
    mode = "evidence"

    def plan(self, question: str, documents: list[Document]) -> AnswerPlan:
        sections = []
        wants_contract = bool(
            tokens(question) & {"contract", "json", "request", "schema", "payload"}
        )
        for doc in documents:
            if doc.kind == "validation":
                value = doc.payload["value"]
                entries = value.get("rules", [value]) if isinstance(value, dict) else [value]
                rules = [
                    Rule(
                        field=str(e.get("field", "Documented rule")),
                        description=str(e.get("description") or json.dumps(e, ensure_ascii=False)),
                        source_ids=[doc.source.id],
                    )
                    if isinstance(e, dict)
                    else Rule(
                        field="Documented rule",
                        description=json.dumps(e, ensure_ascii=False),
                        source_ids=[doc.source.id],
                    )
                    for e in entries
                ]
                sections.append(
                    PlanSection(
                        type="validation",
                        title="Validation",
                        text="",
                        rules=rules,
                        document_id=doc.id,
                        generated_example_json=None,
                        source_ids=[doc.source.id],
                    )
                )
        if wants_contract:
            contracts = [
                d for d in documents if d.kind == "api_contract" and d.payload.get("requestSchema")
            ]
            if contracts:
                doc = contracts[0]
                sections.append(
                    PlanSection(
                        type="api_contract",
                        title=doc.title,
                        text="",
                        rules=[],
                        document_id=doc.id,
                        generated_example_json=None,
                        source_ids=[doc.source.id],
                    )
                )
        if not sections:
            doc = documents[0]
            sections.append(
                PlanSection(
                    type="scenario" if doc.kind in {"scenario", "execution"} else "explanation",
                    title=doc.title,
                    text=doc.content,
                    rules=[],
                    document_id=doc.id,
                    generated_example_json=None,
                    source_ids=[doc.source.id],
                )
            )
        return AnswerPlan(
            status="answered",
            sections=sections,
            limitations=[
                "Evidence view: local extraction is active; model synthesis is not enabled."
            ],
        )
