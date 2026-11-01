from fastapi import APIRouter, Request

from knowledge_assistant.application.contracts import Health, Readiness

from ..configuration.problem_details import problem_responses

router = APIRouter(tags=["Health"])


@router.get(
    "/health/live",
    response_model=Health,
    operation_id="getLiveness",
    responses=problem_responses(500),
)
def live() -> Health:
    return Health(status="healthy")


@router.get(
    "/health/ready",
    response_model=Readiness,
    operation_id="getReadiness",
    responses=problem_responses(500, 503),
)
def ready(request: Request) -> Readiness:
    return Readiness(
        status="ready", snapshot_id=request.app.state.knowledge_service.corpus.snapshot_id
    )
