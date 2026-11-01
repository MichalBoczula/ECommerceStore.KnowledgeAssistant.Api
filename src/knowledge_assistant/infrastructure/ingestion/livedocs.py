"""Read exactly the bundles pinned by a LiveDocs manifest, without extracting ZIPs."""

import hashlib
import json
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlparse

from gherkin.errors import CompositeParserException
from gherkin.parser import Parser
from gherkin.pickles.compiler import Compiler

from knowledge_assistant.domain.models import Corpus, Document, Source

from .json_source import loads
from .relationships import relate
from .reporting import bundle_report
from .structured import entries, validation_rules
from .validation import (
    registered_repository,
    select_projects,
    validate_archive_url,
    validate_schema,
)

MAX_ARCHIVE = 100 * 1024 * 1024
MAX_EXPANDED = 500 * 1024 * 1024


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_bundle(
    path: Path, reference: dict, repository: str | None = None
) -> tuple[dict, dict[str, bytes]]:
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
            safe_member(name)
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
    if "bundle.json" not in files:
        raise ValueError("Missing bundle inventory")
    metadata = loads(files.pop("bundle.json"))
    validate_schema(metadata, "bundle")
    if repository is not None and metadata["repository"] != repository:
        raise ValueError("Bundle repository differs from its LiveDocs registration")
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


def check_attachments(value: object, files: dict[str, bytes]) -> None:
    if isinstance(value, dict):
        for attachment in value.get("attachments", []) or []:
            source = attachment.get("source", "")
            safe_member(source)
            if "allure-results/" + source not in files:
                raise ValueError("Missing Allure attachment")
        for child in value.values():
            check_attachments(child, files)
    elif isinstance(value, list):
        for child in value:
            check_attachments(child, files)


def safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if (
        not name
        or not path.parts
        or "//" in name
        or "\\" in name
        or ":" in name
        or path.is_absolute()
        or ".." in path.parts
        or str(path) != name.rstrip("/")
    ):
        raise ValueError("Unsafe archive entry or attachment reference")


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
    execution_ids: set[str] = set()
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
            url=(
                f"{base_url.rstrip('/')}/livedoc/{version}/{service}/"
                + quote(path.split("#", 1)[0].removeprefix("sources/"), safe="/")
                if base_url
                else None
            ),
        )
        payload["provenance"] = {
            "repository": metadata.get("repository"),
            "workflowRunId": metadata.get("workflowRunId"),
            "generatedAt": metadata.get("generatedAt"),
            "sourceFileSha256": metadata.get("files", {})
            .get(path.split("#", 1)[0], {})
            .get("sha256"),
        }
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
            try:
                ast = Parser().parse(content.decode("utf-8-sig"))
            except CompositeParserException:
                raise ValueError("Invalid Gherkin feature source") from None
            definitions, examples = {}, {}

            def collect(children, definitions=definitions, examples=examples):
                for child in children:
                    if "rule" in child:
                        collect(child["rule"]["children"])
                    if "scenario" in child:
                        scenario = child["scenario"]
                        definitions[scenario["id"]] = scenario
                        for table in scenario.get("examples", []):
                            headers = [
                                c["value"] for c in table.get("tableHeader", {}).get("cells", [])
                            ]
                            for row in table.get("tableBody", []):
                                examples[row["id"]] = {
                                    "values": dict(
                                        zip(
                                            headers, [c["value"] for c in row["cells"]], strict=True
                                        )
                                    ),
                                    "line": row["location"]["line"],
                                }

            collect(ast["feature"]["children"])
            pickles = Compiler().compile({**ast, "uri": path})
            for index, case in enumerate(pickles):
                definition = next(definitions[x] for x in case["astNodeIds"] if x in definitions)
                example = next((examples[x] for x in case["astNodeIds"] if x in examples), {})
                values = example.get("values", {})
                scenario_ids = [
                    v for k, v in values.items() if k.casefold() in {"scenarioid", "caseid"}
                ]
                # Payments publishes the stable case ID at the start of the scenario title.
                payment_id = re.match(r"^(PAY[0-9]+-[0-9]+)\b", case["name"])
                if payment_id:
                    scenario_ids.append(payment_id[1])
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
                        "featureName": ast["feature"]["name"],
                        "scenarioName": definition["name"],
                        "scenarioLine": definition["location"]["line"],
                        "exampleValues": values,
                        "exampleLine": example.get("line"),
                        "scenarioIds": sorted(
                            set(scenario_ids + [t.removeprefix("@") for t in tags])
                        ),
                    },
                    tags=tags,
                )
        if path.startswith("allure-results/") and path.endswith("-result.json"):
            result = loads(content)

            check_attachments(result, files)
            if not result.get("uuid") or result.get("status") not in {
                "passed",
                "failed",
                "broken",
                "skipped",
                "unknown",
            }:
                raise ValueError("Incomplete Allure execution identity/status")
            if result["uuid"] in execution_ids:
                raise ValueError("Duplicate Allure execution identity")
            execution_ids.add(result["uuid"])
            result["scenarioIds"] = sorted(
                {
                    label["value"]
                    for label in result.get("labels", []) or []
                    if label.get("name") in {"scenarioId", "caseId"}
                }
            )
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
        if path.startswith("allure-results/") and path.endswith("-container.json"):
            check_attachments(loads(content), files)
    if not any(d.kind == "execution" for d in documents):
        raise ValueError("No Allure test results")

    spec = loads(files["sources/openapi.json"])
    if not str(spec.get("openapi", "")).startswith("3.") or "paths" not in spec:
        raise ValueError("An OpenAPI 3 document is required")
    operation_ids = set()
    for path, raw_item in spec["paths"].items():
        item = resolve_local_refs(raw_item, spec)
        for method, operation in item.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete", "head", "options"}:
                continue
            operation_id = operation.get("operationId", f"{method.upper()} {path}")
            if operation_id in operation_ids:
                raise ValueError("Duplicate OpenAPI operation identity")
            operation_ids.add(operation_id)
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
            raw_operation = (
                raw_item.get(method, operation) if isinstance(raw_item, dict) else operation
            )
            raw_content = (
                raw_operation.get("requestBody", {}).get("content", {}).get(media_type, {})
            )
            payload = {
                "rawRequestSchema": raw_content.get("schema", {}),
                "sourceOperation": raw_operation,
                "hasDocumentedExample": "example" in definition or bool(definition.get("examples")),
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
        value = loads(files[f"sources/{filename}.json"])
        for entry in entries(value, kind):
            normalized_rules = validation_rules(entry.value) if kind == "validation" else []
            payload = {
                "value": {**entry.value, "name": entry.title, "rules": normalized_rules}
                if kind == "validation"
                else entry.value,
                "original": entry.value,
                "aliases": sorted(entry.aliases),
                "sourceIdentity": entry.identity,
            }
            if kind == "validation":
                payload["normalizedRules"] = normalized_rules
            add(
                kind,
                entry.title,
                f"sources/{filename}.json#{entry.pointer}",
                entry.title + "\n" + json.dumps(entry.value, ensure_ascii=False, indent=2),
                payload,
                operation_id=entry.value.get("operationId") if kind == "operation_links" else None,
            )
        if kind == "operation_links" and isinstance(value, dict) and "operations" in value:
            registry = {k: v for k, v in value.items() if k != "operations"}
            if registry:
                add(
                    kind,
                    "Documentation registry",
                    f"sources/{filename}.json",
                    json.dumps(registry, ensure_ascii=False, indent=2),
                    {"value": registry},
                )
    relate(documents)
    return documents


def download_bundle(reference: dict, cache: Path) -> Path:
    """Only exact HTTPS Azure archive URLs from the trusted manifest; no arbitrary fetcher."""
    import os

    from azure.core.exceptions import AzureError
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobClient

    validate_archive_url(reference["artifact"]["url"])
    url = urlparse(reference["artifact"]["url"])
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
    except AzureError:
        raise ValueError(
            "Cannot download the pinned LiveDocs bundle. Check Blob read access and credentials."
        ) from None
    finally:
        if not isinstance(credential, str):
            credential.close()


def ingest_manifest(
    manifest: dict,
    cache: Path,
    version: str | None = None,
    download: bool = False,
    base_url: str = "",
    registry: dict | None = None,
    reports: list[dict] | None = None,
) -> Corpus:
    selected_version, projects = select_projects(manifest, version)
    # Verify every registration before making any network request.
    repositories = {
        p["project"]: registered_repository(registry, p["project"])
        if registry is not None
        else None
        for p in projects
    }
    snapshot_hash = digest(canonical(sorted(projects, key=lambda p: p["project"])).encode())
    snapshot_id = f"{selected_version}-{snapshot_hash[:20]}"
    documents = []
    for reference in projects:
        archive = (
            download_bundle(reference, cache)
            if download
            else cache / (reference["artifact"]["sha256"] + ".zip")
        )
        metadata, files = load_bundle(archive, reference, repositories[reference["project"]])
        imported = documents_from_bundle(
            metadata,
            files,
            snapshot_id,
            reference["artifact"]["sha256"],
            selected_version,
            base_url,
        )
        documents.extend(imported)
        if reports is not None:
            reports.append(bundle_report(metadata, reference, imported))
    if len({d.id for d in documents}) != len(documents):
        raise ValueError("Ingestion produced duplicate document identities")
    return Corpus(
        snapshot_id=snapshot_id,
        version=selected_version,
        manifest_sha256=digest(canonical(manifest).encode()),
        documents=documents,
    )
