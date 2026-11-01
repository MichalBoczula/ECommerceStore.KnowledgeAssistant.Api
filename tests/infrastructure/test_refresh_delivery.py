import json
import subprocess
from pathlib import Path

import pytest

from knowledge_assistant.infrastructure.delivery.refresh import (
    API_IMAGE,
    API_REPOSITORY,
    DOCS_IMAGE,
    DOCS_REPOSITORY,
    enforce_budget,
    handoff,
    prepare_plan,
    select_publication,
    validate_receipt,
    verify_release,
)
from knowledge_assistant.infrastructure.ingestion.livedocs import canonical, digest
from knowledge_assistant.infrastructure.retrieval.index_plan import corpus_hash, embedding_profile


def successful(repo, sha="a" * 40):
    return dict(
        id=123,
        status="completed",
        conclusion="success",
        head_sha=sha,
        head_repository={"full_name": repo},
        head_branch="main",
        event="push",
        path=".github/workflows/ci.yml",
    )


def selection_reader(docs_run=None, api_run=None, expired=False):
    def fetch(endpoint):
        if "/artifacts" in endpoint:
            return {"artifacts": [dict(id=456, name="livedocs-image", expired=expired)]}
        repo = API_REPOSITORY if API_REPOSITORY in endpoint else DOCS_REPOSITORY
        return {
            "workflow_runs": [api_run or successful(repo)]
            if repo == API_REPOSITORY
            else [docs_run or successful(repo)]
        }

    return fetch


def test_only_successful_main_publication_is_selected():
    result = select_publication("a" * 40, fetch=selection_reader())
    assert result["publicationRun"] == 123 and result["publicationArtifact"] == 456
    assert result["livedocsCommit"] == "a" * 40


@pytest.mark.parametrize(
    "field,value",
    [
        ("event", "pull_request"),
        ("head_branch", "feature"),
        ("status", "in_progress"),
        ("conclusion", "failure"),
        ("path", ".github/workflows/unrelated.yml"),
        ("head_repository", {"full_name": "fork/LiveDocs"}),
    ],
)
def test_unpublished_or_untrusted_source_is_rejected(field, value):
    run = successful(DOCS_REPOSITORY)
    run[field] = value
    with pytest.raises(ValueError, match="publication"):
        select_publication("a" * 40, fetch=selection_reader(docs_run=run))


def test_api_ci_expired_receipts_and_wrong_requested_commit_block_selection():
    run = successful(API_REPOSITORY)
    run["conclusion"] = "failure"
    with pytest.raises(ValueError, match="API source"):
        select_publication("a" * 40, fetch=selection_reader(api_run=run))
    with pytest.raises(ValueError, match="expired"):
        select_publication("a" * 40, fetch=selection_reader(expired=True))
    with pytest.raises(ValueError, match="publication"):
        select_publication("a" * 40, "b" * 40, fetch=selection_reader())
    with pytest.raises(ValueError, match="full source"):
        select_publication("main", fetch=lambda _: pytest.fail("Bad ref reached GitHub"))


@pytest.fixture
def release(tmp_path, demo, cloud):
    source = tmp_path / "docs"
    (source / "manifests").mkdir(parents=True)
    portal = {"fixture": True}
    for name, value in [("portal.json", portal), ("projects.json", {})]:
        (source / "manifests" / name).write_text(json.dumps(value))
    for args in [
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "Source manifests",
        ],
    ]:
        subprocess.run(["git", "-C", str(source), *args], check=True)
    sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"]).decode().strip()
    selected = dict(
        schemaVersion=1,
        apiCommit="a" * 40,
        livedocsCommit=sha,
        publicationRun=123,
        publicationArtifact=456,
        livedocsRepository=DOCS_REPOSITORY,
    )
    published = dict(schemaVersion=1, image=DOCS_IMAGE + "@sha256:" + "b" * 64, commitSha=sha)
    settings = cloud.settings.model_copy(update={"chat_deployment": "chat"})
    suite = tmp_path / "suite.json"
    original = json.loads(Path("evaluation/demo-smoke.json").read_text())
    original["isDemo"] = False
    original["cases"][0]["id"] = "products-01"
    suite.write_text(json.dumps(original))
    plan = prepare_plan(selected, published, source, suite, settings, "https://docs.example.test")
    corpus = demo.corpus.model_copy(deep=True)
    corpus.is_demo = False
    corpus.manifest_sha256 = digest(canonical(portal).encode())
    path = tmp_path / "corpus.json"
    path.write_text(corpus.model_dump_json(by_alias=True))
    ingestion = tmp_path / "ingestion.json"
    ingestion.write_text(
        json.dumps(
            dict(
                corpusSha256=digest(path.read_bytes()),
                corpusBytes=path.stat().st_size,
                snapshotId=corpus.snapshot_id,
                manifestSha256=corpus.manifest_sha256,
                documents=len(corpus.documents),
                manifestCommit=sha,
            )
        )
    )
    index = dict(
        corpusHash=corpus_hash(corpus),
        snapshotId=corpus.snapshot_id,
        embeddingProfile=embedding_profile(settings),
        uploadComplete=True,
        visibleChunksVerified=True,
        sourceDocuments=len(corpus.documents),
        indexedChunks=len(corpus.documents),
        searchIndex=settings.search_index,
    )
    base = dict(
        suiteId=original["id"],
        corpusFileSha256=digest(path.read_bytes()),
        suiteFileSha256=plan["suiteFileSha256"],
        snapshotId=corpus.snapshot_id,
        isDemo=False,
        retrievalMode="azure_hybrid",
        failed=0,
    )
    retrieval = dict(
        **base,
        answerMode="evidence",
        total=4,
        passed=4,
        cases=[
            dict(id=c["id"], passed=True, failures=[], checks={"scope": True})
            for c in original["cases"]
        ],
    )
    model = dict(
        **base,
        answerMode="model",
        total=1,
        passed=1,
        cases=[dict(id="products-01", passed=True, failures=[], checks={"examples": True})],
    )
    return (
        dict(
            plan=plan,
            corpus_path=path,
            ingestion_path=ingestion,
            index=index,
            retrieval=retrieval,
            model=model,
            suite_path=suite,
            settings=settings,
        ),
        source,
        selected,
        published,
    )


def test_plan_binds_published_checkout_manifest_suite_and_configuration(release):
    inputs, source, selected, publication = release
    first = inputs["plan"]
    changed = prepare_plan(
        selected,
        publication,
        source,
        inputs["suite_path"],
        inputs["settings"],
        "https://docs.example.test",
        revision="2",
    )
    assert changed["releaseId"] != first["releaseId"]
    (source / "manifests/portal.json").write_text("{}")
    with pytest.raises(ValueError, match="local changes"):
        prepare_plan(
            selected,
            publication,
            source,
            inputs["suite_path"],
            inputs["settings"],
            "https://docs.example.test",
        )


def test_mutable_image_and_endpoint_credentials_cannot_enter_a_plan(release):
    inputs, source, selected, publication = release
    publication["image"] = DOCS_IMAGE + ":latest"
    with pytest.raises(ValueError, match="immutable"):
        prepare_plan(
            selected,
            publication,
            source,
            inputs["suite_path"],
            inputs["settings"],
            "https://docs.example.test",
        )
    publication["image"] = DOCS_IMAGE + "@sha256:" + "b" * 64
    with pytest.raises(ValueError, match="HTTPS"):
        prepare_plan(
            selected,
            publication,
            source,
            inputs["suite_path"],
            inputs["settings"],
            "https://docs.example.test?sig=private",
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("uniqueEmbeddingInputs", 2001),
        ("embeddingInputBytes", 8000001),
        ("rawFloat32VectorBytes", 40000001),
        ("uniqueEmbeddingInputs", True),
        ("uniqueEmbeddingInputs", 0),
    ],
)
def test_budget_stops_work_before_embedding(field, value):
    plan = dict(uniqueEmbeddingInputs=10, embeddingInputBytes=100, rawFloat32VectorBytes=1000)
    enforce_budget(plan)
    plan[field] = value
    with pytest.raises(ValueError, match="budget"):
        enforce_budget(plan)


def test_matching_receipts_produce_only_the_target_terraform_images(release):
    inputs, *_ = release
    verified = verify_release(**inputs)
    receipt = handoff(
        inputs["plan"],
        verified,
        dict(
            image=API_IMAGE + "@sha256:" + "c" * 64,
            sourceCommit="a" * 40,
            releaseId=inputs["plan"]["releaseId"],
            scannedImageId="sha256:" + "d" * 64,
        ),
    )
    validate_receipt(inputs["plan"], receipt)
    assert set(receipt["terraform"]["app_images"]) == {"knowledge_api", "livedocs"}
    assert receipt["deploymentApplied"] is False
    assert "key" not in json.dumps(receipt).lower()
    receipt["terraform"]["app_images"]["products"] = "unexpected"
    with pytest.raises(ValueError, match="Terraform"):
        validate_receipt(inputs["plan"], receipt)


@pytest.mark.parametrize(
    "change",
    [
        "corpus",
        "manifest",
        "profile",
        "config",
        "index",
        "incomplete",
        "suite",
        "mode",
        "failed",
        "case",
        "duplicate",
        "checks",
    ],
)
def test_mismatched_or_failed_reports_cannot_authorize_publication(release, change):
    inputs, *_ = release
    if change == "corpus":
        inputs["corpus_path"].write_text(inputs["corpus_path"].read_text() + " ")
    elif change == "manifest":
        x = json.loads(inputs["ingestion_path"].read_text())
        x["manifestCommit"] = "b" * 40
        inputs["ingestion_path"].write_text(json.dumps(x))
    elif change == "profile":
        inputs["settings"].embedding_revision = "changed"
    elif change == "config":
        inputs["settings"].chat_deployment = "changed"
    elif change == "index":
        inputs["index"]["searchIndex"] = "different"
    elif change == "incomplete":
        inputs["index"]["uploadComplete"] = False
    elif change == "suite":
        inputs["suite_path"].write_text("{}")
    elif change == "mode":
        inputs["model"]["retrievalMode"] = "local_keyword"
    elif change == "failed":
        inputs["model"]["failed"] = 1
    elif change == "case":
        inputs["model"]["cases"][0]["id"] = "unrelated"
    elif change == "duplicate":
        inputs["retrieval"]["cases"][1] = inputs["retrieval"]["cases"][0]
    elif change == "checks":
        inputs["model"]["cases"][0]["checks"]["examples"] = False
    with pytest.raises(ValueError):
        verify_release(**inputs)
