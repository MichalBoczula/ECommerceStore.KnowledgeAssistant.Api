import argparse
import json
import re
from pathlib import Path
from zipfile import BadZipFile

from azure.core.exceptions import AzureError
from openai import OpenAIError

from .application.contracts import Answer
from .infrastructure.clients.embeddings import EmbeddingCache
from .infrastructure.configuration.settings import Settings
from .infrastructure.ingestion.json_source import read
from .infrastructure.ingestion.livedocs import ingest_manifest
from .infrastructure.ingestion.publication import publish_import
from .infrastructure.retrieval.azure_indexer import AzureIndexer
from .infrastructure.retrieval.index_plan import plan_index
from .infrastructure.retrieval.indexing_io import verified_corpus, write_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Versioned LiveDocs ingestion and indexing")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--projects", type=Path, required=True)
    ingest.add_argument("--manifest-commit", help="Exact LiveDocs Git commit for the two manifests")
    ingest.add_argument("--cache", type=Path, default=Path(".cache/livedocs"))
    ingest.add_argument("--version")
    ingest.add_argument("--download", action="store_true")
    ingest.add_argument("--output", type=Path, default=Path("artifacts/corpus.json"))
    ingest.add_argument("--report", type=Path, default=Path("artifacts/ingestion-report.json"))
    ingest.add_argument(
        "--strict-links", action="store_true", help="Fail on reconciliation warnings"
    )
    ingest.add_argument("--livedocs-base-url", default="")
    for name in ("plan-index", "index"):
        command = commands.add_parser(name)
        command.add_argument("--corpus", type=Path, required=True)
        command.add_argument("--ingestion-report", type=Path, required=True)
        command.add_argument("--cache", type=Path, default=Path(".cache/embeddings"))
        command.add_argument("--report", type=Path, default=Path(f"artifacts/{name}-report.json"))
        command.add_argument("--allow-demo", action="store_true")
    commands.add_parser("export-contract")
    args = parser.parse_args()
    if args.command == "export-contract":
        output = Path("contracts/answer.schema.json")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(Answer.model_json_schema(by_alias=True), indent=2) + "\n")
        from .api.app import create_app

        Path("contracts/openapi.json").write_text(
            json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
        )
        return
    if args.command == "ingest":
        if args.manifest_commit and not re.fullmatch(r"[0-9a-f]{40}", args.manifest_commit):
            parser.error("--manifest-commit must be a full lowercase Git SHA")
        inputs = {args.manifest.resolve(), args.projects.resolve()}
        outputs = {args.output.resolve(), args.report.resolve()}
        if len(outputs) != 2 or inputs & outputs:
            parser.error("Corpus, report and input manifests must use different paths")
        try:
            services = []
            corpus = ingest_manifest(
                read(args.manifest),
                args.cache,
                args.version,
                args.download,
                args.livedocs_base_url,
                registry=read(args.projects),
                reports=services,
            )
            if args.strict_links and any(s["warnings"] for s in services):
                raise ValueError(
                    "Strict reconciliation failed; corpus and report were not published"
                )
            report = publish_import(
                corpus, services, args.output, args.report, args.manifest_commit
            )
        except (ValueError, OSError, KeyError, TypeError, BadZipFile) as error:
            # Avoid tracebacks containing source data, private paths or credentials.
            if type(error) is ValueError:
                parser.exit(1, f"Ingestion failed: {error}\n")
            parser.exit(1, "Ingestion failed: unreadable or invalid documentation input\n")
        print(
            json.dumps(
                {
                    k: report[k]
                    for k in (
                        "snapshotId",
                        "documents",
                        "documentsByKind",
                        "corpusBytes",
                        "warnings",
                    )
                },
                indent=2,
            )
        )
        return
    if args.report.resolve() in {args.corpus.resolve(), args.ingestion_report.resolve()}:
        parser.error("Index report must not overwrite corpus or ingestion report")
    indexer = None
    try:
        settings = Settings()
        settings.require_azure()
        corpus = verified_corpus(args.corpus, args.ingestion_report, args.allow_demo)
        plan = plan_index(corpus, settings)
        if args.command == "plan-index":
            cache = EmbeddingCache(args.cache, plan.profile, settings.embedding_dimensions)
            inputs = {r["content"] for r in plan.rows}
            cached = sum(cache.read(text) is not None for text in inputs)
            result = {
                **plan.summary(settings.embedding_dimensions),
                "cachedEmbeddingInputs": cached,
                "newEmbeddingInputs": len(inputs) - cached,
                "embeddingCallsMade": 0,
                "searchIndex": settings.search_index,
                "uploadComplete": False,
            }
        else:
            indexer = AzureIndexer(settings)
            result = indexer.upload(plan, args.cache)
        write_report(args.report, result)
        print(json.dumps(result, indent=2))
        if args.command == "index":
            print("Index upload complete. Deploy the matching corpus separately to activate it.")
    except (ValueError, OSError, AzureError, OpenAIError) as error:
        message = (
            str(error)
            if type(error) is ValueError
            else (
                "Check corpus/report integrity, Azure configuration, "
                "permissions and provider availability."
            )
        )
        parser.exit(1, f"Indexing failed: {message}\n")
    finally:
        if indexer:
            indexer.close()


if __name__ == "__main__":
    main()
