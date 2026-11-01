import importlib.util
import json
import zipfile
from pathlib import Path

import pytest

from knowledge_assistant.infrastructure.ingestion.livedocs import digest


@pytest.fixture
def demo():
    spec = importlib.util.spec_from_file_location("demo", Path("examples/create_demo.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle_factory(tmp_path, demo):
    def build(overrides=None, extras=None, metadata_overrides=None):
        files = {**demo.files, **(overrides or {})}
        metadata = {
            "schemaVersion": 1,
            "project": "products",
            "repository": "MichalBoczula/ProductsCatalog",
            "commitSha": "a" * 40,
            "generatedAt": "2026-10-31T12:00:00Z",
            "workflowRunId": 1,
            "files": {
                name: {"sha256": digest(data), "sizeBytes": len(data)}
                for name, data in files.items()
            },
        }
        metadata.update(metadata_overrides or {})
        path = tmp_path / "bundle.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(name, data)
            for name, data in (extras or {}).items():
                archive.writestr(name, data)
            archive.writestr("bundle.json", json.dumps(metadata))
        ref = {
            key: metadata[key] for key in ("project", "commitSha", "generatedAt", "workflowRunId")
        }
        ref["artifact"] = {
            "sha256": digest(path.read_bytes()),
            "sizeBytes": path.stat().st_size,
            "url": "https://example.blob.core.windows.net/livedocs/reports/bundle.zip",
        }
        return path, ref, files

    return build
