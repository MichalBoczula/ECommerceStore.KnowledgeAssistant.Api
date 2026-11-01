# Structured answers from documented evidence

The API returns typed sections that the Web application can label and render separately.
For the original question, "Explain mobile phone validation and give me the JSON contract
to create a mobile phone," the server returns one validation section followed by the
selected API contract. Existing explanation/scenario sections follow those sections.
The frontend can number the returned array and render the contract in its JSON tree.
The public Answer/OpenAPI schema and Kiota client are unchanged by this step.

## What the model selects

The private model plan contains section types, document IDs, source IDs, documented
rule IDs and optional generated-example JSON. A validation rule ID identifies an exact
field/description in a specific retrieved policy. The plan no longer accepts arbitrary
rule text. The server checks IDs against the evidence actually supplied to the model,
then copies original rule text and citations into the public `rules` array.

| Section | Server behavior |
| --- | --- |
| `validation` | Copy selected documented rules. Merge policy selections into one section, deduplicate repeated rules and retain per-rule source IDs. |
| `api_contract` | Copy the source operation's method, path, operation ID, media type, request schema, local schema components and documented example. |
| `scenario` | Copy the selected source content. Only an Allure execution document can supply `executionStatus`; a Gherkin scenario describes expected behavior. |
| `explanation` | Accept model-written text only with known supplied citations. The language still requires relevance/semantic review; ID checks are not an entailment proof. |

Artifact titles and citations come from the source document; extra model-added citations
do not become contract/rule provenance. Unknown IDs, wrong source kinds, mismatched
scope and contradictory not-found plans fail verification. Source lookup does not
fetch an artifact at answer time or accept replacement payloads from the model.

The evidence-only planner uses the same rule-ID path. It selects an unambiguous contract
using the requested action/resource; tied candidates are not resolved by retrieval order.
It produces extracted evidence, not a generated business explanation.

## Bounded model context

The default model input budget is 64,000 UTF-8 bytes, including the question and JSON
envelope. Each evidence record has a 12,000-byte limit. These are deterministic byte
budgets, not model token counts or a promise that every possible deployment fits them.
Use a configured chat deployment that supports Responses and structured outputs.

Policies include exact rule IDs/text, operation IDs and policy role. Contracts include
the selected request schema and only transitively referenced local component schemas.
Unrelated components, duplicated original/resolved operations and full Allure payloads
are excluded. Other content has a bounded excerpt. Relationship warnings, observed
execution status and whether content was limited are supplied explicitly.

Oversized rules are omitted as complete units. The model cannot select a retrieved rule
that was omitted from its input. An oversized/unresolvable request schema remains
available for exact server hydration, while example generation is disabled for it.
Context omissions produce an explicit partial-answer limitation. No hidden schema or
contract is replaced with a guessed smaller structure.

The configured chat deployment is preserved. There is no new model choice, external
tool call, repair-model call or evidence/demo fallback. Responses requests use the
Pydantic plan schema and `store=false`. This flag is not a claim about every aspect of
provider data retention. Credentials remain in the existing server configuration.

## Examples and verification

Documented examples always take precedence over generated proposals. They are retained
even when invalid, with `exampleOrigin: openapi`, a failed/unknown verification state
and a partial-answer limitation. This preserves what the source actually documents.

Generated examples must be strict JSON: duplicate object members, non-finite numbers,
malformed content and excessive example size are rejected. Verification uses OpenAPI
3.0 or JSON Schema validation and local reference schemas with an offline registry.
Missing or external references cannot trigger a network fetch. A generated example
that fails validation or cannot be verified is omitted; the original request schema
and supported rules remain available.

`exampleOrigin` distinguishes `openapi`, `generated` and `unavailable`.
`exampleSchemaValid` is true, false or unknown. An explicit JSON `null` can be a valid
example, so consumers must use origin/verification metadata rather than truthiness.
Source import distinguishes an explicit null value from an Example Object that only
has a description/summary and supplies no value.

Successful schema checking never establishes runtime/business-rule compliance. Generated
examples carry that limitation explicitly. Do not treat one as a request that was
executed against the application.

## Missing evidence and status

The server computes status from explicit verification outcomes, not English phrases in
limitation strings. A supported contract with missing requested validation is `partial`;
supported rules survive a rejected generated example. An answer with no sections is
`not_found`. A model cannot label a nonempty answer `not_found` and have it silently
published. Section order is enforced even if the model returns another order.

A `published_only` or unspecified policy is a catalog entry. A `referenced` policy is
an explicit documentation relationship. Neither establishes that every rule executed.
Missing links to the selected operation, selected rule subsets, unresolved operation
relationships and failed/skipped/unknown cited executions have explicit limitations.
An execution's status describes its pinned run, not the currently deployed application.

Invalid/refused/incomplete structured output returns the existing 503
`answer_verification_failed` Problem Details. Provider failures return the existing 503
`service_unavailable`. Responses and logs do not include raw generated output, provider
messages, credentials or invalid values. The supported error contract is unchanged.

## Run and verify

Use the existing `KA_GENERATION_MODE=azure`, Azure OpenAI v1 endpoint and
`KA_CHAT_DEPLOYMENT` configuration. Retrieval/corpus activation remains the separate
Task 3 workflow. A local smoke check can use the synthetic corpus with local retrieval
and a configured model; the catalog and answers continue to report `isDemo: true`.
Do not present that as a live business-documentation answer.

```bash
uv sync --locked
uv run pytest -q
uv run knowledge export-contract
```

The automated suite uses simulated model responses to verify request shape, known-ID
selection, section ordering, original rule/contract hydration, context limits, example
verification, refusal/incomplete handling and safe HTTP failures. It does not measure
the deployed model's relevance, prompt-injection resistance or semantic correctness.
Run representative questions against the configured Azure deployment before treating
model synthesis as validated. No live Azure model call or deployment was performed
while implementing this step.

The implementation follows the official [OpenAI Structured Outputs documentation](https://developers.openai.com/api/docs/guides/structured-outputs?api-mode=responses),
including explicit refusal/incomplete handling and the distinction between a valid
structured shape and a semantically correct answer.
