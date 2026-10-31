import json

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError
from azure.identity import DefaultAzureCredential
from azure.search.documents import SearchClient
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    ExhaustiveKnnAlgorithmConfiguration,
    ExhaustiveKnnParameters,
    SearchableField,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from openai import OpenAIError

from knowledge_assistant.application.contracts import Query
from knowledge_assistant.application.errors import DependencyUnavailableError
from knowledge_assistant.domain.errors import EvidenceVerificationError
from knowledge_assistant.domain.models import Corpus, Document, Source

from ..clients.openai_client import openai_client
from ..configuration.settings import Settings
from ..ingestion.livedocs import canonical, digest


class AzureRetriever:
    mode = "azure_hybrid"

    def __init__(self, settings: Settings, corpus: Corpus | None = None):
        settings.require_azure()
        if not settings.search_endpoint:
            raise ValueError("Configure KA_SEARCH_ENDPOINT")
        self.settings = settings
        self.documents = {d.id: d for d in corpus.documents} if corpus else {}
        self.credential = (
            AzureKeyCredential(settings.search_key.get_secret_value())
            if settings.search_key
            else DefaultAzureCredential()
        )
        self.client = SearchClient(settings.search_endpoint, settings.search_index, self.credential)
        self.index_client = SearchIndexClient(settings.search_endpoint, self.credential)
        self.openai = openai_client(settings)
        self.profile = digest(
            canonical(
                [
                    settings.openai_endpoint,
                    settings.embedding_deployment,
                    settings.embedding_dimensions,
                ]
            ).encode()
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        result = self.openai.embeddings.create(
            model=self.settings.embedding_deployment,
            input=texts,
            dimensions=self.settings.embedding_dimensions,
        )
        ordered = sorted(result.data, key=lambda item: item.index)
        if len(ordered) != len(texts):
            raise ValueError("Embedding response omitted documents")
        vectors = [item.embedding for item in ordered]
        if any(len(v) != self.settings.embedding_dimensions for v in vectors):
            raise ValueError("Embedding dimensions differ from the index configuration")
        return vectors

    def ensure_index(self) -> None:
        fields = [SimpleField(name="id", type="Edm.String", key=True)]
        for field in ("service", "snapshotId", "kind", "embeddingProfile"):
            fields.append(SimpleField(name=field, type="Edm.String", filterable=True))
        fields.append(SimpleField(name="documentId", type="Edm.String", filterable=True))
        fields.extend(
            [
                SearchableField(name="title", type="Edm.String"),
                SearchableField(name="content", type="Edm.String"),
                SimpleField(name="sourceJson", type="Edm.String"),
                SimpleField(name="payloadJson", type="Edm.String"),
                SimpleField(name="operationId", type="Edm.String", filterable=True),
                SimpleField(name="tagsJson", type="Edm.String"),
                SearchField(
                    name="embedding",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True,
                    hidden=True,
                    vector_search_dimensions=self.settings.embedding_dimensions,
                    vector_search_profile_name="knowledge-profile",
                ),
            ]
        )
        vectors = VectorSearch(
            algorithms=[
                ExhaustiveKnnAlgorithmConfiguration(
                    name="exact-cosine", parameters=ExhaustiveKnnParameters(metric="cosine")
                )
            ],
            profiles=[
                VectorSearchProfile(
                    name="knowledge-profile", algorithm_configuration_name="exact-cosine"
                )
            ],
        )
        self.index_client.create_or_update_index(
            SearchIndex(name=self.settings.search_index, fields=fields, vector_search=vectors)
        )

    def upload(self, corpus: Corpus) -> dict:
        self.ensure_index()
        indexed = 0
        chunks = [
            (doc, part, doc.content[start : start + 10000])
            for doc in corpus.documents
            for part, start in enumerate(range(0, max(len(doc.content), 1), 10000))
        ]
        for start in range(0, len(chunks), 16):
            batch = chunks[start : start + 16]
            texts = [d.title + "\n" + content for d, _, content in batch]
            rows = []
            for (doc, part, _), vector, text in zip(batch, self.embed(texts), texts, strict=True):
                rows.append(
                    {
                        "id": f"{doc.id}-{part}",
                        "documentId": doc.id,
                        "service": doc.source.service,
                        "snapshotId": corpus.snapshot_id,
                        "kind": doc.kind,
                        "embeddingProfile": self.profile,
                        "title": doc.title,
                        "content": text,
                        "sourceJson": doc.source.model_dump_json(by_alias=True),
                        "payloadJson": canonical(doc.payload),
                        "operationId": doc.operation_id,
                        "tagsJson": canonical(doc.tags),
                        "embedding": vector,
                    }
                )
            result = self.client.merge_or_upload_documents(rows)
            if len(result) != len(rows) or any(not item.succeeded for item in result):
                raise ValueError("Index upload was incomplete; snapshot must not be activated")
            indexed += len(result)
        stats = self.index_client.get_index_statistics(self.settings.search_index)
        return {
            "indexedChunks": indexed,
            "sourceDocuments": len(corpus.documents),
            "storageBytes": stats.storage_size,
            "vectorIndexBytes": stats.vector_index_size,
            "note": "Azure storage statistics update asynchronously.",
        }

    def search(self, query: Query, limit: int = 12) -> list[Document]:
        try:
            return self._search(query, limit)
        except (AzureError, OpenAIError) as exc:
            raise DependencyUnavailableError() from exc

    def _search(self, query: Query, limit: int) -> list[Document]:
        def literal(value: str) -> str:
            return "'" + value.replace("'", "''") + "'"

        scope = (
            f"service eq {literal(query.service)} and "
            f"snapshotId eq {literal(query.snapshot_id)} and "
            f"embeddingProfile eq {literal(self.profile)}"
        )
        vector = self.embed([query.question])[0]
        results = self.client.search(
            query.question,
            filter=scope,
            top=min(limit * 3, 100),
            vector_queries=[
                VectorizedQuery(
                    vector=vector, k_nearest_neighbors=min(limit * 3, 100), fields="embedding"
                )
            ],
            vector_filter_mode="preFilter",
        )
        docs = []
        seen = set()
        for row in results:
            source = Source.model_validate_json(row["sourceJson"])
            if source.service != query.service or source.snapshot_id != query.snapshot_id:
                raise EvidenceVerificationError(
                    "Search returned evidence outside the selected snapshot"
                )
            document_id = row["documentId"]
            if document_id in seen:
                continue
            seen.add(document_id)
            doc = self.documents.get(document_id) or Document(
                id=document_id,
                source=source,
                kind=row["kind"],
                title=row["title"],
                content=row["content"],
                payload=json.loads(row["payloadJson"]),
                operation_id=row.get("operationId"),
                tags=json.loads(row["tagsJson"]),
            )
            docs.append(doc)
            if len(docs) >= limit:
                break
        return docs

    def close(self) -> None:
        self.client.close()
        self.index_client.close()
        self.openai.close()
        if hasattr(self.credential, "close"):
            self.credential.close()
