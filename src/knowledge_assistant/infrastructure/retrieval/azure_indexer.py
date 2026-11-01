"""Operator-only indexing; the API runtime never constructs an index-management client."""

import time
from pathlib import Path

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient

from ..clients.embeddings import EmbeddingCache, EmbeddingClient
from ..configuration.settings import Settings
from .index_plan import BATCH_SIZE, MANIFEST_KIND, IndexPlan, embedding_profile, manifest_key, scope
from .index_schema import compatible, index_definition


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
        if (
            len(results) != len(rows)
            or any(not r.succeeded for r in results)
            or {r.key for r in results} != {r["id"] for r in rows}
        ):
            raise ValueError("Index upload was incomplete; snapshot must not be activated")

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
        self.ensure_index()
        cache = EmbeddingCache(cache_path, plan.profile, self.settings.embedding_dimensions)
        for start in range(0, len(plan.rows), BATCH_SIZE):
            batch = plan.rows[start : start + BATCH_SIZE]
            vectors = cache.vectors([row["content"] for row in batch], self.embeddings)
            self.upload_rows(
                [{**row, "embedding": vector} for row, vector in zip(batch, vectors, strict=True)]
            )
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
        self.upload_rows([marker])
        try:
            stats = self.index_client.get_index_statistics(self.settings.search_index)
            storage = {
                "storageBytes": stats.storage_size,
                "vectorIndexBytes": stats.vector_index_size,
            }
        except AzureError:
            storage = {"storageBytes": None, "vectorIndexBytes": None}
        return {
            **plan.summary(self.settings.embedding_dimensions),
            "searchIndex": self.settings.search_index,
            "uploadComplete": True,
            "visibleChunksVerified": True,
            "cacheHits": cache.hits,
            "newEmbeddingInputs": cache.misses,
            "embeddingRequests": self.embeddings.requests - previous_requests,
            "embeddingInputTokens": self.embeddings.input_tokens - previous_tokens,
            **storage,
            "note": (
                "Index statistics are eventual and include other snapshots; activation is separate."
            ),
        }

    def close(self):
        self.client.close()
        self.index_client.close()
        self.embeddings.close()
        if hasattr(self.credential, "close"):
            self.credential.close()
