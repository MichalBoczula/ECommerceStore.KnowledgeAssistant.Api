# API Problem Details

Responses use `application/problem+json` and always emit all nine fields:

```json
{
  "type": "about:blank",
  "title": "Conflict",
  "status": 409,
  "detail": "The active documentation snapshot changed. Refresh the catalog.",
  "instance": "/api/v1/query",
  "code": "snapshot_not_active",
  "traceId": "opaque-request-id",
  "errors": [],
  "missingProperties": []
}
```

This follows the ECommerceStoreUsers contract. `type` is the RFC Problem Details
`about:blank` type, and `title` is the HTTP status phrase. `instance` excludes query
values; `traceId` matches the `X-Request-ID` header and server log. Clients branch on
`code`. Problem extensions are permitted. `errors` entries have `message`, `name`
and `entity`; invalid values and provider exception text are never reflected.

| Code | HTTP | Meaning |
| --- | --- | --- |
| `validation_failed` | 400 | Question or context constraints fail. |
| `invalid_json` | 400 | JSON syntax, types, required members or unknown members fail binding. |
| `invalid_request` | 400 | Required request body is absent. |
| `unauthorized` | 401 | Configured API key is missing/incorrect. |
| `resource_not_found` | 404 | Service is absent from this snapshot. |
| `route_not_found` | 404 | No matching route. |
| `method_not_allowed` | 405 | Route exists but the method does not match. |
| `snapshot_not_active` | 409 | Requested documentation snapshot is stale. |
| `unsupported_media_type` | 415 | Query request is not application/json. |
| `internal_error` | 500 | Unexpected failure, generic public detail. |
| `service_unavailable` | 503 | Retrieval/model provider cannot complete the query. |
| `answer_verification_failed` | 503 | Evidence scope or generated claims failed verification. |

Other framework errors use `http_{status}`. `missingProperties` contains inspected
JSON member names when available, otherwise an empty array. Other failures have
empty arrays. Errors never silently switch to demonstration data. A successful
query with no evidence is HTTP 200 with answer status `not_found`.

`contracts/openapi.json` describes declared endpoint error responses with the problem
media type, stable operation IDs and request/response models. Run
`uv run knowledge export-contract` after endpoint/DTO changes and review the artifact.
