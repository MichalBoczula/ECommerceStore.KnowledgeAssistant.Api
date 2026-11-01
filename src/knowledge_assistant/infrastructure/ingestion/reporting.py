"""Deterministic source accounting; corpus bytes are not Azure index storage bytes."""

from collections import Counter

from knowledge_assistant.domain.models import Corpus, Document


def bundle_report(metadata: dict, reference: dict, documents: list[Document]) -> dict:
    scenarios = [d for d in documents if d.kind == "scenario"]
    executions = [d for d in documents if d.kind == "execution"]
    warnings = [w for d in documents for w in d.payload.get("ingestionWarnings", [])]
    return {
        "service": metadata["project"],
        "repository": metadata.get("repository"),
        "commitSha": metadata["commitSha"],
        "workflowRunId": metadata["workflowRunId"],
        "generatedAt": metadata["generatedAt"],
        "bundleSha256": reference["artifact"]["sha256"],
        "archiveBytes": reference["artifact"]["sizeBytes"],
        "filesVerified": len(metadata["files"]),
        "sourceFileSha256": {
            p: v["sha256"]
            for p, v in sorted(metadata["files"].items())
            if p.startswith(("sources/", "bdd/"))
        },
        "documentsByKind": dict(sorted(Counter(d.kind for d in documents).items())),
        "scenarioDefinitions": len(
            {(d.payload["featurePath"], d.payload.get("scenarioLine")) for d in scenarios}
        ),
        "compiledScenarios": len(scenarios),
        "executionResults": len(executions),
        "executionStatuses": dict(sorted(Counter(d.payload["status"] for d in executions).items())),
        "scenariosWithExecution": sum(
            bool(d.payload.get("executionDocumentIds")) for d in scenarios
        ),
        "unmatchedExecutions": sum(not d.payload.get("scenarioDocumentIds") for d in executions),
        "validationRules": sum(
            len(d.payload.get("normalizedRules", [])) for d in documents if d.kind == "validation"
        ),
        "operationLinks": sum(
            d.kind == "operation_links" and bool(d.operation_id) for d in documents
        ),
        "warnings": warnings,
    }


def ingestion_report(
    corpus: Corpus,
    bundles: list[dict],
    corpus_sha256: str,
    corpus_bytes: int,
    manifest_ref: str | None = None,
) -> dict:
    if len({d.id for d in corpus.documents}) != len(corpus.documents):
        raise ValueError("Ingestion produced duplicate document identities")
    return {
        "schemaVersion": 1,
        "snapshotId": corpus.snapshot_id,
        "version": corpus.version,
        "manifestSha256": corpus.manifest_sha256,
        "manifestCommit": manifest_ref,
        "corpusSha256": corpus_sha256,
        "corpusBytes": corpus_bytes,
        "textBytes": sum(len(d.content.encode()) for d in corpus.documents),
        "documents": len(corpus.documents),
        "documentsByKind": dict(sorted(Counter(d.kind for d in corpus.documents).items())),
        "services": bundles,
        "warnings": sum(len(b["warnings"]) for b in bundles),
        "note": (
            "Input integrity and import accounting do not prove "
            "business-rule execution or Azure index capacity."
        ),
    }
