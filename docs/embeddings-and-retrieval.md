# Embeddings and hybrid retrieval

The operator builds an index from one successful Task 2 import. The API uses that
index to find document IDs, then reads the original evidence from the same corpus
packaged in its image. Indexing never changes the running image or active corpus.

## Configure one matching profile

Use an existing Azure OpenAI v1 endpoint and a deployed **text-embedding-3** model.
The dimensions argument requires this model family or a compatible later model. The
code does not provision a model resource or silently choose another model or tier.
Configure the non-secret values in your local `.env`, following `.env.example`:

```dotenv
KA_SEARCH_ENDPOINT=https://YOUR-SERVICE.search.windows.net
KA_SEARCH_INDEX=knowledge-v1
KA_OPENAI_ENDPOINT=https://YOUR-RESOURCE.openai.azure.com/openai/v1/
KA_EMBEDDING_DEPLOYMENT=YOUR-EMBEDDING-DEPLOYMENT
KA_EMBEDDING_DIMENSIONS=1536
KA_EMBEDDING_REVISION=1
```

The endpoint, deployment name, dimensions and explicit revision define the embedding
profile. API keys and chat deployment are excluded. Increment the revision if the model
behind an unchanged deployment name changes. A different profile cannot reuse old
cache entries or retrieve old vectors. Keep the Azure deployment's actual model
version fixed while indexing and serving a profile.

Inject `KA_OPENAI_KEY` and an indexing **admin** Search key in `KA_SEARCH_KEY` through
your secret store, or use an authorized local Azure identity. The operator needs index
creation/read/write access. Runtime receives the existing read-only Search query key
from Terraform; it never constructs an index-management client. No credentials belong
in corpus/report files, git, image layers or Docker build arguments.

## Plan without spending on embeddings

Use the complete corpus and report produced by `knowledge ingest`. Changed bytes,
snapshot, manifest or document counts fail validation before cloud clients are created.
Synthetic corpora require `--allow-demo` and an isolated test index; do not mix demo
data into the production corpus.

```bash
uv run knowledge plan-index \
  --corpus artifacts/active-corpus.json \
  --ingestion-report artifacts/ingestion-report.json \
  --cache .cache/embeddings \
  --report artifacts/index-plan.json
```

This only reads local inputs/cache. Review `indexedChunks`, `sourceDocuments`,
`uniqueEmbeddingInputs`, `cachedEmbeddingInputs`, `newEmbeddingInputs` and
`rawFloat32VectorBytes`. The last value is a lower-level data size, **not** an Azure
capacity estimate. Storage statistics after indexing determine actual usage.

The current chunk policy preserves all text tails and Unicode boundaries. Each title
plus chunk is at most 6,000 UTF-8 bytes, with 256 bytes of boundary-aligned overlap.
This is a conservative byte budget for the supported byte-BPE embedding models,
rather than a token count estimate. Sixteen bounded inputs per request keep batches
small. Actual successful input-token usage is reported from the provider response.
No tokenizer download or new runtime dependency is required.

The ignored cache stores vectors, profile hashes and input hashes without raw text.
Only exact input text under the same embedding profile is reusable. It survives
snapshot changes when text stays identical. Corrupt vectors, wrong dimensions,
non-finite/zero vectors and missing/duplicate response indices stop the operation.

## Upload and verify completion

```bash
uv run knowledge index \
  --corpus artifacts/active-corpus.json \
  --ingestion-report artifacts/ingestion-report.json \
  --cache .cache/embeddings \
  --report artifacts/index-report.json
```

Index creation happens only if the named index is absent. An incompatible existing
schema, including the earlier prototype schema, fails before embedding/upload. Select
a fresh `KA_SEARCH_INDEX` when replacing it. The application does not delete old
indexes, alter existing capacity, change a SKU or select a paid fallback.

Rows contain bounded searchable text, title, kind, scope and source-document identity/
hash plus a hidden vector. Full `payloadJson`, duplicated component schemas, source
JSON and binary attachments are omitted. The authoritative corpus retains them.

Every batch result must acknowledge every expected row ID successfully. After upload,
the command polls the scoped visible chunk count for up to 30 attempts, one second
apart. If the count has not converged, it fails without publishing a marker; rerun
using the same inputs/cache. Successful chunks and embeddings can be reused.

Only after all chunks are visible does it upload a completion marker bound to the
exact corpus and profile. The report records completion, cache hits, new embeddings,
successful provider calls/tokens, and Azure index/storage statistics. Statistics are
eventual and include other retained snapshots. A statistics outage after a successful
marker upload records null sizes, rather than falsely reporting that upload failed.
The report is replaced only on success; check its corpus/profile identifiers before
using a report retained from an earlier run.

An interrupted new snapshot cannot become queryable through this API without its
matching marker. An already completed, identical corpus/profile remains usable during
an idempotent retry. Changing inputs creates different row and marker identities;
the previous snapshot is retained until you deliberately retire it.

## Activate separately through the existing deployment flow

Review the report, then build an API image containing that exact corpus using the
existing `CORPUS_FILE` build argument. Default CI images continue to contain synthetic
demo data. Do not switch a demo image to Azure mode expecting real documents to appear.

In the Terraform repository, match these settings to the successful index operation:

```hcl
knowledge_runtime_mode       = "azure"
knowledge_search_enabled     = true
knowledge_search_index       = "knowledge-v1" # Or the verified replacement index.
knowledge_embedding_revision = "1"
```

Match `knowledge_models` endpoint, deployment names and dimensions as well. Deploy the
reviewed real-corpus image through the existing Terraform plan/apply workflow and
Azure Storage backend. This indexing code does not run Terraform or replace state.
`/health/ready` continues to check local initialization, not Azure readiness. Each
Azure query checks for the exact completed corpus/profile marker before embedding.

## Verify retrieval before enabling chat synthesis

Locally, set `KA_CORPUS_PATH` to the indexed corpus, `KA_RETRIEVAL_MODE=azure` and
`KA_GENERATION_MODE=evidence`. Start the API normally. Query through the Web proxy or
the documented `POST /api/v1/query` endpoint with the catalog's snapshot ID. This tests
retrieval and original contract hydration without a chat-model call.

| Question | Inspect |
| --- | --- |
| How does mobile phone validation work? Give me the create request contract as JSON. | Products scope; contract path/schema copied from the corpus; published policies distinguished from enforcement evidence. |
| What happens when invoice PDF generation fails? | Invoices scope; expected scenarios distinguished from execution status; unresolved source links retained as limitations. |
| What happens when checkout times out? | Payments scope; checkout/retry sources, rather than unrelated product policies. |
| Ask for behavior absent from the selected service. | Inspect missing evidence; similarity retrieval is not proof that the feature exists. |

Hybrid requests combine text and vector search with prefilters for service, snapshot,
corpus hash and embedding profile. No semantic ranker is enabled. Multiple chunk hits
collapse to one source document. Every returned ID, kind, hash and scope must match
the local corpus; unknown or stale rows fail evidence verification. The index cannot
inject replacement contracts or payloads into an answer.

Bounded one-hop expansion follows explicit operation relationships to include original
contracts, linked policies and flows. It retains the top source hits and never crosses
the selected service/snapshot. A published standalone policy is not automatically
attached to an operation. Retrieval remains a bounded subset, not a full inventory.
The existing structured answer contract and Kiota client are unchanged.

## Validation scope and source references

Offline tests exercise SDK request shapes, vector validation/cache reuse, partial
uploads, visibility failures, schema compatibility, query scoping, stale/unknown IDs,
original contract hydration and the HTTP validation-before-contract response. They
use synthetic vectors; they do **not** measure model relevance or Azure permissions.

An offline plan for the three available pinned bundles audited in Task 2 produced
528 source documents, 631 chunks, 1,492,814 input bytes including overlap, and
3,876,864 raw float32 vector bytes at 1,536 dimensions. This is a partial-source sizing
check, not an indexed or activated four-service snapshot. Users' exact pinned ZIP
still requires private Blob access. No live Azure indexing/relevance test was run.

Primary implementation references:

- [Azure hybrid search and RRF](https://learn.microsoft.com/en-us/azure/search/hybrid-search-overview).
- [Azure push indexing and batch limits](https://learn.microsoft.com/en-us/azure/search/search-what-is-data-import).
- [Azure OpenAI v1 embeddings and dimensions/input limits](https://learn.microsoft.com/en-us/rest/api/microsoft-foundry/azureopenai/embeddings).
