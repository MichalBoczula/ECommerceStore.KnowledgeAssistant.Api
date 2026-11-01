import logging
from http import HTTPStatus
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from knowledge_assistant.application.errors import (
    DependencyUnavailableError,
    ResourceNotFoundError,
    SnapshotNotActiveError,
)
from knowledge_assistant.domain.errors import EvidenceVerificationError

from .problem_details import ApiProblemDetails, ApiValidationError

logger = logging.getLogger(__name__)


def problem(
    request: Request,
    status: int,
    code: str,
    detail: str,
    *,
    errors: list[ApiValidationError] | None = None,
    missing: list[str] | None = None,
    headers: dict | None = None,
) -> JSONResponse:
    trace_id = str(uuid4())
    body = ApiProblemDetails(
        type="about:blank",
        title=HTTPStatus(status).phrase,
        status=status,
        detail=detail,
        instance=request.url.path,
        code=code,
        trace_id=trace_id,
        errors=errors or [],
        missing_properties=missing or [],
    )
    # Never log request bodies, provider exception messages or generated content.
    logger.warning("API problem trace_id=%s status=%d code=%s", trace_id, status, code)
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content=body.model_dump(by_alias=True),
        headers={**(headers or {}), "X-Request-ID": trace_id},
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        entries = exc.errors()
        invalid_json = any(
            e["type"]
            in {
                "json_invalid",
                "missing",
                "model_attributes_type",
                "string_type",
                "int_type",
                "bool_type",
                "dict_type",
                "list_type",
                "literal_error",
                "extra_forbidden",
            }
            for e in entries
        )
        if any(e["loc"] == ("body",) and e["type"] == "missing" for e in entries):
            return problem(request, 400, "invalid_request", "A JSON request body is required.")
        # Names are from parsed contract locations. Never reflect invalid inputs/messages.
        missing = sorted(
            {
                str(e["loc"][-1])
                for e in entries
                if e["type"] == "missing" and len(e["loc"]) > 1 and e["loc"][0] == "body"
            }
        )
        return problem(
            request,
            400,
            "invalid_json" if invalid_json else "validation_failed",
            "The JSON request does not match the contract."
            if invalid_json
            else "One or more validation errors occurred.",
            missing=missing,
            errors=[]
            if invalid_json
            else [
                ApiValidationError(
                    message="The value does not satisfy the request contract.",
                    name="RequestValidation",
                    entity=str(e["loc"][-1]),
                )
                for e in entries
            ],
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        codes = {
            400: "invalid_request",
            401: "unauthorized",
            403: "forbidden",
            404: "route_not_found",
            405: "method_not_allowed",
            415: "unsupported_media_type",
            503: "service_unavailable",
        }
        return problem(
            request,
            exc.status_code,
            codes.get(exc.status_code, f"http_{exc.status_code}"),
            "The request could not be processed.",
            headers=exc.headers,
        )

    async def snapshot_error(request: Request, exc: SnapshotNotActiveError):
        return problem(
            request,
            409,
            "snapshot_not_active",
            "The active documentation snapshot changed. Refresh the catalog.",
        )

    async def not_found(request: Request, exc: ResourceNotFoundError):
        return problem(
            request,
            404,
            "resource_not_found",
            "The service is absent from the selected documentation snapshot.",
        )

    async def unavailable(request: Request, exc: DependencyUnavailableError):
        return problem(
            request,
            503,
            "service_unavailable",
            "The knowledge provider is unavailable. Try again later.",
        )

    async def unverified(request: Request, exc: EvidenceVerificationError):
        return problem(
            request,
            503,
            "answer_verification_failed",
            "The answer could not be verified against the selected evidence.",
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        return problem(request, 500, "internal_error", "An unexpected error occurred.")

    for error, handler in [
        (SnapshotNotActiveError, snapshot_error),
        (ResourceNotFoundError, not_found),
        (DependencyUnavailableError, unavailable),
        (EvidenceVerificationError, unverified),
    ]:
        app.add_exception_handler(error, handler)
