import argparse
import json
from collections import Counter
from pathlib import Path

from .application.contracts import Answer
from .domain.models import Corpus
from .infrastructure.configuration.settings import Settings
from .infrastructure.ingestion.livedocs import ingest_manifest
from .infrastructure.retrieval.azure_retriever import AzureRetriever


def main() -> None:
    parser = argparse.ArgumentParser(description="Versioned LiveDocs ingestion and indexing")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest")
    ingest.add_argument("--manifest", type=Path, required=True)
    ingest.add_argument("--cache", type=Path, default=Path(".cache/livedocs"))
    ingest.add_argument("--version")
    ingest.add_argument("--download", action="store_true")
    ingest.add_argument("--output", type=Path, default=Path("artifacts/corpus.json"))
    ingest.add_argument("--livedocs-base-url", default="")
    index = commands.add_parser("index")
    index.add_argument("--corpus", type=Path, required=True)
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
        corpus = ingest_manifest(
            json.loads(args.manifest.read_text()),
            args.cache,
            args.version,
            args.download,
            args.livedocs_base_url,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(".partial")
        data = corpus.model_dump_json(by_alias=True, indent=2)
        temporary.write_text(data)
        temporary.replace(args.output)
        print(
            json.dumps(
                {
                    "snapshotId": corpus.snapshot_id,
                    "documents": len(corpus.documents),
                    "kinds": dict(Counter(d.kind for d in corpus.documents)),
                    "corpusBytes": len(data.encode()),
                    "textBytes": sum(len(d.content.encode()) for d in corpus.documents),
                    "note": "Source bytes are not Azure index storage usage.",
                },
                indent=2,
            )
        )
        return
    corpus = Corpus.model_validate_json(args.corpus.read_text())
    indexer = AzureRetriever(Settings())
    try:
        result = indexer.upload(corpus)
        print(json.dumps({"snapshotId": corpus.snapshot_id, **result}, indent=2))
        print("Activate this corpus in KA_CORPUS_PATH only after all uploads succeed.")
    finally:
        indexer.close()


if __name__ == "__main__":
    main()
