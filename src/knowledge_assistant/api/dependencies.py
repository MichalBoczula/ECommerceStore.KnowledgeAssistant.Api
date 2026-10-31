import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

from knowledge_assistant.application.services.knowledge_service import KnowledgeService

api_key = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Added by the Web server proxy when API authentication is configured.",
)


def authorize(request: Request, key: Annotated[str | None, Depends(api_key)]) -> None:
    expected = request.app.state.settings.api_key
    if expected and (not key or not secrets.compare_digest(key, expected.get_secret_value())):
        raise HTTPException(401)
    if request.method == "POST":
        media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
        if media_type != "application/json":
            raise HTTPException(415)


def knowledge_service(request: Request) -> KnowledgeService:
    return request.app.state.knowledge_service
