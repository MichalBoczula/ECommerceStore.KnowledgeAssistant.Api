"""Read exactly the bundles pinned by a LiveDocs manifest, without extracting ZIPs."""

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from gherkin.parser import Parser
from gherkin.pickles.compiler import Compiler

from .models import Corpus, Document, Source

MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 500 * 1024 * 1024


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_bundle(path: Path, reference: dict) -> tuple[dict, dict[str, bytes]]:
    if path.stat().st_size > MAX_ARCHIVE:
        raise ValueError("Archive exceeds the LiveDocs compressed size limit")
    if path.stat().st_size != reference["artifact"]["sizeBytes"]:
        raise ValueError("Archive size differs from its manifest")
    if digest(path.read_bytes()) != reference["artifact"]["sha256"]:
        raise ValueError("Archive checksum differs from its manifest")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 50001 or sum(x.file_size for x in infos) > MAX_EXPANDED:
            raise ValueError("Archive exceeds the LiveDocs inventory/expanded size limit")
        files: dict[str, bytes] = {}
        names: set[str] = set()
        for info in infos:
            name = info.filename
            parts = PurePosixPath(name).parts
            if (
                not parts
                or ".." in parts
                or name.startswith("/")
                or "\\" in name
                or re.match(r"^[A-Za-z]:", name)
                or name in names
                or stat.S_ISLNK(info.external_attr >> 16)
                or info.flag_bits & 1
            ):
                raise ValueError("Unsafe or duplicate archive entry")
            names.add(name)
            if not info.is_dir():
                files[name] = archive.read(info)
    metadata = json.loads(files.pop("bundle.json"))
    if metadata.get("schemaVersion") != 1:
        raise ValueError("Unsupported bundle schema version")
    for key in ("project", "commitSha", "workflowRunId", "generatedAt"):
        if metadata[key] != reference[key]:
            raise ValueError(f"Bundle identity mismatch: {key}")
    inventory = metadata["files"]
    if set(files) != set(inventory):
        raise ValueError("Bundle contains missing or undeclared files")
    for name, content in files.items():
        if digest(content) != inventory[name]["sha256"]:
            raise ValueError(f"File checksum mismatch: {name}")
        if len(content) != inventory[name]["sizeBytes"]:
            raise ValueError(f"File size mismatch: {name}")
    required = [
        "sources/openapi.json",
        "sources/flows.json",
        "sources/validation-policies.json",
        "sources/operation-links.json",
    ]
    if any(p not in files for p in required):
        raise ValueError("Missing structured LiveDocs sources")
    if not any(p.startswith("bdd/") and p.endswith(".feature") for p in files):
        raise ValueError("No BDD feature files")
    return metadata, files


def resolve_local_refs(value: object, root: dict, seen: frozenset[str] = frozenset()) -> object:
    if isinstance(value, list):
        return [resolve_local_refs(v, root, seen) for v in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        ref = value["$ref"]
        if not ref.startswith("#/"):
            raise ValueError("External OpenAPI references must be bundled before ingestion")
        if ref in seen:
            return value
        target = root
        for part in ref[2:].split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        resolved = resolve_local_refs(target, root, seen | {ref})
        siblings = {k: resolve_local_refs(v, root, seen) for k, v in value.items() if k != "$ref"}
        return {**resolved, **siblings}
    return {k: resolve_local_refs(v, root, seen) for k, v in value.items()}


def documents_from_bundle(
    metadata: dict,
    files: dict[str, bytes],
    snapshot_id: str,
    bundle_sha: str,
    version: str,
    base_url: str = "",
) -> list[Document]:
    documents: list[Document] = []
    service = metadata["project"]

    def add(
        kind: str,
        title: str,
        path: str,
        content: str,
        payload: dict,
        operation_id: str | None = None,
        tags: list[str] | None = None,
    ) -> None:
        identity = digest(f"{snapshot_id}:{service}:{kind}:{path}".encode())
        source = Source(
            id="s-" + identity,
            service=service,
            snapshot_id=snapshot_id,
            path=path,
            commit_sha=metadata["commitSha"],
            bundle_sha256=bundle_sha,
            title=title,
            url=(f"{base_url.rstrip('/')}/livedoc/{version}/{service}/" if base_url else None),
        )
        documents.append(
            Document(
                id="d-" + identity,
                source=source,
                kind=kind,
                title=title,
                content=content,
                payload=payload,
                operation_id=operation_id,
                tags=tags or [],
            )
        )

    for path, content in sorted(files.items()):
        if path.startswith("bdd/") and path.endswith(".feature"):
            ast = Parser().parse(content.decode("utf-8-sig"))
            pickles = Compiler().compile({**ast, "uri": path})
            for index, case in enumerate(pickles):
                steps = case["steps"]
                text = "\n".join(
                    step["text"]
                    + (
                        "\n" + json.dumps(step["argument"], ensure_ascii=False)
                        if "argument" in step
                        else ""
                    )
                    for step in steps
                )
                tags = [tag["name"] for tag in case.get("tags", [])]
                # Compiled pickles expand every outline row, backgrounds and data tables.
                case_key = digest(canonical([case["name"], text, index]).encode())[:20]
                add(
                    "scenario",
                    case["name"],
                    f"{path}#case-{case_key}",
                    f"{case['name']}\n{' '.join(tags)}\n{text}",
                    {
                        "name": case["name"],
                        "steps": steps,
                        "tags": tags,
                        "exampleOrdinal": index,
                        "featurePath": path,
                    },
                    tags=tags,
                )
        if path.startswith("allure-results/") and path.endswith("-result.json"):
            result = json.loads(content)

            def check_attachments(node: dict) -> None:
                for attachment in node.get("attachments", []):
                    source = attachment["source"]
                    if PurePosixPath(source).name != source:
                        raise ValueError("Unsafe Allure attachment reference")
                    if f"allure-results/{source}" not in files:
                        raise ValueError("Missing Allure attachment")
                for step in node.get("steps", []):
                    check_attachments(step)

            check_attachments(result)
            add(
                "execution",
                result.get("name", path),
                path,
                json.dumps(
                    {
                        k: result[k]
                        for k in ("name", "description", "status", "steps", "parameters")
                        if k in result
                    },
                    ensure_ascii=False,
                ),
                result,
            )
    if not any(d.kind == "execution" for d in documents):
        raise ValueError("No Allure test results")

    spec = json.loads(files["sources/openapi.json"])
    if not str(spec.get("openapi", "")).startswith("3.") or "paths" not in spec:
        raise ValueError("An OpenAPI 3 document is required")
    for path, raw_item in spec["paths"].items():
        item = resolve_local_refs(raw_item, spec)
        for method, operation in item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            operation_id = operation.get("operationId", f"{method.upper()} {path}")
            body = operation.get("requestBody", {})
            media = body.get("content", {})
            media_type = (
                "application/json" if "application/json" in media else next(iter(media), "")
            )
            definition = media.get(media_type, {})
            example = definition.get("example")
            if example is None:
                example = next(
                    (
                        e["value"]
                        for e in definition.get("examples", {}).values()
                        if isinstance(e, dict) and "value" in e
                    ),
                    None,
                )
            payload = {
                "method": method.upper(),
                "path": path,
                "operationId": operation_id,
                "openapiVersion": spec["openapi"],
                "mediaType": media_type,
                "requestSchema": definition.get("schema", {}),
                "exampleRequest": example,
                "operation": operation,
                "referenceSchemas": spec.get("components", {}).get("schemas", {}),
            }
            add(
                "api_contract",
                operation.get("summary", operation_id),
                "sources/openapi.json#/paths/"
                + path.replace("~", "~0").replace("/", "~1")
                + f"/{method}",
                f"{method.upper()} {path}\n{operation_id}\n"
                + json.dumps(operation, ensure_ascii=False),
                payload,
                operation_id=operation_id,
            )

    for filename, kind in (
        ("validation-policies", "validation"),
        ("flows", "flow"),
        ("operation-links", "operation_links"),
    ):
        value = json.loads(files[f"sources/{filename}.json"])
        entries = value if isinstance(value, list) else [value]
        for index, entry in enumerate(entries):
            title = (
                entry.get("name") or entry.get("title") or entry.get("id")
                if isinstance(entry, dict)
                else None
            ) or f"{filename} {index + 1}"
            add(
                kind,
                str(title),
                f"sources/{filename}.json#entry-{index}",
                json.dumps(entry, ensure_ascii=False, indent=2),
                {"value": entry},
            )
    return documents


def download_bundle(reference: dict, cache: Path) -> Path:
    """Only exact HTTPS Azure archive URLs from the trusted manifest; no arbitrary fetcher."""
    import os

    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    url = urlparse(reference["artifact"]["url"])
    if (
        url.scheme != "https"
        or not url.hostname
        or not url.hostname.endswith(".blob.core.windows.net")
        or not url.path.startswith("/livedocs/reports/")
        or url.query
        or url.fragment
    ):
        raise ValueError("Expected a credential-free LiveDocs Azure Blob URL")
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / (reference["artifact"]["sha256"] + ".zip")
    if target.exists():
        return target
    credential = os.environ.get("LIVEDOCS_STORAGE_SAS_TOKEN") or DefaultAzureCredential()
    try:
        with BlobClient.from_blob_url(url.geturl(), credential=credential) as client:
            downloader = client.download_blob(max_concurrency=1)
            if (
                downloader.size != reference["artifact"]["sizeBytes"]
                or downloader.size > MAX_ARCHIVE
            ):
                raise ValueError("Unexpected archive size")
            data = downloader.readall()
        if digest(data) != reference["artifact"]["sha256"]:
            raise ValueError("Downloaded archive checksum mismatch")
        temporary = target.with_suffix(".partial")
        temporary.write_bytes(data)
        temporary.replace(target)
        return target
    finally:
        if not isinstance(credential, str):
            credential.close()


def ingest_manifest(
    manifest: dict,
    cache: Path,
    version: str | None = None,
    download: bool = False,
    base_url: str = "",
) -> Corpus:
    if manifest.get("schemaVersion") != 1:
        raise ValueError("Unsupported portal schema version")
    selected_version = version or manifest["latestVersion"]
    selected = next((v for v in manifest["versions"] if v["version"] == selected_version), None)
    if not selected or not selected["projects"]:
        raise ValueError("Selected version is empty or does not exist")
    projects = selected["projects"]
    if len({p["project"] for p in projects}) != len(projects):
        raise ValueError("Duplicate projects in selected snapshot")
    snapshot_hash = digest(canonical(sorted(projects, key=lambda p: p["project"])).encode())
    snapshot_id = f"{selected_version}-{snapshot_hash[:20]}"
    documents = []
    for reference in projects:
        archive = (
            download_bundle(reference, cache)
            if download
            else cache / (reference["artifact"]["sha256"] + ".zip")
        )
        metadata, files = load_bundle(archive, reference)
        documents.extend(
            documents_from_bundle(
                metadata,
                files,
                snapshot_id,
                reference["artifact"]["sha256"],
                selected_version,
                base_url,
            )
        )
    return Corpus(
        snapshot_id=snapshot_id,
        version=selected_version,
        manifest_sha256=digest(canonical(manifest).encode()),
        documents=documents,
    )
