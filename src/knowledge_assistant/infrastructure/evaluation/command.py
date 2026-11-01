import hashlib

from knowledge_assistant.application.evaluation.models import EvaluationSuite
from knowledge_assistant.application.evaluation.runner import evaluate_suite, validate_inputs
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.domain.models import Corpus

from ..configuration.settings import Settings
from ..ingestion.json_source import loads, read
from ..planning.model_planner import ModelPlanner
from ..retrieval.azure_retriever import AzureRetriever
from ..retrieval.indexing_io import verified_corpus, write_report
from ..retrieval.local_retriever import LocalRetriever


def summary(report: dict) -> str:
    rows = [
        f"## Evaluation: {report['suiteId']}",
        "",
        f"{report['passed']}/{report['total']} passed; {report['failed']} failed.",
        "",
        f"Modes: `{report['retrievalMode']}` / `{report['answerMode']}`. "
        f"Synthetic data: `{str(report['isDemo']).lower()}`.",
        "",
        f"Snapshot: `{report['snapshotId']}`. Documents: {report['corpusDocuments']}.",
        "",
        "| Case | Result | Failed checks |",
        "| --- | --- | --- |",
    ]
    rows.extend(
        f"| {r['id']} | {'PASS' if r['passed'] else 'FAIL'} | {', '.join(r['failures']) or '—'} |"
        for r in report["cases"]
    )
    rows.extend(
        [
            "",
            "Deterministic evidence checks; semantic explanation quality requires review. "
            "Azure/model quality is measured only when those modes are explicitly selected.",
            "",
        ]
    )
    return "\n".join(rows)


def run_evaluation(args) -> dict:
    inputs = [args.corpus, args.suite]
    if args.ingestion_report:
        inputs.append(args.ingestion_report)
    outputs = [args.report] + ([args.summary] if args.summary else [])
    if len({p.resolve() for p in outputs}) != len(outputs) or {p.resolve() for p in inputs} & {
        p.resolve() for p in outputs
    }:
        raise ValueError("Evaluation outputs must not overwrite inputs or each other")
    suite = EvaluationSuite.model_validate(read(args.suite))
    if args.case_id:
        if set(args.case_id) - {c.id for c in suite.cases}:
            raise ValueError("Unknown evaluation case ID")
        suite = suite.model_copy(update={"cases": [c for c in suite.cases if c.id in args.case_id]})
    if args.ingestion_report:
        corpus = verified_corpus(args.corpus, args.ingestion_report, args.allow_demo)
    else:
        corpus = Corpus.model_validate(loads(args.corpus.read_bytes()))
        if not (args.allow_demo and corpus.is_demo):
            raise ValueError("Real evaluation requires the successful ingestion report")
    validate_inputs(suite, corpus)  # Reject stale pins before constructing cloud clients.
    settings = Settings(retrieval_mode=args.retrieval_mode, generation_mode=args.generation_mode)
    resources = []
    try:
        retriever = (
            AzureRetriever(settings, corpus)
            if args.retrieval_mode == "azure"
            else LocalRetriever(corpus)
        )
        resources.append(retriever)
        planner = ModelPlanner(settings) if args.generation_mode == "azure" else EvidencePlanner()
        resources.append(planner)
        report = evaluate_suite(suite, corpus, retriever, planner, settings.max_candidates)
        report.update(
            corpusFileSha256=hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
            suiteFileSha256=hashlib.sha256(args.suite.read_bytes()).hexdigest(),
            searchIndex=settings.search_index if args.retrieval_mode == "azure" else None,
            chatDeployment=settings.chat_deployment if args.generation_mode == "azure" else None,
        )
        write_report(args.report, report)
        if args.summary:
            args.summary.parent.mkdir(parents=True, exist_ok=True)
            args.summary.write_text(summary(report))
        return report
    finally:
        for resource in reversed(resources):
            if hasattr(resource, "close"):
                resource.close()
