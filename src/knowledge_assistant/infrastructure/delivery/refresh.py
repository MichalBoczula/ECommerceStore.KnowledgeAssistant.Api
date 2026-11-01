import json
import re
import subprocess
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from ..configuration.settings import Settings
from ..ingestion.json_source import read
from ..ingestion.livedocs import canonical, digest
from ..retrieval.index_plan import corpus_hash, embedding_profile
from ..retrieval.indexing_io import verified_corpus

API_REPOSITORY = "MichalBoczula/ECommerceStore.KnowledgeAssistant.Api"
DOCS_REPOSITORY = "MichalBoczula/ECommerceStore.LiveDocs"
API_IMAGE = "docker.io/mb0101/ecommerce-store-knowledge-assistant-api"
DOCS_IMAGE = "docker.io/mb0101/ecommerce-store-livedocs"


def full_sha(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise ValueError("Expected a full source commit")
    return value


def immutable_image(value: str, repository: str) -> str:
    normalized = value if value.startswith("docker.io/") else "docker.io/" + value
    if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", normalized):
        raise ValueError("Expected the repository immutable image digest")
    return normalized


def github_json(endpoint: str) -> dict:
    # gh owns authentication/redirect handling. Never include its stderr or tokens in reports.
    result = subprocess.run(
        ["gh", "api", "--method", "GET", endpoint], capture_output=True, text=True, timeout=60
    )
    if result.returncode:
        raise ValueError("Cannot read publication evidence from GitHub")
    return json.loads(result.stdout)


def successful_run(run: dict, repository: str, sha: str | None = None) -> bool:
    return all(
        [
            run.get("status") == "completed",
            run.get("conclusion") == "success",
            run.get("head_branch") == "main",
            run.get("event") in {"push", "workflow_dispatch"},
            run.get("head_repository", {}).get("full_name") == repository,
            run.get("path", "").split("@")[0] == ".github/workflows/ci.yml",
            sha is None or run.get("head_sha") == sha,
        ]
    )


def select_publication(api_sha: str, requested_sha: str = "", fetch=github_json) -> dict:
    full_sha(api_sha)
    if requested_sha:
        full_sha(requested_sha)

    def runs(repo, sha):
        query = urlencode(
            dict(
                branch="main", status="success", per_page=100, **({"head_sha": sha} if sha else {})
            )
        )
        return fetch(f"repos/{repo}/actions/workflows/ci.yml/runs?{query}")["workflow_runs"]

    if not any(successful_run(r, API_REPOSITORY, api_sha) for r in runs(API_REPOSITORY, api_sha)):
        raise ValueError("API source commit has no successful main CI run")
    run = next(
        (
            r
            for r in runs(DOCS_REPOSITORY, requested_sha)
            if successful_run(r, DOCS_REPOSITORY, requested_sha or None)
        ),
        None,
    )
    if run is None:
        raise ValueError("No successful LiveDocs main publication was found")
    full_sha(run["head_sha"])
    artifacts = fetch(f"repos/{DOCS_REPOSITORY}/actions/runs/{int(run['id'])}/artifacts")[
        "artifacts"
    ]
    artifact = next(
        (a for a in artifacts if a["name"] == "livedocs-image" and not a.get("expired")), None
    )
    if artifact is None:
        raise ValueError("LiveDocs publication receipt is missing or expired")
    return dict(
        schemaVersion=1,
        apiCommit=api_sha,
        livedocsCommit=run["head_sha"],
        livedocsRepository=DOCS_REPOSITORY,
        publicationRun=int(run["id"]),
        publicationArtifact=int(artifact["id"]),
    )


def https_url(value: str) -> str:
    url = urlsplit(value)
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
    ):
        raise ValueError("Use an HTTPS endpoint without credentials, query or fragment")
    return value.rstrip("/")


def prepare_plan(
    selection: dict,
    publication: dict,
    source: Path,
    suite: Path,
    settings: Settings,
    base_url: str,
    revision: str = "1",
) -> dict:
    full_sha(selection["apiCommit"])
    full_sha(selection["livedocsCommit"])
    if (
        publication.get("schemaVersion") != 1
        or publication.get("commitSha") != selection["livedocsCommit"]
    ):
        raise ValueError("Published LiveDocs image differs from the selected source commit")
    image = immutable_image(publication["image"], DOCS_IMAGE)
    actual = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    if actual != selection["livedocsCommit"]:
        raise ValueError("LiveDocs checkout differs from the published source")
    settings.require_azure(chat=True)
    https_url(settings.search_endpoint)
    https_url(settings.openai_endpoint)
    https_url(base_url)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,126}[a-z0-9]", settings.search_index):
        raise ValueError("Invalid refresh index name")
    if not revision or len(revision) > 128:
        raise ValueError("Invalid refresh revision")
    manifests = {}
    for name in ["portal.json", "projects.json"]:
        # Read immutable Git objects; changed working files cannot change selected inputs.
        content = subprocess.run(
            ["git", "-C", str(source), "show", f"HEAD:manifests/{name}"],
            capture_output=True,
            check=True,
        ).stdout
        if (source / "manifests" / name).read_bytes() != content:
            raise ValueError("LiveDocs manifest checkout has local changes")
        manifests[name] = digest(content)
    config = dict(
        searchEndpoint=settings.search_endpoint,
        searchIndex=settings.search_index,
        openaiEndpoint=settings.openai_endpoint,
        chatDeployment=settings.chat_deployment,
        embeddingDeployment=settings.embedding_deployment,
        embeddingDimensions=settings.embedding_dimensions,
        embeddingRevision=settings.embedding_revision,
        refreshRevision=revision,
        livedocsBaseUrl=base_url.rstrip("/"),
    )
    identity = {
        **selection,
        "livedocsImage": image,
        "manifestFiles": manifests,
        "manifestSha256": digest(canonical(read(source / "manifests/portal.json")).encode()),
        "suiteFileSha256": digest(suite.read_bytes()),
        "configuration": config,
    }
    return {
        **identity,
        "releaseId": digest(canonical(identity).encode()),
        "embeddingProfile": embedding_profile(settings),
    }


def enforce_budget(
    plan: dict,
    max_inputs: int = 2000,
    max_input_bytes: int = 8_000_000,
    max_vector_bytes: int = 40_000_000,
) -> None:
    for name, cap in [
        ("uniqueEmbeddingInputs", max_inputs),
        ("embeddingInputBytes", max_input_bytes),
        ("rawFloat32VectorBytes", max_vector_bytes),
    ]:
        value = plan.get(name)
        if type(cap) is not int or cap < 1 or type(value) is not int or value < 1 or value > cap:
            raise ValueError("Planned indexing exceeds the configured refresh budget")


def verify_release(
    plan: dict,
    corpus_path: Path,
    ingestion_path: Path,
    index: dict,
    retrieval: dict,
    model: dict,
    suite_path: Path,
    settings: Settings,
) -> dict:
    corpus = verified_corpus(corpus_path, ingestion_path)
    ingestion = read(ingestion_path)
    if (
        ingestion.get("manifestCommit") != plan["livedocsCommit"]
        or corpus.manifest_sha256 != plan["manifestSha256"]
    ):
        raise ValueError("Ingested corpus differs from the selected LiveDocs manifests")
    file_sha = digest(corpus_path.read_bytes())
    profile = embedding_profile(settings)
    if (
        profile != plan["embeddingProfile"]
        or digest(suite_path.read_bytes()) != plan["suiteFileSha256"]
    ):
        raise ValueError("Refresh profile or suite changed during the run")
    config = plan["configuration"]
    if any(
        config[name] != value
        for name, value in {
            "searchEndpoint": settings.search_endpoint,
            "searchIndex": settings.search_index,
            "openaiEndpoint": settings.openai_endpoint,
            "chatDeployment": settings.chat_deployment,
            "embeddingDeployment": settings.embedding_deployment,
            "embeddingDimensions": settings.embedding_dimensions,
            "embeddingRevision": settings.embedding_revision,
        }.items()
    ):
        raise ValueError("Refresh settings changed during the run")
    if (
        index.get("corpusHash") != corpus_hash(corpus)
        or index.get("snapshotId") != corpus.snapshot_id
        or index.get("embeddingProfile") != profile
        or index.get("uploadComplete") is not True
        or index.get("visibleChunksVerified") is not True
        or index.get("sourceDocuments") != len(corpus.documents)
        or index.get("searchIndex") != settings.search_index
    ):
        raise ValueError("Index completion does not match this release corpus/profile")
    from knowledge_assistant.application.evaluation.models import EvaluationSuite

    expected = EvaluationSuite.model_validate(read(suite_path))
    for report, mode, count in [(retrieval, "evidence", len(expected.cases)), (model, "model", 1)]:
        case_ids = {c.id for c in expected.cases} if mode == "evidence" else {"products-01"}
        if (
            report.get("suiteId") != expected.id
            or report.get("corpusFileSha256") != file_sha
            or report.get("suiteFileSha256") != plan["suiteFileSha256"]
            or report.get("snapshotId") != corpus.snapshot_id
            or report.get("isDemo") is not False
            or report.get("answerMode") != mode
            or report.get("retrievalMode") != "azure_hybrid"
            or report.get("total") != count
            or report.get("passed") != count
            or report.get("failed") != 0
            or len(report.get("cases", [])) != count
            or {r.get("id") for r in report.get("cases", [])} != case_ids
            or any(
                r.get("passed") is not True
                or r.get("failures")
                or not r.get("checks")
                or any(value is not True for value in r["checks"].values())
                for r in report["cases"]
            )
        ):
            raise ValueError("Live evaluation did not pass for the exact release corpus")
    if model["cases"][0]["id"] != "products-01":
        raise ValueError("Core model evaluation case is missing")
    return dict(
        snapshotId=corpus.snapshot_id,
        manifestSha256=corpus.manifest_sha256,
        corpusFileSha256=file_sha,
        corpusHash=index["corpusHash"],
        embeddingProfile=profile,
        sourceDocuments=len(corpus.documents),
        indexedChunks=index["indexedChunks"],
        sourcePins=[
            dict(service=service, commitSha=commit, bundleSha256=bundle)
            for service, commit, bundle in sorted(
                {
                    (d.source.service, d.source.commit_sha, d.source.bundle_sha256)
                    for d in corpus.documents
                }
            )
        ],
        ingestionWarnings=ingestion.get("warnings", 0),
    )


def handoff(plan: dict, verified: dict, image: dict) -> dict:
    reference = immutable_image(image["image"], API_IMAGE)
    if (
        image.get("sourceCommit") != plan["apiCommit"]
        or image.get("releaseId") != plan["releaseId"]
    ):
        raise ValueError("Published API image differs from the refresh identity")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", image.get("scannedImageId", "")):
        raise ValueError("Published API image has no scanned identity")
    config = plan["configuration"]
    return dict(
        schemaVersion=1,
        releaseId=plan["releaseId"],
        apiCommit=plan["apiCommit"],
        livedocsCommit=plan["livedocsCommit"],
        publicationRun=plan["publicationRun"],
        image=reference,
        livedocsImage=plan["livedocsImage"],
        searchEndpoint=config["searchEndpoint"],
        **verified,
        evaluation=dict(retrievalCases="all", modelCases=["products-01"]),
        terraform=dict(
            app_images=dict(knowledge_api=reference, livedocs=plan["livedocsImage"]),
            knowledge_runtime_mode="azure",
            knowledge_search_index=config["searchIndex"],
            knowledge_embedding_revision=config["embeddingRevision"],
            knowledge_models=dict(
                endpoint=config["openaiEndpoint"],
                chat_deployment=config["chatDeployment"],
                embedding_deployment=config["embeddingDeployment"],
                embedding_dimensions=config["embeddingDimensions"],
            ),
        ),
        deploymentApplied=False,
    )


def validate_receipt(plan: dict, receipt: dict) -> None:
    if (
        receipt.get("releaseId") != plan["releaseId"]
        or receipt.get("apiCommit") != plan["apiCommit"]
        or receipt.get("livedocsCommit") != plan["livedocsCommit"]
        or receipt.get("livedocsImage") != plan["livedocsImage"]
        or receipt.get("embeddingProfile") != plan["embeddingProfile"]
        or receipt.get("manifestSha256") != plan["manifestSha256"]
        or receipt.get("searchEndpoint") != plan["configuration"]["searchEndpoint"]
        or receipt.get("deploymentApplied") is not False
    ):
        raise ValueError("Cached handoff differs from the selected release")
    image = immutable_image(receipt["image"], API_IMAGE)
    config = plan["configuration"]
    terraform = receipt.get("terraform", {})
    if (
        terraform.get("app_images") != {"knowledge_api": image, "livedocs": plan["livedocsImage"]}
        or terraform.get("knowledge_runtime_mode") != "azure"
        or terraform.get("knowledge_search_index") != config["searchIndex"]
        or terraform.get("knowledge_embedding_revision") != config["embeddingRevision"]
        or terraform.get("knowledge_models")
        != {
            "endpoint": config["openaiEndpoint"],
            "chat_deployment": config["chatDeployment"],
            "embedding_deployment": config["embeddingDeployment"],
            "embedding_dimensions": config["embeddingDimensions"],
        }
    ):
        raise ValueError("Cached Terraform handoff differs from the release configuration")
