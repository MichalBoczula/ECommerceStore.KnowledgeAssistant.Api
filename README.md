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
  --cache .cache/livedocs --download \
  --livedocs-base-url https://YOUR-LIVEDOCS-HOST \
  --output artifacts/active-corpus.json
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
uv run knowledge index --corpus artifacts/active-corpus.json
```

The command creates the versioned index schema, embeds bounded chunks without
omitting text tails, uploads documents and reports Azure storage statistics.
Exhaustive cosine vector search keeps the first small corpus simple. Hybrid queries
combine full-text and vector retrieval, with service, snapshot and embedding-profile
filters. Changing the embedding model or dimensions requires reindexing and a new
index name if the vector schema changes. Do not update a deployment's model in place
without reindexing the entire corpus.

Statistics are eventually consistent. The 50 MB Free Search capacity applies to
index storage, not archive ZIP size or exported JSON bytes. Activation is a separate
step: only point a running API/image to the corpus after the entire upload succeeds.
Failed batches stop publication; no automatic paid tier or active-snapshot switch.

The model produces a Pydantic-validated section plan referencing retrieved document
IDs. The server rejects unknown citations and hydrates contracts from the original
OpenAPI artifact. Documented/generated examples are schema checked; a failing generated
example is omitted. Schema validation does not prove all business rules or API execution.
Recursive schemas retain references plus `referenceSchemas` for validation.

## Frontend contract

`contracts/answer.schema.json` is generated from the Python models:

```bash
uv run knowledge export-contract
```

The Angular repository vendors this schema and generates its TypeScript types.
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
