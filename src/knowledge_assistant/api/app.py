"""Composition root: HTTP and infrastructure are wired here, outside the use cases."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from knowledge_assistant.application.ports import AnswerPlanner, KnowledgeRetriever
from knowledge_assistant.application.services.evidence_planner import EvidencePlanner
from knowledge_assistant.application.services.knowledge_service import KnowledgeService
from knowledge_assistant.domain.models import Corpus
from knowledge_assistant.infrastructure.configuration.settings import Settings
from knowledge_assistant.infrastructure.planning.model_planner import ModelPlanner
from knowledge_assistant.infrastructure.retrieval.azure_retriever import AzureRetriever
from knowledge_assistant.infrastructure.retrieval.local_retriever import LocalRetriever

from .configuration.exception_handlers import register_exception_handlers
from .endpoints import health, knowledge


def create_app(
    settings: Settings | None = None,
    *,
    corpus: Corpus | None = None,
    retriever: KnowledgeRetriever | None = None,
    planner: AnswerPlanner | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loaded = corpus or Corpus.model_validate_json(settings.corpus_path.read_text())
        retrieval = retriever or (
            AzureRetriever(settings, loaded)
            if settings.retrieval_mode == "azure"
            else LocalRetriever(loaded)
        )
        generation = planner or (
            ModelPlanner(settings) if settings.generation_mode == "azure" else EvidencePlanner()
        )
        app.state.knowledge_service = KnowledgeService(
            loaded, retrieval, generation, settings.max_candidates
        )
        try:
            yield
        finally:
            for resource in (retrieval, generation):
                if hasattr(resource, "close"):
                    resource.close()

    app = FastAPI(title="E-Commerce Knowledge Assistant", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key"],
        )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(knowledge.router)

    def openapi():
        if app.openapi_schema is None:
            schema = get_openapi(title=app.title, version=app.version, routes=app.routes)
            for path in schema["paths"].values():
                for operation in path.values():
                    responses = operation.get("responses", {})
                    # Binding failures use the shared 400 contract rather than framework 422.
                    responses.pop("422", None)
                    for status, response in responses.items():
                        if status.isdigit() and int(status) >= 400:
                            content = response.get("content", {})
                            if "application/json" in content:
                                content["application/problem+json"] = content.pop(
                                    "application/json"
                                )
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app
