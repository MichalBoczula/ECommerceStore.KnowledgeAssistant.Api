"""Read-only hybrid retrieval, hydrated exclusively from the selected local corpus."""

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from openai import OpenAIError

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.errors import DependencyUnavailableError
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.models import Corpus, Document

from ..clients.embeddings import EmbeddingClient, InvalidEmbeddingError, validate_vector
from ..configuration.settings import Settings
from .context import expand_context
from .index_plan import (
    INPUT_BYTES,
    MANIFEST_KIND,
    chunk_text,
    corpus_hash,
    document_hash,
    embedding_profile,
    literal,
    manifest_key,
    scope,
)


class AzureRetriever:
    mode = "azure_hybrid"

    def __init__(self, settings: Settings, corpus: Corpus, *, client=None, embeddings=None):
        settings.require_azure()
        if not settings.search_endpoint:
            raise ValueError("Configure KA_SEARCH_ENDPOINT")
        self.settings = settings
        self.corpus = corpus
        self.documents = {d.id: d for d in corpus.documents}
        if not self.documents or len(self.documents) != len(corpus.documents):
            raise ValueError("Retrieval requires a nonempty corpus with unique document IDs")
        self.hashes = {d.id: document_hash(d) for d in corpus.documents}
        self.corpus_sha, self.profile = corpus_hash(corpus), embedding_profile(settings)
        self.credential = None
        if client is None:
            self.credential = (
                AzureKeyCredential(settings.search_key.get_secret_value())
                if settings.search_key
                else DefaultAzureCredential()
            )
        self.client = client or SearchClient(
            settings.search_endpoint, settings.search_index, self.credential
        )
        self.embeddings = embeddings or EmbeddingClient(settings)

    def search(self, query: Query, limit: int = 12) -> list[Document]:
        try:
            return self._search(query, limit)
        except (AzureError, OpenAIError, InvalidEmbeddingError) as exc:
            raise DependencyUnavailableError() from exc

    def _search(self, query: Query, limit: int) -> list[Document]:
        if query.snapshot_id != self.corpus.snapshot_id:
            raise EvidenceVerificationError("Query differs from the configured corpus")
        if not 1 <= limit <= 50:
            raise ValueError("Retrieval limit must be from 1 to 50")
        if not query.question.strip():
            return []
        try:
            marker = self.client.get_document(manifest_key(self.corpus_sha, self.profile))
        except ResourceNotFoundError:
            raise DependencyUnavailableError(
                "Selected corpus has no completed index upload"
            ) from None
        if (
            marker.get("kind") != MANIFEST_KIND
            or marker.get("corpusHash") != self.corpus_sha
            or marker.get("embeddingProfile") != self.profile
            or marker.get("snapshotId") != query.snapshot_id
            or marker.get("sourceDocumentCount") != len(self.documents)
            or not isinstance(marker.get("chunkCount"), int)
            or marker["chunkCount"] < 1
        ):
            raise EvidenceVerificationError(
                "Index completion marker differs from the selected corpus"
            )
        inputs = (
            [query.question]
            if len(query.question.encode()) <= INPUT_BYTES
            else list(chunk_text("", query.question))
        )
        vectors = self.embeddings.embed(inputs)
        vector = [sum(values) / len(vectors) for values in zip(*vectors, strict=True)]
        vector = validate_vector(vector, self.settings.embedding_dimensions)
        filtered = (
            scope(self.corpus_sha, self.profile)
            + f" and service eq {literal(query.service)}"
            + f" and snapshotId eq {literal(query.snapshot_id)} and kind ne '{MANIFEST_KIND}'"
        )
        results = self.client.search(
            search_text=query.question,
            filter=filtered,
            top=min(limit * 4, 100),
            select=[
                "documentId",
                "documentHash",
                "service",
                "snapshotId",
                "kind",
                "corpusHash",
                "embeddingProfile",
            ],
            vector_queries=[
                VectorizedQuery(
                    vector=vector, k_nearest_neighbors=min(limit * 4, 100), fields="embedding"
                )
            ],
            vector_filter_mode="preFilter",
        )
        docs, seen = [], set()
        for row in results:
            doc = self.documents.get(row.get("documentId"))
            if (
                doc is None
                or row.get("corpusHash") != self.corpus_sha
                or row.get("embeddingProfile") != self.profile
                or row.get("service") != query.service
                or row.get("snapshotId") != query.snapshot_id
                or doc.source.service != query.service
                or doc.source.snapshot_id != query.snapshot_id
                or row.get("kind") != doc.kind
                or row.get("documentHash") != self.hashes[doc.id]
            ):
                raise EvidenceVerificationError(
                    "Search returned unknown, stale or out-of-scope evidence"
                )
            if doc.id not in seen:
                seen.add(doc.id)
                docs.append(doc)
        return expand_context(docs, self.corpus, query, limit)

    def close(self):
        self.client.close()
        self.embeddings.close()
        if hasattr(self.credential, "close"):
            self.credential.close()
