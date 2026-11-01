"""Pure index planning: bounded UTF-8 inputs, stable identities and compact search rows."""

from dataclasses import dataclass

from knowledge_assistant.domain.models import Corpus, Document

from ..configuration.settings import Settings
from ..ingestion.livedocs import canonical, digest

SCHEMA_VERSION = "knowledge-chunks-2"
INPUT_BYTES = 6000
OVERLAP_BYTES = 256
BATCH_SIZE = 16
MANIFEST_KIND = "__snapshot__"


def embedding_profile(settings: Settings) -> str:
    return digest(
        canonical(
            [
                settings.openai_endpoint.rstrip("/"),
                settings.embedding_deployment,
                settings.embedding_dimensions,
                settings.embedding_revision,
            ]
        ).encode()
    )


def document_hash(document: Document) -> str:
    return digest(canonical(document.model_dump(mode="json", by_alias=True)).encode())


def corpus_hash(corpus: Corpus) -> str:
    return digest(
        canonical(
            [
                SCHEMA_VERSION,
                INPUT_BYTES,
                OVERLAP_BYTES,
                corpus.model_dump(mode="json", by_alias=True),
            ]
        ).encode()
    )


def manifest_key(corpus_sha: str, profile: str) -> str:
    return "manifest-" + digest(f"{corpus_sha}:{profile}".encode())


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def scope(corpus_sha: str, profile: str) -> str:
    return f"corpusHash eq {literal(corpus_sha)} and embeddingProfile eq {literal(profile)}"


def chunk_text(title: str, content: str):
    header = (title + "\n").encode()
    if len(header) > 2000:
        raise ValueError("Document title exceeds the embedding input budget")
    raw, start = content.encode(), 0
    budget = INPUT_BYTES - len(header)
    while True:
        part = raw[start : start + budget].decode("utf-8", errors="ignore")
        end = start + len(part.encode())
        yield header.decode() + part
        if end >= len(raw):
            break
        start = max(start + 1, end - OVERLAP_BYTES)
        while start < len(raw) and raw[start] & 0xC0 == 0x80:
            start += 1


@dataclass
class IndexPlan:
    corpus: Corpus
    corpus_sha: str
    profile: str
    rows: list[dict]

    def summary(self, dimensions: int) -> dict:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "snapshotId": self.corpus.snapshot_id,
            "corpusHash": self.corpus_sha,
            "embeddingProfile": self.profile,
            "sourceDocuments": len(self.corpus.documents),
            "indexedChunks": len(self.rows),
            "uniqueEmbeddingInputs": len({r["content"] for r in self.rows}),
            "embeddingInputBytes": sum(len(r["content"].encode()) for r in self.rows),
            "rawFloat32VectorBytes": len(self.rows) * dimensions * 4,
            "note": "Vector bytes exclude Azure index overhead; they are not a capacity guarantee.",
        }


def plan_index(corpus: Corpus, settings: Settings) -> IndexPlan:
    if not corpus.documents or len({d.id for d in corpus.documents}) != len(corpus.documents):
        raise ValueError("Indexing requires a nonempty corpus with unique document IDs")
    sha, profile = corpus_hash(corpus), embedding_profile(settings)
    rows = []
    for doc in corpus.documents:
        if doc.source.snapshot_id != corpus.snapshot_id or not doc.source.service:
            raise ValueError("Document identity differs from the selected corpus")
        fingerprint = document_hash(doc)
        for part, text in enumerate(chunk_text(doc.title, doc.content)):
            rows.append(
                {
                    "id": "chunk-" + digest(f"{sha}:{profile}:{doc.id}:{part}".encode()),
                    "documentId": doc.id,
                    "documentHash": fingerprint,
                    "service": doc.source.service,
                    "snapshotId": corpus.snapshot_id,
                    "corpusHash": sha,
                    "embeddingProfile": profile,
                    "kind": doc.kind,
                    "title": doc.title,
                    "content": text,
                }
            )
    return IndexPlan(corpus, sha, profile, rows)
