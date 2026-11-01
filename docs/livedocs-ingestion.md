# LiveDocs ingestion

This step builds a local corpus and an audit report. It does not create embeddings,
upload an Azure index, replace the running corpus, build a production image or deploy.
Application CI continues to use synthetic demo data.

## Select one exact snapshot

Use the LiveDocs commit selected for deployment. Copy both `manifests/portal.json` and
`manifests/projects.json` from that checkout. Do not select the newest producer ZIP or
substitute a newer producer commit: that would describe different business behavior.
The supplied `--manifest-commit` is recorded as an operator assertion; the importer
checks the manifest contents and bundle identities, rather than contacting GitHub.

The default selects `latestVersion`; pass `--version v1` to select an explicit version.
Every project in that version is required. There is no partial-success corpus.

## Read the private Blob container

Run from an authenticated development environment, for example Azure Cloud Shell in
Bash. With `az login`, the logged-in identity needs **Storage Blob Data Reader** for
the existing `livedocs` container or a containing scope. Management-plane access or
seeing the storage account in Portal alone does not grant blob data access.

Alternatively inject `LIVEDOCS_STORAGE_SAS_TOKEN` from your secret store using a
read-only SAS scoped to this container. Do not paste it into chat, source files, CLI
arguments, manifest URLs, Docker build arguments or committed `.env` files.
Only exact credential-free HTTPS Azure Blob URLs under `/livedocs/reports/` are accepted.
No storage key or new storage account is needed for the importer.

```bash
uv sync --locked
uv run knowledge ingest \
  --manifest /path/to/ECommerceStore.LiveDocs/manifests/portal.json \
  --projects /path/to/ECommerceStore.LiveDocs/manifests/projects.json \
  --manifest-commit FULL_LIVEDOCS_COMMIT_SHA \
  --version v1 \
  --cache .cache/livedocs --download \
  --livedocs-base-url https://YOUR-LIVEDOCS-HOST \
  --output artifacts/active-corpus.json \
  --report artifacts/ingestion-report.json
```

For an offline rerun, omit `--download` and keep each original ZIP at
`<cache>/<manifest-artifact-sha256>.zip`. Reusing the exact manifest and cache produces
the same corpus and report bytes. The report's `corpusSha256` binds it to the output.
Files are staged after the entire import succeeds and individually replaced atomically;
consumers must check that hash if an interrupted filesystem publication is suspected.

Invalid JSON, duplicate members, invalid manifest/bundle schemas, unregistered producers,
identity/size/hash mismatches, missing or undeclared files, unsafe ZIP members, missing
Allure attachments and invalid source structures stop the import. It reads ZIP entries
without extraction. Provider errors return a safe failure message without credentials.
The vendored portal and bundle v1 JSON Schemas are copied unchanged from
`ECommerceStore.LiveDocs` commit `2f2ca78bc76ac3ae4182ccba7585bde868f6aad1`.

Review `services[].warnings`, `scenariosWithExecution`, `unmatchedExecutions`,
`executionStatuses` and the source identities before using the corpus. Use
`--strict-links` when the selected producers have no unresolved or ambiguous links;
this refuses publication on any reconciliation warning. Correct the producer sources
and publish a new LiveDocs snapshot to resolve source defects. Do not edit a pinned ZIP.
Failed imports do not overwrite a prior corpus or report. All ZIPs, corpora, reports and
attachments stay outside git; the repository ignores cache and artifact directories.

## Source adapters and evidence

| Input | Import behavior |
| --- | --- |
| Gherkin | Compile each example row, background, Rule and data table; retain feature, template, row values, line and explicit scenario IDs. |
| Allure | Keep execution metadata and failed/skipped results; verify nested result/container attachments without embedding binary bodies. |
| OpenAPI | One document per operation; keep the original operation/schema and local referenced components alongside the resolved request schema. Missing examples stay missing. External references must be bundled. |
| Products policies/flows | Adapt the published arrays and nested rule messages. |
| Users/Invoices descriptors | Unwrap named policy/flow descriptors, including multiple flow descriptors in one map. |
| Payments catalogs | Keep each source-qualified policy, condition and operation flow. |
| Operation links | Resolve explicit operation, policy, flow and scenario references; keep registry metadata and standalone policies. Feature-only references retain feature granularity. |

Execution matching uses an explicit scenario ID when supplied, or exact scenario/template
name and feature. Outline parameters disambiguate example rows. It never matches by ZIP
order, status, fuzzy names or approximate step text. Ambiguous/unmatched results remain
in the corpus and report. Policy/flow references require exact published identities or
aliases. A registered policy is `published_only` until an explicit operation link refers
to it; `referenced` is a documentation relationship, not proof of runtime enforcement.

Every document carries the producer repository, commit, workflow run, source-file and
archive hashes. Source URLs point to the actual static source file under the selected
LiveDocs version. Raw Allure JSON is linked directly; no UI test fragment is guessed.

## Pinned-source audit on 2026-11-01

The inspected manifest is LiveDocs commit
`2f2ca78bc76ac3ae4182ccba7585bde868f6aad1`. It still selects October 25–26 producer
bundles, rather than the newer producer main branches. This is a source audit, not
verification of the LiveDocs image currently deployed in Azure.

Original GitHub Actions `documentation-bundle` ZIPs for the following three references
matched the portal's exact byte sizes and SHA-256 hashes. No archive contents or raw
business payloads are included here.

| Service | Producer commit | Expanded scenarios | Allure results matched | Policies / rules | Flows | OpenAPI operations | Warnings |
| --- | --- | ---: | ---: | --- | ---: | ---: | --- |
| Products | `ab8fe05e2fee4870c6b1255a8f4d6e5d49d90f5e` | 76 | 76 | 5 / 26 | 9 | 11 | 0 |
| Invoices | `43af2ef1720b3b66b0a5f7392d5321b4de5b7d87` | 77 | 77 | 8 / 33 | 15 | 15 | 9 unresolved flow references |
| Payments | `ab868c98940741ef6422dfafcab71134f3e4b8e3` | 51 | 51 | 8 / 8 | 7 | 7 | 0 |

All 204 imported executions in these bundles report `passed`. This is evidence from
their pinned runs, not a new run of the business tests. Invoices' nine operation links
refer to descriptor names absent from that bundle; the importer retains those references
and warns instead of choosing another flow.

Users is pinned to `112c4be06a79ef1f0653e1e2d8d6de0538e7d611`. Its documentation ZIP
has expired from GitHub Actions, so the complete four-service snapshot has **not** been
imported here. The retained `generated-contract` artifact from run `40210725093`
was checked separately: 6 policies, 36 documented rule messages, 12 flows and 14 operation
links. This validates adapter compatibility only; it does not validate the unavailable
Users bundle inventory or BDD/Allure evidence. Fetch the exact pinned Users ZIP from
private Blob storage to complete the full snapshot. A missing bundle fails the import.

The 340.642 MB container total measured earlier includes source/report assets and
multiple archives. Neither that total nor exported corpus bytes establishes Azure
Search index storage usage. Measure the actual index after the separate indexing step.

## Next step

Once the report is reviewed, run `knowledge plan-index`, then `knowledge index` with
both `--corpus` and `--ingestion-report`. Follow the
[indexing and retrieval runbook](embeddings-and-retrieval.md) and inspect
Azure's index statistics and validate representative questions against source evidence.
Corpus activation and the production image are separate reviewable steps. The public
Answer/OpenAPI contract is unchanged by ingestion, so this step requires no Kiota
regeneration or frontend changes.
