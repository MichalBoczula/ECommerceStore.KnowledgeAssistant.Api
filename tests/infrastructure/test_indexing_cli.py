import json
import sys

import pytest

from knowledge_assistant.cli import main
from knowledge_assistant.infrastructure.ingestion.livedocs import digest
from knowledge_assistant.infrastructure.retrieval.indexing_io import verified_corpus


def inputs(tmp_path, demo):
    corpus = demo.corpus.model_copy(deep=True)
    corpus.is_demo = False
    path, report = tmp_path / "corpus.json", tmp_path / "ingestion-report.json"
    path.write_text(corpus.model_dump_json(by_alias=True))
    report.write_text(
        json.dumps(
            {
                "snapshotId": corpus.snapshot_id,
                "manifestSha256": corpus.manifest_sha256,
                "documents": len(corpus.documents),
                "corpusSha256": digest(path.read_bytes()),
                "corpusBytes": path.stat().st_size,
            }
        )
    )
    return path, report, corpus


def test_offline_plan_makes_no_cloud_calls(tmp_path, demo, cloud, monkeypatch, capsys):
    path, ingestion_report, _ = inputs(tmp_path, demo)
    output = tmp_path / "plan.json"
    monkeypatch.setattr("knowledge_assistant.cli.Settings", lambda: cloud.settings)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline planning must not construct cloud clients")

    monkeypatch.setattr("knowledge_assistant.cli.AzureIndexer", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge",
            "plan-index",
            "--corpus",
            str(path),
            "--ingestion-report",
            str(ingestion_report),
            "--report",
            str(output),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )
    main()
    plan = json.loads(output.read_text())
    assert plan["embeddingCallsMade"] == 0 and not plan["uploadComplete"]
    assert plan["indexedChunks"] >= plan["sourceDocuments"]
    assert plan["newEmbeddingInputs"] > 0
    assert "rawFloat32VectorBytes" in capsys.readouterr().out


@pytest.mark.parametrize("change", ["bytes", "snapshot", "manifest", "count"])
def test_unreviewed_or_changed_corpus_cannot_be_indexed(tmp_path, demo, change):
    path, report, _ = inputs(tmp_path, demo)
    if change == "bytes":
        path.write_text(path.read_text() + " ")
    else:
        value = json.loads(report.read_text())
        key = {"snapshot": "snapshotId", "manifest": "manifestSha256", "count": "documents"}[change]
        value[key] = "different" if change != "count" else 0
        report.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="does not match"):
        verified_corpus(path, report)


def test_failed_indexing_keeps_last_report_and_closes_clients(tmp_path, demo, cloud, monkeypatch):
    path, ingestion_report, _ = inputs(tmp_path, demo)
    output = tmp_path / "index-report.json"
    output.write_text("last-successful-report")
    closed = []

    class FailingIndexer:
        def __init__(self, settings):
            pass

        def upload(self, plan, cache):
            raise ValueError("Index upload was incomplete")

        def close(self):
            closed.append(True)

    monkeypatch.setattr("knowledge_assistant.cli.Settings", lambda: cloud.settings)
    monkeypatch.setattr("knowledge_assistant.cli.AzureIndexer", FailingIndexer)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge",
            "index",
            "--corpus",
            str(path),
            "--ingestion-report",
            str(ingestion_report),
            "--report",
            str(output),
        ],
    )
    with pytest.raises(SystemExit) as failure:
        main()
    assert failure.value.code == 1
    assert output.read_text() == "last-successful-report" and closed == [True]


def test_indexing_demo_data_requires_an_explicit_opt_in(tmp_path, demo):
    path, report, corpus = inputs(tmp_path, demo)
    corpus.is_demo = True
    path.write_text(corpus.model_dump_json(by_alias=True))
    value = json.loads(report.read_text())
    value.update(corpusSha256=digest(path.read_bytes()), corpusBytes=path.stat().st_size)
    report.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="allow-demo"):
        verified_corpus(path, report)
    assert verified_corpus(path, report, allow_demo=True).is_demo
