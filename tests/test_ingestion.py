import json

import pytest

from knowledge_assistant.ingestion import documents_from_bundle, ingest_manifest, load_bundle


def test_outline_rows_remain_separate_with_expanded_values(bundle_factory):
    path, ref, _ = bundle_factory()
    metadata, files = load_bundle(path, ref)
    docs = documents_from_bundle(metadata, files, "pinned", ref["artifact"]["sha256"], "v1")
    scenarios = [d for d in docs if d.kind == "scenario"]
    assert len(scenarios) == 2
    assert "price 0" in scenarios[0].content
    assert "price -1" in scenarios[1].content
    assert all("@DEMO-MOBILE-001" in d.tags for d in scenarios)
    assert len({d.id for d in scenarios}) == 2


def test_background_and_gherkin_tables_survive(bundle_factory):
    feature = b"""Feature: Validation
  Background:
    Given a registered developer
  Scenario: Table
    When I submit a mobile phone
      | name | Example |
    Then it is accepted
"""
    path, ref, _ = bundle_factory({"bdd/mobile.feature": feature})
    metadata, files = load_bundle(path, ref)
    doc = next(
        d for d in documents_from_bundle(metadata, files, "s", "h", "v1") if d.kind == "scenario"
    )
    assert "registered developer" in doc.content
    assert "dataTable" in doc.content and "Example" in doc.content


def test_archive_and_file_integrity_are_enforced(bundle_factory):
    path, ref, _ = bundle_factory()
    ref["artifact"]["sha256"] = "b" * 64
    with pytest.raises(ValueError, match="checksum"):
        load_bundle(path, ref)


@pytest.mark.parametrize("name", ["../escape.txt", "/absolute.txt", "C:/escape.txt"])
def test_unsafe_zip_members_rejected(bundle_factory, name):
    path, ref, _ = bundle_factory(extras={name: b"bad"})
    with pytest.raises(ValueError, match="Unsafe"):
        load_bundle(path, ref)


def test_undeclared_files_rejected(bundle_factory):
    path, ref, _ = bundle_factory(extras={"unlisted.txt": b"bad"})
    with pytest.raises(ValueError, match="undeclared"):
        load_bundle(path, ref)


def test_missing_allure_attachment_rejected(bundle_factory):
    result = json.dumps(
        {
            "name": "Failed",
            "status": "failed",
            "steps": [
                {"name": "Then", "attachments": [{"source": "missing.json", "name": "response"}]}
            ],
        }
    ).encode()
    path, ref, _ = bundle_factory({"allure-results/demo-result.json": result})
    metadata, files = load_bundle(path, ref)
    with pytest.raises(ValueError, match="Missing Allure"):
        documents_from_bundle(metadata, files, "s", "h", "v1")


def test_development_version_gets_new_identity_when_bundle_changes(bundle_factory, tmp_path):
    path, ref, _ = bundle_factory()
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / (ref["artifact"]["sha256"] + ".zip")).write_bytes(path.read_bytes())
    manifest = {
        "schemaVersion": 1,
        "latestVersion": "v1",
        "versions": [{"version": "v1", "state": "development", "projects": [ref]}],
    }
    first = ingest_manifest(manifest, cache)
    path, ref2, _ = bundle_factory({"sources/flows.json": b'[{"name":"Changed"}]'})
    (cache / (ref2["artifact"]["sha256"] + ".zip")).write_bytes(path.read_bytes())
    manifest["versions"][0]["projects"] = [ref2]
    second = ingest_manifest(manifest, cache)
    assert first.snapshot_id != second.snapshot_id
    assert first.version == second.version == "v1"
