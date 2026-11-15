"""Operator-only indexing; the API runtime never constructs an index-management client."""

import time
from contextlib import contextmanager
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from openai import OpenAIError

from ..clients.embeddings import EmbeddingCache, EmbeddingClient
from ..configuration.settings import Settings
from .index_plan import BATCH_SIZE, MANIFEST_KIND, IndexPlan, embedding_profile, manifest_key, scope
from .index_schema import compatible, index_definition


class IndexingFailure(ValueError):
    """An operator-facing failure built from safe provider metadata."""


def _http_status(error) -> int | None:
    status = getattr(error, "status_code", None)
    return status if type(status) is int and 100 <= status <= 599 else None


class AzureIndexer:
    def __init__(self, settings: Settings, *, client=None, index_client=None, embeddings=None):
        settings.require_azure()
        if not settings.search_endpoint:
            raise ValueError("Configure KA_SEARCH_ENDPOINT")
        self.settings = settings
        self.credential = None
        if client is None or index_client is None:
            self.credential = (
                AzureKeyCredential(settings.search_key.get_secret_value())
                if settings.search_key
                else DefaultAzureCredential()
            )
        self.client = client or SearchClient(
            settings.search_endpoint, settings.search_index, self.credential
        )
        self.index_client = index_client or SearchIndexClient(
            settings.search_endpoint, self.credential
        )
        self.embeddings = embeddings or EmbeddingClient(settings)

    def ensure_index(self):
        expected = index_definition(self.settings.search_index, self.settings.embedding_dimensions)
        try:
            actual = self.index_client.get_index(self.settings.search_index)
        except ResourceNotFoundError:
            self.index_client.create_index(expected)
            return
        if not compatible(actual, expected):
            raise ValueError("Existing index schema is incompatible; select a new KA_SEARCH_INDEX")

    def upload_rows(self, rows):
        results = self.client.upload_documents(documents=rows)
        failed = [r for r in results if not r.succeeded]
        if (
            len(results) != len(rows)
            or failed
            or {r.key for r in results} != {r["id"] for r in rows}
        ):
            statuses = sorted({status for result in failed if (status := _http_status(result))})
            detail = f"HTTP {' / '.join(str(status) for status in statuses)}" if statuses else ""
            raise IndexingFailure("index write was incomplete" + (f" ({detail})" if detail else ""))

    def storage_usage(self) -> str:
        try:
            counters = self.index_client.get_service_statistics().get("counters", {})
        except AzureError:
            return "Search service storage statistics unavailable."
        storage = counters.get("storage_size", {}) if isinstance(counters, dict) else {}
        if isinstance(storage, dict):
            used, quota = storage.get("usage"), storage.get("quota")
            if type(used) is int and used >= 0 and type(quota) is int and quota > 0:
                return f"Search service storage {used:,} of {quota:,} bytes."
        return "Search service storage statistics unavailable."

    @contextmanager
    def _step(self, provider: str, action: str, progress: str = "", *, read_storage=False):
        try:
            yield
        except (AzureError, OpenAIError, OSError, IndexingFailure) as error:
            if isinstance(error, IndexingFailure):
                reason = str(error)
            elif isinstance(error, OSError):
                provider = "Local I/O"
                reason = f"errno {error.errno}" if type(error.errno) is int else "I/O error"
            else:
                status = _http_status(error)
                reason = f"HTTP {status}" if status else "transport or provider error"
            where = f" ({progress})" if progress else ""
            capacity = f" {self.storage_usage()}" if read_storage else ""
            raise IndexingFailure(
                f"{provider} failed while {action}{where}: {reason}.{capacity}"
            ) from None

    def verify_visible(self, plan: IndexPlan):
        filtered = scope(plan.corpus_sha, plan.profile) + f" and kind ne '{MANIFEST_KIND}'"
        for attempt in range(30):
            results = self.client.search(
                search_text="*", filter=filtered, top=0, include_total_count=True
            )
            if results.get_count() == len(plan.rows):
                return
            if attempt < 29:
                time.sleep(1)
        raise ValueError("Uploaded chunks are not all visible; retry indexing before activation")

    def upload(self, plan: IndexPlan, cache_path: Path) -> dict:
        if plan.profile != embedding_profile(self.settings):
            raise ValueError("Index plan differs from the configured embedding profile")
        previous_requests = self.embeddings.requests
        previous_tokens = self.embeddings.input_tokens
        with self._step("Azure AI Search", "checking or creating the index"):
            self.ensure_index()
        with self._step("Azure AI Search", "reading the completion marker"):
            try:
                marker = self.client.get_document(manifest_key(plan.corpus_sha, plan.profile))
            except ResourceNotFoundError:
                marker = None
        if marker is not None:
            if any(
                marker.get(key) != value
                for key, value in {
                    "kind": MANIFEST_KIND,
                    "corpusHash": plan.corpus_sha,
                    "embeddingProfile": plan.profile,
                    "snapshotId": plan.corpus.snapshot_id,
                    "sourceDocumentCount": len(plan.corpus.documents),
                    "chunkCount": len(plan.rows),
                }.items()
            ):
                raise ValueError("Completed index marker differs from the requested plan")
            with self._step("Azure AI Search", "checking visibility of the completed scope"):
                self.verify_visible(plan)
            return {
                **plan.summary(self.settings.embedding_dimensions),
                "searchIndex": self.settings.search_index,
                "uploadComplete": True,
                "visibleChunksVerified": True,
                "reusedCompletedSnapshot": True,
                "cacheHits": 0,
                "newEmbeddingInputs": 0,
                "embeddingRequests": 0,
                "embeddingInputTokens": 0,
                **self.statistics(),
            }
        cache = EmbeddingCache(cache_path, plan.profile, self.settings.embedding_dimensions)
        for start in range(0, len(plan.rows), BATCH_SIZE):
            batch = plan.rows[start : start + BATCH_SIZE]
            progress = f"{start:,} of {len(plan.rows):,} chunks uploaded in this run"
            with self._step("Azure OpenAI", "generating or loading embeddings", progress):
                vectors = cache.vectors([row["content"] for row in batch], self.embeddings)
            with self._step("Azure AI Search", "uploading chunks", progress, read_storage=True):
                self.upload_rows(
                    [
                        {**row, "embedding": vector}
                        for row, vector in zip(batch, vectors, strict=True)
                    ]
                )
        with self._step("Azure AI Search", "verifying visible chunks"):
            self.verify_visible(plan)
        marker = {
            "id": manifest_key(plan.corpus_sha, plan.profile),
            "kind": MANIFEST_KIND,
            "snapshotId": plan.corpus.snapshot_id,
            "corpusHash": plan.corpus_sha,
            "embeddingProfile": plan.profile,
            "chunkCount": len(plan.rows),
            "sourceDocumentCount": len(plan.corpus.documents),
        }
        with self._step("Azure AI Search", "writing the completion marker", read_storage=True):
            self.upload_rows([marker])
        return {
            **plan.summary(self.settings.embedding_dimensions),
            "searchIndex": self.settings.search_index,
            "uploadComplete": True,
            "visibleChunksVerified": True,
            "reusedCompletedSnapshot": False,
            "cacheHits": cache.hits,
            "newEmbeddingInputs": cache.misses,
            "embeddingRequests": self.embeddings.requests - previous_requests,
            "embeddingInputTokens": self.embeddings.input_tokens - previous_tokens,
            **self.statistics(),
            "note": (
                "Index statistics are eventual and include other snapshots; activation is separate."
            ),
        }

    def statistics(self):
        try:
            stats = self.index_client.get_index_statistics(self.settings.search_index)
            return {
                "storageBytes": stats.get("storage_size"),
                "vectorIndexBytes": stats.get("vector_index_size"),
            }
        except AzureError:
            return {"storageBytes": None, "vectorIndexBytes": None}

    def close(self):
        self.client.close()
        self.index_client.close()
        self.embeddings.close()
        if hasattr(self.credential, "close"):
            self.credential.close()
