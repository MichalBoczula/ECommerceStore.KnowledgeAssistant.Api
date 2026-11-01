import json
from pathlib import Path

import pytest
from azure.core.exceptions import AzureError
from azure.storage.blob import BlobClient

from knowledge_assistant.infrastructure.ingestion.json_source import loads
from knowledge_assistant.infrastructure.ingestion.livedocs import (
    documents_from_bundle,
    download_bundle,
    ingest_manifest,
    load_bundle,
)
from knowledge_assistant.infrastructure.ingestion.validation import select_projects


def portal(reference):
    return {
        "schemaVersion": 1,
        "latestVersion": "v1",
        "versions": [
            {"version": "v1", "state": "development", "projects": [reference]},
        ],
    }


@pytest.mark.parametrize(
    "content",
    [
        '{"policies":[],"policies":[{}]}',
        '{"value":NaN}',
        '{"value":Infinity}',
    ],
)
def test_ambiguous_or_nonfinite_json_is_rejected(content):
    with pytest.raises(ValueError):
        loads(content)


@pytest.mark.parametrize("name", ["bdd/../a", "./a", "a//b", "a/./b", "a\\b", "."])
def test_noncanonical_archive_paths_are_rejected(bundle_factory, name):
    path, ref, _ = bundle_factory(extras={name: b"unsafe"})
    with pytest.raises(ValueError, match="Unsafe"):
        load_bundle(path, ref)


def test_repository_is_verified_against_selected_projects_registration(bundle_factory):
    path, ref, _ = bundle_factory()
    with pytest.raises(ValueError, match="repository"):
        load_bundle(path, ref, repository="Someone/Unrelated")


def test_registration_is_checked_before_download(bundle_factory, monkeypatch, tmp_path):
    _, ref, _ = bundle_factory()

    def unexpected(*args, **kwargs):
        pytest.fail("Download must not precede registration validation")

    monkeypatch.setattr(
        "knowledge_assistant.infrastructure.ingestion.livedocs.download_bundle", unexpected
    )
    with pytest.raises(ValueError, match="absent"):
        ingest_manifest(portal(ref), tmp_path, download=True, registry={})


@pytest.mark.parametrize("change", ["sha", "size", "date", "version", "latest", "project"])
def test_invalid_or_ambiguous_portal_manifest_rejected(bundle_factory, change):
    _, ref, _ = bundle_factory()
    manifest = portal(ref)
    if change == "sha":
        ref["artifact"]["sha256"] = "bad"
    elif change == "size":
        ref["artifact"]["sizeBytes"] = -1
    elif change == "date":
        ref["generatedAt"] = "not-a-date"
    elif change == "version":
        manifest["versions"].append(manifest["versions"][0])
    elif change == "latest":
        manifest["latestVersion"] = "v2"
    else:
        manifest["versions"][0]["projects"].append(ref)
    with pytest.raises(ValueError):
        select_projects(manifest, None)


@pytest.mark.parametrize(
    "url",
    [
        "https://example.blob.core.windows.net/livedocs/reports/x.zip?sig=example",
        "https://name:password@example.blob.core.windows.net/livedocs/reports/x.zip",
        "https://example.blob.core.windows.net:443/livedocs/reports/x.zip",
        "https://example.blob.core.windows.net.evil.test/livedocs/reports/x.zip",
        "https://example.blob.core.windows.net/livedocs/reports/../x.zip",
        "https://example.blob.core.windows.net/livedocs/reports/%2e%2e/x.zip",
    ],
)
def test_blob_urls_cannot_smuggle_credentials_or_change_origin(bundle_factory, url):
    _, ref, _ = bundle_factory()
    ref["artifact"]["url"] = url
    with pytest.raises(ValueError, match="credential-free"):
        select_projects(portal(ref), None)


def test_bundle_schema_and_inventory_hashes_are_verified(bundle_factory):
    path, ref, _ = bundle_factory(metadata_overrides={"workflowRunId": "wrong-type"})
    with pytest.raises(ValueError, match="schema"):
        load_bundle(path, ref)
    path, ref, _ = bundle_factory(
        metadata_overrides={
            "files": {
                "bdd/mobile.feature": {
                    "sizeBytes": 1,
                    "sha256": "0" * 64,
                }
            }
        }
    )
    with pytest.raises(ValueError, match="undeclared"):
        load_bundle(path, ref)


@pytest.mark.parametrize("attachment", ["missing.json", "../outside.json"])
def test_nested_container_attachments_are_also_checked(bundle_factory, attachment):
    container = {"befores": [{"steps": [{"attachments": [{"source": attachment}]}]}]}
    path, ref, _ = bundle_factory(
        {"allure-results/setup-container.json": json.dumps(container).encode()}
    )
    metadata, files = load_bundle(path, ref)
    with pytest.raises(ValueError, match="Missing Allure|Unsafe"):
        documents_from_bundle(metadata, files, "s", "h", "v1")


def test_duplicate_execution_uuids_are_not_counted_twice(bundle_factory, demo):
    path, ref, _ = bundle_factory(
        {"allure-results/copy-result.json": demo.files["allure-results/demo-result.json"]}
    )
    metadata, files = load_bundle(path, ref)
    with pytest.raises(ValueError, match="Duplicate Allure"):
        documents_from_bundle(metadata, files, "s", "h", "v1")


def test_blob_provider_errors_do_not_expose_credential_values(
    bundle_factory, monkeypatch, tmp_path
):
    _, ref, _ = bundle_factory()
    monkeypatch.setenv("LIVEDOCS_STORAGE_SAS_TOKEN", "test-only")

    def denied(*args, **kwargs):
        raise AzureError("private provider diagnostic sig=do-not-print")

    monkeypatch.setattr(BlobClient, "from_blob_url", denied)
    with pytest.raises(ValueError, match="Check Blob read access") as failure:
        download_bundle(ref, Path(tmp_path / "cache"))
    assert "do-not-print" not in str(failure.value)
