# E-Commerce Knowledge Assistant API

Python/FastAPI service that answers developer questions using one immutable snapshot
of executable LiveDocs. Answers contain numbered explanation, validation, API contract
and scenario sections with source paths, source commits and documentation links.

## Run locally

Requires Python 3.12+ and uv. From this repository:

```bash
uv sync --locked
uv run uvicorn knowledge_assistant.main:app --host 127.0.0.1 --port 8080
```

The default is **synthetic demonstration data**, local keyword retrieval and an
extracted evidence view. It does not call a model or describe the deployed store.
The UI labels these modes. Azure hybrid retrieval and model synthesis are explicit
configuration choices; upstream failures never silently fall back to demo answers.

`GET /api/v1/catalog` lists the active snapshot and available services.
`POST /api/v1/query` accepts `question`, `service` and `snapshotId`.
API documentation is available at `/docs`. `/health/live` checks the host;
`/health/ready` checks corpus initialization, not Azure connectivity.

## Ingest the exact LiveDocs snapshot

Obtain `manifests/portal.json` from the exact LiveDocs commit selected for deployment.
Prefer a local copy from that checkout; a producer uploading a newer archive does
not change the deployed documentation snapshot.

```bash
uv run knowledge ingest \
  --manifest /path/to/ECommerceStore.LiveDocs/manifests/portal.json \
  --projects /path/to/ECommerceStore.LiveDocs/manifests/projects.json \
  --manifest-commit FULL_LIVEDOCS_COMMIT_SHA \
  --cache .cache/livedocs --download \
  --livedocs-base-url https://YOUR-LIVEDOCS-HOST \
  --output artifacts/active-corpus.json \
  --report artifacts/ingestion-report.json
```

The downloader uses Azure credentials (`az login` locally) or the existing
`LIVEDOCS_STORAGE_SAS_TOKEN` environment variable. Credentials never belong in the
manifest, URLs, source files or Docker build arguments. It downloads exact immutable
archive references, validates byte counts, SHA-256 hashes, declared inventory,
identity and safe ZIP paths, and reads without extracting the archive.

The ingestion preserves all compiled Gherkin examples, backgrounds and data tables;
OpenAPI operations; validation policies; flows; operation links; and Allure result
metadata/attachment references. Large binary attachments remain in the archive.
Local OpenAPI references are resolved; external references must be bundled first.
Failed executions remain execution evidence, distinct from expected BDD behavior.
A development version such as `v1` gets a new snapshot ID when its selected inputs change.

The report records per-service source commits, archive and source-file hashes, policy/flow
counts, expanded scenarios, execution statuses, exact execution matches and unresolved
operation links. Individual descriptors are adapted from each producer's source format;
published standalone policies are distinguished from policies explicitly linked to an
operation. An execution passing does not establish that every published policy runs.
`--strict-links` rejects any reconciliation warning. Failed ingestion leaves the prior
corpus/report untouched. See [the ingestion runbook and pinned-source audit](docs/livedocs-ingestion.md).

## Azure Search and model synthesis

Copy `.env.example` to `.env` locally and fill the commented Azure settings. Set:

- `KA_RETRIEVAL_MODE=azure` and `KA_GENERATION_MODE=azure`;
- Search endpoint, index name and a **query key** for API runtime;
- Azure OpenAI **v1** base URL and deployed embedding/chat model names;
- the embedding dimension used by that deployment; an Azure OpenAI key or Entra identity;
- `KA_CORPUS_PATH=artifacts/active-corpus.json` and a nonempty `KA_API_KEY` when exposed.

For ingestion, use a Search **admin key** or an identity with index-management/data
write permissions. The API uses a read-only query key. Run indexing separately:

```bash
uv run knowledge plan-index \
  --corpus artifacts/active-corpus.json \
  --ingestion-report artifacts/ingestion-report.json
uv run knowledge index \
  --corpus artifacts/active-corpus.json \
  --ingestion-report artifacts/ingestion-report.json
```

Planning makes no cloud calls and reports chunk counts, cache reuse and raw vector bytes.
Indexing verifies the corpus against its successful ingestion report, creates a missing
index or validates an existing schema, embeds bounded UTF-8 chunks with overlap, reuses
successful embeddings from the local cache, and reports measured Azure storage statistics.
It refuses incompatible existing indexes instead of updating them in place. Search stores
compact text and identities; source JSON and full contracts remain in the reviewed corpus.
The completion marker is published only after every chunk upload succeeds and is visible.
Runtime requires this marker and uses the query key, with no index-management client.
Exhaustive cosine vector search keeps the first small corpus simple. Hybrid queries
combine full-text and vector retrieval, with service, snapshot and embedding-profile
filters. Changing the embedding model or dimensions requires reindexing and a new
index name if the vector schema changes. Do not update a deployment's model in place
without reindexing the entire corpus. Increment `KA_EMBEDDING_REVISION` when the model
behind the same deployment name changes. Match the revision and index name in Terraform.
The application validates every retrieved identity/hash and hydrates original documents
from its local corpus. Bounded expansion follows explicit same-service operation links
to include contracts, policies and flows. It never falls back to index-supplied payloads.
See [the indexing and retrieval runbook](docs/embeddings-and-retrieval.md).

Statistics are eventually consistent. The 50 MB Free Search capacity applies to
index storage, not archive ZIP size or exported JSON bytes. Activation is a separate
step: only point a running API/image to the corpus after the entire upload succeeds.
Failed batches stop publication; no automatic paid tier or active-snapshot switch.

The model produces a Pydantic-validated section plan referencing retrieved document
IDs and documented rule IDs. It does not author validation rules or replace contract
JSON: the server hydrates both from the original evidence and enforces validation before
contract sections. Multiple selected policies share one validation section with per-rule
citations. Missing parts remain explicit partial answers. Published standalone policies
are distinguished from operation links and runtime execution evidence.

Model input is a bounded catalog, rather than repeated full payloads. Unsupported rule,
source or document references fail verification. Refused, incomplete and unparseable
model responses use safe Problem Details without falling back to demo data.
Documented/generated examples are checked offline against their request schemas and
local `referenceSchemas`; failing generated examples are omitted. JSON `null` examples
retain their documented/generated origin. Schema validity does not establish business-rule
compliance or successful API execution. See [structured answers](docs/structured-answers.md).

## Layers

The package follows the boundaries used by `ECommerceStoreUsers`, adapted to Python:

| Folder | Responsibility |
| --- | --- |
| `api/` | Thin endpoints, authentication, Problem Details, OpenAPI and composition root. |
| `application/` | Knowledge service, retrieval/planning ports, DTOs, evidence planning and answer assembly. |
| `domain/` | Source/document/corpus models, text rules and evidence verification failure. |
| `infrastructure/` | LiveDocs ZIP/blob ingestion, local/Azure retrievers, model client and settings. |

Application and Domain never import FastAPI, Azure or OpenAI SDKs. Adapters implement
application ports; `api/app.py` supplies them to `KnowledgeService`. The CLI composes
indexing/ingestion separately. `main.py` remains the ASGI entry point. Tests are
organized by layer, with an import boundary check and HTTP contract tests.
See [the architecture decision](docs/adr/0001-layered-knowledge-service.md).

## Errors

All controlled API errors, binding failures, missing routes and unexpected failures
use `application/problem+json` with the same nine-field store contract. Clients branch
on `code`, and use `traceId` (also `X-Request-ID`) for log correlation. Binding errors
return 400 instead of FastAPI's default 422. Provider outages and failed evidence
verification return safe 503 responses; unexpected bugs return 500. Raw provider/model
messages, request bodies and query values are never included in public problems.
See [the problem contract](docs/api-problem-contract.md).

## Frontend contract

`contracts/openapi.json` is generated from the actual FastAPI routes, typed catalog,
query/answer DTOs and explicit Problem Details responses. The answer JSON Schema
is also exported for standalone consumers:

```bash
uv run knowledge export-contract
```

The Angular repository vendors the reviewed OpenAPI artifact, pins its upstream commit
and SHA-256, and generates the real TypeScript Kiota client. Generated clients belong
to the Web repository. Endpoints have stable operation IDs; the generated request
builders include error mappings for `application/problem+json`. CI rejects OpenAPI drift.
When changing the response, update both snapshots and the schema version for a
breaking change. Responses distinguish `answered`, `partial`, `not_found`,
`evidence`/`model`, `local_keyword`/`azure_hybrid` and `isDemo`.

## Containers and infrastructure

```bash
docker build -t knowledge-assistant-api .
# For real documentation, after successful indexing:
docker build --build-arg CORPUS_FILE=artifacts/active-corpus.json -t knowledge-assistant-api .
```

Default images contain clearly labelled synthetic data. Production images must be
built with the indexed corpus. The image runs as UID 10001 on port 8080. Runtime
keys are injected through environment secrets. The Web Nginx proxy sends `X-API-Key`;
the browser receives no model/Search/API credentials.

`ECommerceStore.Terraform` owns the optional Free Search service and API/Web hosts
in the existing ACA environment. This repository owns schema/content and models;
Terraform never embeds documents. Model deployments are supplied separately.

## Verification

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest -q
```

Tests exercise real ZIPs/Gherkin parsing, corrupted archives, outline expansion,
snapshot changes, API scope/auth, citation rejection, exact contract hydration,
invalid generated examples and the exported response schema. CI repeats these
checks and builds the container. Live Azure calls require deployment credentials
and are not represented as having passed by offline tests.

The first slice supports business explanations and request contracts. Exhaustive
cross-service counts, binary attachment retrieval and multi-turn conversations are
separate work. Top-K similarity results must never be presented as a complete inventory.

## CI and Docker publication

CI follows the storefront Web and Users pattern: dependency review on pull requests,
repository-history secret scanning, source/build/contract checks, separate test jobs,
and a required quality gate. The image job builds once, smoke-tests and scans that
local image for fixable high/critical vulnerabilities, then publishes the same image
after a successful push to `main`. Pull requests build and scan without Docker Hub login
or publishing. Test results are uploaded as JUnit artifacts, including on failures.

Enable **Dependency graph** in this repository's **Settings → Advanced Security →
Supply chain security** before running the PR checks. GitHub dependency review requires
that setting; the quality gate fails if dependency review is unavailable or fails.

Configure these existing repository Actions secrets:

- `DOCKER_USER`: Docker Hub account with push access to `mb0101/ecommerce-store-knowledge-assistant-api`.
- `DOCKER_TOKEN`: that account's Docker Hub access token.

Successful main runs publish `mb0101/ecommerce-store-knowledge-assistant-api:<full-commit-sha>`
and `:latest`, verify both push digests match, and attach `image-reference.json` in the
`image-reference` workflow artifact. The artifact records the immutable
`docker.io/mb0101/ecommerce-store-knowledge-assistant-api@sha256:...` reference,
source commit and scanned image ID. Use the digest reference for deployment.

Add its `knowledge_api` entry to your existing Terraform `app_images` map, preserving
all other app entries. Run plan/apply from `ECommerceStore.Terraform` with its existing
Azure Storage backend and deployment variables. This application CI owns image
publication; Azure credentials, infrastructure deployment and state remain in that
Terraform checkout.

The published baseline API image includes the labelled synthetic demo corpus and its OCI description states that explicitly. Its container smoke test uses local/evidence mode and checks health, API-key enforcement and a sourced synthetic query. The current Terraform configuration runs Azure mode, so Search/model settings are still required for hosting. Real LiveDocs ingestion/indexing and building an image with the matching real corpus are subsequent work; this publishing pipeline does not load deployment credentials or bundle unreviewed live data.

The same local CI entry points are available in `scripts/ci.sh`.

