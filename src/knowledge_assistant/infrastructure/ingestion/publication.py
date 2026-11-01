"""Stage both files before publishing a complete import; reports bind corpus bytes by hash."""

import json
import os
import tempfile
from pathlib import Path

from knowledge_assistant.domain.models import Corpus

from .livedocs import digest
from .reporting import ingestion_report


def publish_import(
    corpus: Corpus,
    services: list[dict],
    output: Path,
    report_path: Path,
    manifest_commit: str | None = None,
) -> dict:
    if output.resolve() == report_path.resolve():
        raise ValueError("Corpus and report must use different output paths")
    data = (corpus.model_dump_json(by_alias=True, indent=2) + "\n").encode()
    report = ingestion_report(corpus, services, digest(data), len(data), manifest_commit)
    staged = []
    try:
        for path, content in (
            (output, data),
            (report_path, (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode()),
        ):
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temporary:
                staged.append((Path(temporary.name), path))
                temporary.write(content)
        for temporary, target in staged:
            os.replace(temporary, target)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return report
