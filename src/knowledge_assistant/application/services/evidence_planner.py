from knowledge_assistant.domain.models import Document

from ..planning import AnswerPlan, PlanSection
from .evidence_catalog import intent, policy_rules, select_contract, select_policies


class EvidencePlanner:
    mode = "evidence"

    def plan(self, question: str, documents: list[Document]) -> AnswerPlan:
        sections = []
        requested = intent(question)
        contract = select_contract(question, documents) if requested.contract else None
        for doc in select_policies(question, documents, contract):
            if requested.validation:
                rules = policy_rules(doc)
                if rules:
                    sections.append(
                        PlanSection(
                            type="validation",
                            title="Validation",
                            text="",
                            rule_ids=list(rules)[:100],
                            document_id=doc.id,
                            generated_example_json=None,
                            source_ids=[doc.source.id],
                        )
                    )
        if requested.contract:
            doc = contract
            if doc is not None:
                sections.append(
                    PlanSection(
                        type="api_contract",
                        title=doc.title,
                        text="",
                        rule_ids=[],
                        document_id=doc.id,
                        generated_example_json=None,
                        source_ids=[doc.source.id],
                    )
                )
        flows = [
            d for d in documents if d.kind == "flow" and d.title.casefold() in question.casefold()
        ]
        for doc in flows:
            sections.append(
                PlanSection(
                    type="explanation",
                    title=doc.title,
                    text=doc.content,
                    rule_ids=[],
                    document_id=doc.id,
                    generated_example_json=None,
                    source_ids=[doc.source.id],
                )
            )
        if not sections and documents and not requested.contract:
            doc = documents[0]
            sections.append(
                PlanSection(
                    type="scenario" if doc.kind in {"scenario", "execution"} else "explanation",
                    title=doc.title,
                    text=doc.content,
                    rule_ids=[],
                    document_id=doc.id,
                    generated_example_json=None,
                    source_ids=[doc.source.id],
                )
            )
        return AnswerPlan(
            status="answered" if sections else "not_found",
            sections=sections[:12],
            limitations=["Evidence view: extracted documentation; model synthesis is not enabled."],
        )
