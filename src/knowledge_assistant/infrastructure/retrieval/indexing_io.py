"""Verify Task 2 outputs before any cloud call and publish reports only on success."""

import json
import os
import tempfile
from pathlib import Path

from knowledge_assistant.domain.models import Corpus

from ..ingestion.json_source import loads, read
from ..ingestion.livedocs import digest


def verified_corpus(path: Path, report_path: Path, allow_demo: bool = False) -> Corpus:
    data = path.read_bytes()
    corpus = Corpus.model_validate(loads(data))
    report = read(report_path)
    if (
        not isinstance(report, dict)
        or report.get("corpusSha256") != digest(data)
        or report.get("corpusBytes") != len(data)
        or report.get("snapshotId") != corpus.snapshot_id
        or report.get("manifestSha256") != corpus.manifest_sha256
        or report.get("documents") != len(corpus.documents)
    ):
        raise ValueError("Corpus does not match the successful ingestion report")
    if corpus.is_demo and not allow_demo:
        raise ValueError("Synthetic corpus requires --allow-demo and an isolated test index")
    return corpus


def write_report(path: Path, report: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
