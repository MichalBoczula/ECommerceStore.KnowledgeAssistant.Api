import json
import sys

import pytest

from knowledge_assistant.cli import main
from knowledge_assistant.infrastructure.ingestion.json_source import read
from knowledge_assistant.infrastructure.ingestion.livedocs import digest


def command(tmp_path, bundle_factory, monkeypatch, *extra):
    path, reference, _ = bundle_factory()
    cache = tmp_path / "cache"
    cache.mkdir(exist_ok=True)
    (cache / (reference["artifact"]["sha256"] + ".zip")).write_bytes(path.read_bytes())
    manifest = tmp_path / "portal.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "latestVersion": "v1",
                "versions": [
                    {"version": "v1", "state": "development", "projects": [reference]},
                ],
            }
        )
    )
    registry = tmp_path / "projects.json"
    registry.write_text(json.dumps({"products": {"repository": "MichalBoczula/ProductsCatalog"}}))
    output, report = tmp_path / "corpus.json", tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "knowledge",
            "ingest",
            "--manifest",
            str(manifest),
            "--projects",
            str(registry),
            "--cache",
            str(cache),
            "--output",
            str(output),
            "--report",
            str(report),
            "--manifest-commit",
            "a" * 40,
            *extra,
        ],
    )
    return output, report, manifest, reference, cache


def test_cli_report_accounts_for_sources_and_binds_exact_output_bytes(
    tmp_path, bundle_factory, monkeypatch, capsys
):
    output, report, _, reference, _ = command(tmp_path, bundle_factory, monkeypatch)
    main()
    first = output.read_bytes(), report.read_bytes()
    audit = read(report)
    (service,) = audit["services"]
    assert audit["corpusSha256"] == digest(output.read_bytes())
    assert audit["corpusBytes"] == output.stat().st_size
    assert audit["manifestCommit"] == "a" * 40
    assert audit["documents"] == sum(audit["documentsByKind"].values())
    assert service["bundleSha256"] == reference["artifact"]["sha256"]
    assert service["compiledScenarios"] == 2 and service["executionResults"] == 1
    assert service["validationRules"] == 3
    assert audit["warnings"] >= 1  # The synthetic result omits outline parameters.
    main()
    assert first == (output.read_bytes(), report.read_bytes())
    assert "snapshotId" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["missing_bundle", "bad_bundle", "strict_links", "bad_json"])
def test_failed_import_preserves_the_last_good_corpus_and_report(
    tmp_path, bundle_factory, monkeypatch, failure, capsys
):
    extra = ("--strict-links",) if failure == "strict_links" else ()
    output, report, manifest, reference, cache = command(
        tmp_path, bundle_factory, monkeypatch, *extra
    )
    output.write_text("last-good-corpus")
    report.write_text("last-good-report")
    archive = cache / (reference["artifact"]["sha256"] + ".zip")
    if failure == "missing_bundle":
        archive.unlink()
    elif failure == "bad_bundle":
        archive.write_bytes(b"corrupt")
    elif failure == "bad_json":
        manifest.write_text('{"schemaVersion":1,"schemaVersion":2}')
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    assert output.read_text() == "last-good-corpus"
    assert report.read_text() == "last-good-report"
    assert "Traceback" not in capsys.readouterr().err


def test_cli_rejects_overwriting_an_input_manifest(tmp_path, bundle_factory, monkeypatch):
    _, _, manifest, _, _ = command(tmp_path, bundle_factory, monkeypatch)
    original = manifest.read_bytes()
    sys.argv.extend(["--output", str(manifest)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert manifest.read_bytes() == original
