# Documentation refresh and release handoff

The API repository owns **Refresh Knowledge**. It produces a tested image containing
one verified LiveDocs corpus and an immutable handoff for ECommerceStore.Terraform.
The workflow does not apply Terraform, switch ACA revisions, change the existing
Azure Storage backend, or delete source archives/old index scopes.

## Delivery sequence

1. Require successful main CI for the exact API commit. Select a successful LiveDocs
   main CI publication, or verify an explicitly supplied full LiveDocs commit.
2. Download the exact `livedocs-image` artifact by ID. Match its immutable Docker
   digest/commit to a sparse checkout of the published manifests. Record Git-object
   file hashes, suite hash, model/index configuration and a release fingerprint.
3. Reuse a matching cached successful handoff when its immutable image is pullable.
   Otherwise require all refresh credentials before downloading or indexing anything.
4. Ingest every selected archive with existing identity/checksum/inventory validation.
   Missing Users or other archives block the complete candidate; services are not omitted.
5. Require all 25 reviewed Products cases in local/evidence mode. A changed Products
   publication needs a reviewed suite/pin update; automation cannot silently approve it.
6. Plan and enforce input/vector budgets, then complete indexing. A valid existing
   completion marker plus visible chunk count avoids repeating corpus embedding calls,
   even with an empty local embedding cache. Corrupt markers fail instead of authorizing reuse.
7. Require the 25 cases against real hybrid retrieval in evidence mode using a query
   key, followed by the combined Products case (`products-01`) in model mode. Its
   requested JSON example must be available and schema-checked.
8. Verify reports agree on the corpus, published manifests, profile, configuration and
   case identities. Build one image, compare its baked corpus bytes with the verified
   input, check health/auth and a sourced answer, then scan it with Trivy.
9. Publish those tested/scanned bytes under `refresh-RUN_ID-ATTEMPT`; emit the digest
   in `knowledge-refresh-handoff/release.json`. Refresh does not overwrite `latest`
   or the normal commit tag used by synthetic baseline CI.

Every stage is sequential. A failed stage stops the candidate and does not activate it.
An index failure can leave incomplete new chunks; existing corpus/profile scopes remain
available. The pipeline does not clean up or promote a partially indexed snapshot.

## Setup in the API repository

Existing repository secrets `DOCKER_USER` and `DOCKER_TOKEN` are reused. No additional
GitHub write token or LiveDocs repository change is needed. For the new environment
**`knowledge-refresh`**, configure these secrets:

| Secret | Use |
| --- | --- |
| `LIVEDOCS_STORAGE_SAS_TOKEN` | Read-only container SAS for the exact archive URLs, like existing LiveDocs CI. |
| `KA_SEARCH_ADMIN_KEY` | Index management/uploads in the indexing step only. |
| `KA_SEARCH_QUERY_KEY` | Read-only Search access for live evaluations. |
| `KA_OPENAI_KEY` | Existing Azure OpenAI resource for embeddings and the core answer check. |
| `LIVEDOCS_READ_TOKEN` | Optional: needed if repository visibility/permissions prevent the default token reading publications. Give read-only Contents/Actions access to API and LiveDocs. |

The workflow uses the key/SAS path for this small portfolio deployment. Keys are
passed to their individual steps through environment variables, never Docker build
arguments, image configuration, reports or browser assets. The runtime query key
remains managed separately by Terraform. The refresh admin key is not a runtime key.

Configure these repository/environment **variables** to match the existing Terraform
configuration and actual model deployments:

| Variable | Value |
| --- | --- |
| `KA_SEARCH_ENDPOINT` | Exact `knowledge_search_endpoint` output from the existing Terraform root. |
| `KA_SEARCH_INDEX` | Same as `knowledge_search_index`; default `knowledge-v1`. |
| `KA_OPENAI_ENDPOINT` | Existing Azure OpenAI v1 base URL, matching `knowledge_models.endpoint`. |
| `KA_EMBEDDING_DEPLOYMENT` | Matching deployed embedding model name. |
| `KA_EMBEDDING_DIMENSIONS` | Matching dimensions; default 1536. |
| `KA_EMBEDDING_REVISION` | Matching profile revision; default `1`. |
| `KA_CHAT_DEPLOYMENT` | Matching chat deployment name. |
| `KA_LIVEDOCS_BASE_URL` | Public HTTPS LiveDocs host, without credentials/query/fragment. |
| `KA_REFRESH_REVISION` | Default `1`; increment to invalidate a handoff after operational/model changes behind unchanged names. |

No model resource or paid Search tier is created by this workflow. Existing Terraform
still owns the optional Free Search service and the shared ACA hosts.

## Run and enable automatic refresh

Merge the PR and wait for successful API main CI. From **Actions → Refresh Knowledge →
Run workflow**, select `main`. Leave `livedocs_commit` empty for the newest successful
LiveDocs main publication, or enter its full 40-character commit. An unpublished ref,
PR/fork publication, expired receipt or mismatched image is rejected. Missing/expired
publication artifacts require a successful LiveDocs republish before retrying.

Set `force_rebuild` only to rerun evaluation/build for identical inputs or replace a
missing candidate image. A forced run does not overwrite the existing cache entry;
its new digest is in that run's handoff. Increment `KA_REFRESH_REVISION` for lasting
configuration changes that should have a new cached identity.

To enable daily checks, set the **repository-level** variable
`KNOWLEDGE_REFRESH_ENABLED=true`. Until then scheduled jobs are skipped; manual main
runs remain available. The cron corresponds to 04:17 Warsaw winter time / 05:17 summer
time. Scheduling is daily polling of successful publications, not an immediate cross-repo
webhook. GitHub can delay or disable inactive scheduled workflows; manual runs use the
same verification path. Main-only execution and serialized refresh concurrency keep
PR secrets and concurrent uploads out of this delivery path.

An unchanged fingerprint restores only a metadata receipt, verifies image availability,
and emits the same handoff without Azure/model calls. Cache retention is best-effort;
a cache miss repeats evaluation/build, but a valid completed index avoids repeating
corpus embeddings. Failed partial runs may repeat their embedding work on retry.

GitHub behavior is described in [workflow events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
and [artifact access](https://docs.github.com/en/rest/actions/artifacts). Source selection
is limited to the two named repositories and their existing `ci.yml` publication workflows.

## Budgets and retained data

| Variable | Default ceiling per planned corpus |
| --- | --- |
| `KA_REFRESH_MAX_INPUTS` | 2,000 unique embedding inputs |
| `KA_REFRESH_MAX_INPUT_BYTES` | 8,000,000 UTF-8 input bytes, including repeated chunk inputs |
| `KA_REFRESH_MAX_VECTOR_BYTES` | 40,000,000 raw float32 vector bytes |

These are workload guards, not price estimates or a guarantee that the index fits the
50 MB Free Search quota. Azure index overhead and retained snapshots also consume
capacity. Review the plan/storage reports before increasing limits. No automatic tier
upgrade, old-snapshot deletion or index-name switch occurs. The 25 retrieval cases make
query embeddings, and the core model case makes another query embedding/chat request;
existing provider SDK retry behavior can add attempts. Indexing records measured
embedding usage; chat cost/usage monitoring belongs to public-demo readiness.

Only provenance/planning/index/evaluation metadata is uploaded as
`knowledge-refresh-reports` (14 days). The handoff is retained for 30 days. Source ZIPs,
embedding cache and raw corpus stay on the disposable runner; the real corpus is baked
into the published API image. Like the existing public LiveDocs image, that image exposes
its documentation data to anyone who can pull it. Use appropriate private registry/artifact
access if that documentation must be private. No generated reports or source bundles
are committed to Git.

The current reviewed suite pins the October Products publication. Unresolved relationship
warnings remain in the ingestion report and supported answers disclose limitations;
archive integrity failures always stop ingestion. Evaluation is a factual/lexical gate,
not proof of every generated sentence. Inspect the model answer before public presentation.

## Terraform handoff and rollback

Download `knowledge-refresh-handoff/release.json`. It records API/LiveDocs source
commits and image digests, corpus/manifest/profile hashes, source pins, warning count,
model/index settings and `deploymentApplied:false`.

In your existing ECommerceStore.Terraform checkout:

1. Preserve the current handoff, image references and settings for rollback.
2. Merge `terraform.app_images.knowledge_api` and `terraform.app_images.livedocs` from
   the receipt into the existing `app_images` map; preserve all other app entries.
   `release.json` is a handoff envelope, **not a replacement `.tfvars` file**.
3. Match `knowledge_runtime_mode`, `knowledge_search_index`,
   `knowledge_embedding_revision` and `knowledge_models` to the receipt. Confirm the
   receipt's Search endpoint equals the root output. Keep secrets in existing local
   inputs/`TF_VAR_*`; the receipt never contains them.
4. Use the existing Azure Storage backend and inspect a fresh plan:

```bash
git pull
terraform init
terraform validate
terraform plan -out=knowledge-refresh.tfplan
terraform show knowledge-refresh.tfplan
# After reviewing this exact plan:
terraform apply knowledge-refresh.tfplan
```

The root continues using `stecomtfstatemb261011/tfstate` and key
`ecommerce-store/simple-dev.terraform.tfstate`. Do not create a second state/root or
migrate the backend for this refresh. Infrastructure changes or unrelated replacements
in the plan need investigation before applying.

After applying, request `/api/v1/catalog` through the Knowledge Assistant Web host.
Its active snapshot must match the receipt, with `isDemo:false`, `azure_hybrid` retrieval
and `model` answers. Verify the matching LiveDocs host's `/build-info.json` commit and
inspect the combined mobile-phone answer's rules, contract, citations and limits. ACA
liveness/readiness alone does not establish Search/model availability or answer quality.

For rollback, restore both prior image digests and their matching model/index/profile
settings through the same Terraform root. Keep the old index/corpus scope available
until rollback is no longer needed. No refresh step removes that data or applies state.

## Verification and remaining work

Normal CI validates workflow syntax with checksum-pinned actionlint 1.7.12 and exercises
publication selection, source/profile/report mismatches, budgets, completed-index reuse,
cache handoffs and publication guards. Docker CI checks the exact baked demo corpus,
then a separate synthetic non-demo fixture image exercises the refresh smoke path.
That fixture is never published; the published baseline image is built once and unchanged.

The live refresh workflow is not run by PR CI and needs the environment settings above.
Green unit/container checks do not establish successful Azure indexing or live model quality.
The final public-demo task remains: request limits, latency/token visibility and a reviewed
presentation against the deployed release.
