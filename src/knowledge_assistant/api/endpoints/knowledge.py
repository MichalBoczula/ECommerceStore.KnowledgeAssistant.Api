from typing import Annotated

from fastapi import APIRouter, Depends

from knowledge_assistant.application.contracts import Answer, Catalog, Query
from knowledge_assistant.application.services.knowledge_service import KnowledgeService

from ..configuration.problem_details import problem_responses
from ..dependencies import authorize, knowledge_service

router = APIRouter(prefix="/api/v1", tags=["Knowledge"], dependencies=[Depends(authorize)])
Service = Annotated[KnowledgeService, Depends(knowledge_service)]


@router.get(
    "/catalog",
    response_model=Catalog,
    operation_id="getKnowledgeCatalog",
    responses=problem_responses(401, 500, 503),
)
def catalog(service: Service) -> Catalog:
    return service.catalog()


@router.post(
    "/query",
    response_model=Answer,
    operation_id="queryKnowledge",
    responses=problem_responses(400, 401, 404, 409, 415, 500, 503),
)
def query(body: Query, service: Service) -> Answer:
    return service.query(body)
