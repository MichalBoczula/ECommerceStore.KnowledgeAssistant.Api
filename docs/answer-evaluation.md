# Answer evaluation

`knowledge evaluate` runs reviewed questions through the same `KnowledgeService`,
retriever, planner and assembler used by the API. Application assertions have no
HTTP/provider dependencies; the command composes adapters and publishes reports.
There is no model judge, new database, dependency or HTTP contract.

## Two datasets

| Set | Purpose | Source |
| --- | --- | --- |
| `evaluation/demo-smoke.json` | Four deterministic CI regression checks. | Existing synthetic demo corpus; explicit `--allow-demo`. |
| `evaluation/products-v1.json` | 25 source-reviewed Products questions and expected facts. | Exact Products commit `71f8a59f1d2cee84a90247de305901c6cb5dc7a6`, archive SHA-256 `3778159f7c3579610bfb0fd76c0068dbe11e7e7e547c0c2a7fe251ab3d548c53`. |

The Products questions cover the combined phone-validation/create request, eight phone
rules, eleven published hardware rules, amount/pagination/filter policies, nine API
operations, four flows, an unsupported contract and an unrelated question. Expectations
were checked against the original policy messages, OpenAPI operations and flow steps;
they are not recorded model answers. The hardware policy is published without an
explicit operation link: those answers must disclose that limitation. No hardware or
money validation is inferred from the create flow.

The suite uses the **9 November Products publication** selected by LiveDocs PR #14.
All 25 questions passed in local evidence mode with 100% expected-source recall on the
complete four-service snapshot `v1-838bfd35950d4a1a1a0b`. Updating the Products commit or
archive requires reviewing the suite pins and expectations. Each query stays within its
selected service and snapshot.

## Offline use

From the repository checkout:

```bash
uv run knowledge evaluate \
  --suite evaluation/demo-smoke.json --corpus examples/demo-corpus.json \
  --allow-demo --report artifacts/evaluation/demo.json \
  --summary artifacts/evaluation/demo.md

# After verified LiveDocs ingestion; no Azure calls in this mode:
uv run knowledge evaluate \
  --suite evaluation/products-v1.json \
  --corpus artifacts/active-corpus.json \
  --ingestion-report artifacts/ingestion-report.json \
  --report artifacts/evaluation/products-local.json \
  --summary artifacts/evaluation/products-local.md
```

Both mode options default explicitly to `local` / `evidence`, even if `.env` selects
Azure for hosting. Real corpora require their successful ingestion report. Demo data
requires opt-in and cannot be substituted for a real suite. Setup validates corpus
integrity, source pins and required source paths before constructing cloud clients.
Input/output collisions are rejected. Exit codes: **0** all cases pass; **1** one or
more cases fail (report still written); **2** invalid inputs/configuration.

## Azure evaluation

After indexing the exact corpus and configuring existing Search/OpenAI settings:

```bash
# First inspect a single case to keep provider work small:
uv run knowledge evaluate \
  --suite evaluation/products-v1.json \
  --corpus artifacts/active-corpus.json \
  --ingestion-report artifacts/ingestion-report.json \
  --retrieval-mode azure --generation-mode azure --case-id products-01 \
  --report artifacts/evaluation/products-azure-one.json
```

Repeat `--case-id` to select a subset; omit it to run all 25 sequentially. Unknown IDs
fail before provider construction. Selecting Azure is explicit; it uses the existing
read-only retriever/completion-marker checks and model planner. This command never
creates an index, embeds the corpus or activates a deployment. Provider SDK retries
remain governed by their existing client configuration. A full run can incur query,
embedding and chat costs; report latency is measured, token usage/cost is not estimated.

A request-body example explicitly requested by a model case must be available and
schema-checked. The same case in evidence mode permits an honest partial answer when
no example is documented. Missing an example cannot be hidden by inventing JSON.

## What is checked

Every result checks service/snapshot/demo scope, cited evidence from the actual retrieved
set, original rule field/text/source triples, original method/path/operation/media type,
request schema and local reference definitions. Example origin and schema-validation
claims are checked against their original sources. Documented examples cannot be
silently replaced or omitted. Generated examples must validate offline; no remote
schema resolution or API execution is performed.

Each question adds expected status, section types/order, exact rule sets when specified,
required/allowed source paths, operation identity, text sentinels and evidence limits.
Expected-source recall measures required paths present **after bounded expansion** within
`maxCandidates`; it is not recall over every potentially relevant document. Explainable
failures distinguish retrieval, citations, rules, contracts, examples and other checks.
Query/provider exceptions produce a failed case without exporting exception messages.

JSON reports include suite/file hashes, corpus hash, source pins, document count,
modes, configured candidate limit, per-case result, source paths, failure keys and
latency. They omit question/answer bodies, API credentials and provider messages.
Keep reports with private source paths in an appropriate private location. Markdown
summaries show the per-case outcomes. CI uploads the synthetic report and makes its
four cases mandatory alongside existing layer/HTTP checks and Docker verification.

These assertions are factual/lexical sentinels, not a semantic truth judge. A paraphrased
flow explanation can fail a text sentinel and needs review. A known citation alone does
not establish the relevance or completeness of every sentence. Review model answers
for relevance, unsupported claims, missing facts and understandable presentation before
public demonstration. Offline success does not establish Azure/model quality.

## Measured Products baseline

[The recorded comparison](../evaluation/baselines/products-local-v1.json) used all
**189 documents** from the 26 October Products archive, with the same 25 questions and
candidate limit 12 before and after these changes. The baseline file preserves that
historical suite's pins:

| Mode | Before | After |
| --- | --- | --- |
| Local keyword + evidence | 11/25 pass | 25/25 pass |
| Expected-source recall after expansion | 84% | 100% |
| Azure retrieval / model synthesis | Not run | Not run |

The before implementation is API main commit `405530e733f005ea986ac2ff411f5be7e7ef49e1`.
The comparison uses a Products-only manifest derived from the existing audited portal
reference, yielding snapshot `v1-224872e33a099f7f2aa9`. It is not the complete deployed
four-service portal; Users' pinned archive is still an independent ingestion prerequisite.
The raw archive/corpus and full answers are not committed. To reproduce, preserve the
same Products reference in a Products-only copy of the portal manifest, ingest it with
the existing registry and exact cached ZIP, then run the command above. The original
full portal snapshot can also be evaluated when all four pinned bundles are available.

The measured fixes tokenize CamelCase operation/policy names while preserving whole
identifiers, normalize a small English action vocabulary, keep formatting words from
being the only retrieval signal, prioritize requested structured evidence, and reuse
bounded explicit relationship expansion. Evidence planning selects relevant named or
operation-linked policies, preserves explicitly requested flows, resolves exact operation
IDs and abstains when an operation lacks positive entity evidence or remains ambiguous.
This improves the small English evidence view; it does not promise general multilingual
or semantic retrieval. Azure retrieval/index contents are unchanged.

Next validation is the same Products suite against the indexed corpus in Azure/model
mode, followed by human review of those answers. Documentation refresh and public-demo
readiness follow that quality check.
