from pydantic import ConfigDict

from knowledge_assistant.domain.common.model import Model


class ApiValidationError(Model):
    message: str
    name: str
    entity: str


class ApiProblemDetails(Model):
    """The shared store error contract; clients branch on code, not public detail."""

    model_config = ConfigDict(extra="allow")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    trace_id: str
    errors: list[ApiValidationError]
    missing_properties: list[str]


# Explicit operation metadata drives Kiota error mappings, including framework failures.
def problem_responses(*statuses: int) -> dict:
    return {
        status: {
            "model": ApiProblemDetails,
            "description": "Safe API problem. Branch on code; traceId correlates server logs.",
            "content": {"application/problem+json": {}},
        }
        for status in statuses
    }
