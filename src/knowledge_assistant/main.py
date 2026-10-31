import logging
import secrets
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .answers import EvidencePlanner, ModelPlanner, assemble
from .config import Settings
from .models import Answer, AnswerPlan, Corpus, Query
from .retrieval import AzureRetriever, LocalRetriever

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, *, corpus: Corpus | None = None, retriever=None, planner=None
) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        loaded = corpus or Corpus.model_validate_json(settings.corpus_path.read_text())
        app.state.corpus = loaded
        app.state.retriever = retriever or (
            AzureRetriever(settings, loaded)
            if settings.retrieval_mode == "azure"
            else LocalRetriever(loaded)
        )
        app.state.planner = planner or (
            ModelPlanner(settings) if settings.generation_mode == "azure" else EvidencePlanner()
        )
        yield
        for resource in (app.state.retriever, app.state.planner):
            if hasattr(resource, "close"):
                resource.close()

    app = FastAPI(title="E-Commerce Knowledge Assistant", version="0.1.0", lifespan=lifespan)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-API-Key"],
        )

    def authorize(x_api_key: str | None = Header(default=None)) -> None:
        if settings.api_key and (
            not x_api_key
            or not secrets.compare_digest(x_api_key, settings.api_key.get_secret_value())
        ):
            raise HTTPException(401, "Invalid API key")

    @app.get("/health/live")
    def live():
        return {"status": "healthy"}

    @app.get("/health/ready")
    def ready(request: Request):
        # Local readiness proves the pinned corpus loaded, not upstream availability.
        return {"status": "ready", "snapshotId": request.app.state.corpus.snapshot_id}

    @app.get("/api/v1/catalog", dependencies=[Depends(authorize)])
    def catalog(request: Request):
        current = request.app.state.corpus
        return {
            "schemaVersion": 1,
            "snapshots": [
                {
                    "id": current.snapshot_id,
                    "version": current.version,
                    "isDemo": current.is_demo,
                    "services": sorted({d.source.service for d in current.documents}),
                }
            ],
            "answerMode": request.app.state.planner.mode,
            "retrievalMode": request.app.state.retriever.mode,
        }

    @app.post("/api/v1/query", response_model=Answer, dependencies=[Depends(authorize)])
    def query(body: Query, request: Request):
        current = request.app.state.corpus
        if body.snapshot_id != current.snapshot_id:
            raise HTTPException(409, "Requested snapshot is not active; refresh the catalog")
        if body.service not in {d.source.service for d in current.documents}:
            raise HTTPException(404, "Service is absent from the selected snapshot")
        retrieval = request.app.state.retriever
        generation = request.app.state.planner
        documents = retrieval.search(body, settings.max_candidates)
        for doc in documents:
            if doc.source.snapshot_id != body.snapshot_id or doc.source.service != body.service:
                raise ValueError("Evidence escaped the requested scope")
        plan = (
            generation.plan(body.question, documents)
            if documents
            else AnswerPlan(
                status="not_found",
                sections=[],
                limitations=[
                    "No supporting evidence was found in the selected service and snapshot."
                ],
            )
        )
        return assemble(body, documents, plan, generation.mode, retrieval.mode, current.is_demo)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception):
        request_id = str(uuid4())
        # Log only exception type; SDK errors and model output can contain sensitive data.
        logger.error("Knowledge query failed request_id=%s type=%s", request_id, type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "errorCode": "KNOWLEDGE_UNAVAILABLE",
                "message": "The answer could not be verified. Try again later.",
                "requestId": request_id,
            },
        )

    return app


app = create_app()
